"""Analyze Agent — Q&A, comparison, extraction, and summarisation.

Pipeline (per request):
  1. strip_conversational_prefix — remove greeting noise before retrieval
  2. _detect_task                — classify into qa / compare / extract / summary
  3. _enrich_search_query        — expand "этот документ" refs to real filenames
  4. _retrieve_and_rerank        — vector → BM25 → graph sections → cross-encoder
  5. _build_context_string       — assemble LLM-ready context with source headers
  6. _run_analysis_llm           — task-specific prompt + LLM call
     · compare / extract → JSON  (parse_with_retry + Pydantic schema, num_predict=2000)
     · qa / summary      → text  (get_llm, num_predict=2000)
  7. _parse_result               — validated dict for JSON tasks; pass-through text
  8. _format_output              — readable markdown with human-friendly headers
  9. _build_citations            — filename + page + rerank_score per hit
 10. analyze_node                — orchestrator; returns updated AgentState
"""
from __future__ import annotations

import logging
import re
import statistics as _statistics
import time
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import Settings, get_settings
from ..core.json_output import AnalyzeCompareResult, AnalyzeExtractResult, parse_with_retry
from ..core.llm import get_llm, invoke_with_retry
from ..core.utils import (
    DOC_REF_RE,
    build_final_response,
    build_history_messages,
    extract_hit_filename,
    extract_hit_page,
    extract_recent_filename,
    strip_conversational_prefix,
)
from ..graph.state import AgentState
from ..prompts import (
    ANALYZE_COMPARE,
    ANALYZE_EXTRACT,
    ANALYZE_QA,
    ANALYZE_SUMMARY,
)
from ..tools.bm25_search import bm25_search
from ..tools.neo4j_query import graph_section_search
from ..tools.qdrant_search import qdrant_search
from ..tools.reranker import reranker

logger = logging.getLogger(__name__)

# ── Graceful degradation message when Ollama is unreachable ──────────────────
_OLLAMA_UNAVAILABLE_MSG = (
    "К сожалению, языковая модель временно недоступна. "
    "Попробуйте повторить запрос через несколько минут."
)


def _score_stats(scores: list[float]) -> dict:
    """Return min/max/p50 for a list of scores. Returns empty dict if no scores."""
    if not scores:
        return {}
    return {
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "p50": round(_statistics.median(scores), 4),
    }


# ── Task classification ────────────────────────────────────────────────────────

TaskType = Literal["qa", "compare", "extract", "summary"]

_COMPARE_STEMS = re.compile(
    r"(?:^|(?<=\s))(?:сравн|сравнен|сопостав|отличи[ея]|разниц|различи[ея])",
    re.IGNORECASE | re.UNICODE,
)
_EXTRACT_STEMS = re.compile(
    r"(?:^|(?<=\s))(?:извлек|вытащ|выдел[иа]|найди\s+все|укажи\s+все|перечисл)",
    re.IGNORECASE | re.UNICODE,
)
_SUMMARY_STEMS = re.compile(
    r"(?:^|(?<=\s))(?:резюм|суммар|суммаризу|кратк(?:о|ое|ий)|краткое\s+содержани)",
    re.IGNORECASE | re.UNICODE,
)

# Friendly display names for each task type used in _format_output headers.
_TASK_LABELS: dict[TaskType, str] = {
    "qa": "Ответ",
    "compare": "Сравнительный анализ",
    "extract": "Извлечённые данные",
    "summary": "Резюме документа",
}

# Regex that extracts the two subjects of a compare query.
# Handles patterns like "сравни X и Y", "сравни X с Y", "отличие X от Y".
_COMPARE_PAIR_RE = re.compile(
    r"(?:сравн\w*|сопостав\w*|отличи[ея]\s+\w+\s+от)\s+(.+?)\s+(?:и|с|vs\.?)\s+(.+?)$",
    re.IGNORECASE | re.UNICODE,
)


# ── Stage 1 + 2: Task classification ──────────────────────────────────────────

def _detect_task(query: str) -> TaskType:
    """Return the analysis task type inferred from the user query."""
    q = query.lower()
    if _COMPARE_STEMS.search(q):
        return "compare"
    if _EXTRACT_STEMS.search(q):
        return "extract"
    if _SUMMARY_STEMS.search(q):
        return "summary"
    return "qa"


# ── Stage 3: Referential enrichment ──────────────────────────────────────────

def _enrich_search_query(query: str, state: AgentState) -> tuple[str, str | None]:
    """Expand referential phrases like 'этот документ' with the actual filename.

    Returns:
        (search_query, recent_filename)
    """
    if DOC_REF_RE.search(query):
        filename = extract_recent_filename(state)
        if filename:
            logger.info("analyze: referential query enriched with filename=%s", filename)
            return f"{filename} {query}", filename
    return query, None


# ── Stage 4a: Retrieve + rerank (single context) ──────────────────────────────

def _retrieve_and_rerank(
    search_query: str,
    lexical_query: str,
    limit: int,
    s: Settings,
) -> tuple[list[dict], float, bool, dict]:
    """Hybrid retrieval pipeline for analyze: vector → BM25 → graph → rerank.

    Unlike the search agent this is deliberately synchronous (no thread pool)
    because analyze tasks are already heavier LLM operations; the graph
    enrichment adds minimal extra latency compared to the total task time.

    Returns:
        (reranked_docs, best_rerank_score, has_usable_context, stage_counts)
    """
    # 1. Vector search — broad semantic recall
    vector_hits: list[dict] = qdrant_search.invoke({
        "query": search_query,
        "limit": limit,
    })

    if not vector_hits:
        return [], 0.0, False, {"vector_hits": 0, "bm25_hits": 0, "graph_hits": 0, "merged_hits": 0, "reranked_hits": 0, "rerank_score_stats": {}}

    # 2. BM25 over vector results — lifts lexically strong hits
    bm25_hits: list[dict] = bm25_search.invoke({
        "query": lexical_query,
        "documents": vector_hits,
        "top_k": limit,
    })

    # 3. Neo4j section enrichment — structured law/section text
    graph_hits: list[dict] = []
    try:
        kw = " ".join(
            w for w in re.findall(r"[а-яёА-ЯЁa-zA-Z0-9]+", lexical_query)
            if len(w) >= 3
        )[:80]
        sections = graph_section_search.invoke({"keywords": kw, "limit": 3})
        for sec in sections:
            text = (sec.get("text") or "")[:s.content_snippet_max_len]
            if text:
                graph_hits.append({
                    "id": sec.get("section_id") or f"sec:{hash(text)}",
                    "content": text,
                    "score": float(sec.get("score", 0.5)),
                    "section": f"стр. {sec.get('page', '?')} — {sec.get('source', '')}",
                    "metadata": {
                        "source": sec.get("source", ""),
                        "page": sec.get("page"),
                    },
                })
    except Exception as exc:
        logger.warning("analyze: graph section search failed: %s", exc)

    # 4. Merge BM25 + graph, dedup by ID
    seen_ids: set[str] = set()
    merged: list[dict] = []
    for hit in bm25_hits + graph_hits:
        hit_id = str(hit.get("id") or "")
        if hit_id and hit_id in seen_ids:
            continue
        if hit_id:
            seen_ids.add(hit_id)
        merged.append(hit)

    # 4b. Cosine pre-filter (mirrors search_agent _filter_by_relevance)
    filtered = [h for h in merged if h.get("score", 0) >= s.min_relevance_score]
    if not filtered and merged:
        logger.warning(
            "analyze: all %d hits below min_relevance_score=%.2f -- using all",
            len(merged), s.min_relevance_score,
        )
        filtered = merged
    merged = filtered

    # 5. Cross-encoder rerank over candidate pool
    candidates = sorted(
        merged, key=lambda d: float(d.get("score", 0)), reverse=True
    )[:s.rerank_candidate_pool]

    reranked: list[dict] = (
        reranker.invoke({
            "query": lexical_query,
            "documents": candidates,
            "top_k": s.rerank_top_k,
        })
        if candidates else []
    )

    best_score = reranked[0].get("rerank_score", 0.0) if reranked else 0.0
    has_context = bool(reranked) and best_score >= s.search_min_confidence

    logger.info(
        "analyze: retrieve_and_rerank hits=%d→bm25=%d→graph=%d→merged=%d "
        "→reranked=%d  best_score=%.3f",
        len(vector_hits), len(bm25_hits), len(graph_hits),
        len(merged), len(reranked), best_score,
    )
    stage_counts = {
        "vector_hits": len(vector_hits),
        "bm25_hits": len(bm25_hits),
        "graph_hits": len(graph_hits),
        "merged_hits": len(merged),
        "reranked_hits": len(reranked),
        "rerank_score_stats": _score_stats([h.get("rerank_score", 0) for h in reranked]),
    }
    return reranked, best_score, has_context, stage_counts


# ── Stage 4b: Compare — fetch two independent contexts ────────────────────────

def _extract_compare_subjects(query: str) -> tuple[str, str] | None:
    """Try to extract the two subjects of a compare query.

    Handles patterns like:
    - "сравни закон о банках и закон о финансировании"
    - "отличие залога от поручительства"
    - "сравни главу 2 с главой 3"

    Returns (subject_a, subject_b) or None if extraction fails.
    """
    m = _COMPARE_PAIR_RE.search(query)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        # Sanity-check: each subject should be at least 2 characters
        if len(a) >= 2 and len(b) >= 2:
            return a, b
    return None



# ── Stage 5: Context string assembly ──────────────────────────────────────────

def _build_context_string(
    hits: list[dict],
    label: str = "Контекст",
    s: Settings | None = None,
) -> str:
    """Build a numbered context string with source headers for the LLM.

    Each block starts with:
        [Источник N] Документ: <filename>, стр. <page>
        <content>

    This mirrors the search agent format so ANALYZE_QA can use the same
    source-citation convention.
    """
    if s is None:
        s = get_settings()
    parts: list[str] = []
    for i, d in enumerate(hits):
        filename = extract_hit_filename(d) or "—"
        page = extract_hit_page(d)
        page_suffix = f", стр. {page}" if page else ""
        content = d.get("content", "")[:s.content_snippet_max_len]
        parts.append(
            f"[Источник {i + 1}] Документ: {filename}{page_suffix}\n{content}"
        )
    return f"{label}:\n" + "\n\n".join(parts) if parts else f"{label}: нет данных"


def _build_compare_context(
    hits_a: list[dict],
    hits_b: list[dict],
    subjects: tuple[str, str] | None,
    s: Settings,
) -> str:
    """Build a two-sided context block for compare tasks."""
    label_a = f"Документ А ({subjects[0]})" if subjects else "Документ А"
    label_b = f"Документ Б ({subjects[1]})" if subjects else "Документ Б"
    ctx_a = _build_context_string(hits_a, label=label_a, s=s)
    ctx_b = _build_context_string(hits_b, label=label_b, s=s)
    return f"{ctx_a}\n\n{'─' * 60}\n\n{ctx_b}"


# ── Stage 6: LLM call ──────────────────────────────────────────────────────────

_NO_CONTEXT_RESPONSE = (
    "В загруженных документах не найдено достаточно релевантной информации "
    "по данному запросу. Попробуйте переформулировать запрос или загрузить "
    "нужный документ."
)

_TASK_SYSTEM_PROMPTS: dict[TaskType, str] = {
    "qa": ANALYZE_QA,
    "compare": ANALYZE_COMPARE,
    "extract": ANALYZE_EXTRACT,
    "summary": ANALYZE_SUMMARY,
}

# Tasks that return structured JSON (others return free text)
_JSON_TASKS: frozenset[TaskType] = frozenset({"compare", "extract"})

_JSON_TASK_SCHEMAS: dict[str, type] = {
    "compare": AnalyzeCompareResult,
    "extract": AnalyzeExtractResult,
}


def _run_analysis_llm(
    query: str,
    task: TaskType,
    context_str: str,
    recent_filename: str | None,
    state: AgentState,
    s: Settings,
) -> tuple[str | dict, bool]:
    """Call the LLM with the appropriate task prompt.

    For JSON tasks (compare, extract): uses parse_with_retry with schema constraint.
    For text tasks (qa, summary): uses get_llm, returns raw text.

    Returns:
        (raw_output_or_dict, json_success)
        - JSON tasks: (dict from model_dump or raw str, success bool)
        - Text tasks: (raw str, True) — no JSON parsing needed
    """
    system_prompt = _TASK_SYSTEM_PROMPTS[task]
    doc_hint = f"\nАнализируемый документ: {recent_filename}\n" if recent_filename else ""
    user_msg = (
        f"Запрос: {query}\n{doc_hint}\n"
        f"{context_str}"
    )

    history = build_history_messages(state, max_turns=s.history_turns)

    if task in _JSON_TASKS:
        schema = _JSON_TASK_SCHEMAS[task]
        messages = [
            SystemMessage(content=system_prompt),
            *history,
            HumanMessage(content=user_msg),
        ]
        result, success = parse_with_retry(messages, schema, num_predict=2000)
        if success and result is not None:
            return result.model_dump(), True
        # Return raw empty string on total failure — _parse_result will create fallback
        return "", False
    else:
        llm = get_llm(num_predict=2000)
        raw = invoke_with_retry(llm, [
            SystemMessage(content=system_prompt),
            *history,
            HumanMessage(content=user_msg),
        ])
        if not raw.strip():
            logger.error("analyze: LLM returned empty for task=%s (Ollama may be down)", task)
            raw = _OLLAMA_UNAVAILABLE_MSG
        return raw, True


# ── Stage 7: Result parsing ────────────────────────────────────────────────────

def _parse_result(raw: str | dict, task: TaskType, query: str, json_success: bool = True) -> dict:
    """Parse LLM output into a normalised dict.

    JSON tasks: if json_success=True, raw is already a validated dict.
    If json_success=False, build fallback dict with _parse_failed=True.
    Text tasks: wrap raw text in {"task": task, "result": raw}.
    """
    if task in _JSON_TASKS:
        if json_success and isinstance(raw, dict):
            return raw

        # Parse failure fallback
        raw_str = raw if isinstance(raw, str) else ""
        return {
            "task": task,
            "result": raw_str,
            "similarities": [],
            "differences": [],
            "entities": [],
            "key_points": [],
            "recommendations": [],
            "legal_conflicts": [],
            "confidence": 0.5,
            "_parse_failed": True,
        }

    # Free-text tasks: no JSON expected
    raw_str = raw if isinstance(raw, str) else str(raw)
    return {
        "task": task,
        "result": raw_str.strip(),
        "confidence": 0.9 if raw_str.strip() else 0.0,
    }


# ── Stage 8: Output formatting ────────────────────────────────────────────────

def _format_output(result: dict, task: TaskType) -> str:
    """Render the parsed result as human-readable markdown.

    Uses friendly section headers, not technical task names.
    """
    label = _TASK_LABELS.get(task, "Анализ")
    parts = [f"**{label}:**\n\n{result.get('result', '')}"]

    if result.get("key_points"):
        parts.append(
            "**Ключевые тезисы:**\n"
            + "\n".join(f"- {p}" for p in result["key_points"])
        )
    if result.get("similarities"):
        parts.append(
            "**Сходства:**\n"
            + "\n".join(f"- {s}" for s in result["similarities"])
        )
    if result.get("differences"):
        parts.append(
            "**Различия:**\n"
            + "\n".join(f"- {d}" for d in result["differences"])
        )
    if result.get("legal_conflicts"):
        parts.append(
            "**Правовые противоречия:**\n"
            + "\n".join(f"- {c}" for c in result["legal_conflicts"])
        )
    if result.get("entities"):
        entity_str = ", ".join(
            f"{e.get('value', e.get('name', ''))} [{e.get('type', '')}]"
            + (f" — {e['source']}" if e.get("source") else "")
            for e in result["entities"]
            if e.get("value") or e.get("name")
        )
        if entity_str:
            parts.append(f"**Сущности:** {entity_str}")
    if result.get("recommendations"):
        parts.append(
            "**Рекомендации:**\n"
            + "\n".join(f"- {r}" for r in result["recommendations"])
        )

    confidence = result.get("confidence", 0.5)
    parts.append(f"*Уверенность: {confidence:.0%}*")
    return "\n\n".join(parts)


# ── Stage 9: Citations ────────────────────────────────────────────────────────

def _build_citations(hits: list[dict], s: Settings) -> list[dict]:
    """Build structured citation records from reranked hits."""
    return [
        {
            "index": i + 1,
            "content_preview": d.get("content", "")[:s.citation_preview_max_len],
            "filename": extract_hit_filename(d),
            "page_number": extract_hit_page(d),
            "section": d.get("section", ""),
            "score": d.get("rerank_score", d.get("score", 0.0)),
        }
        for i, d in enumerate(hits[:s.rerank_top_k])
    ]


# ── Graph entry point ─────────────────────────────────────────────────────────

def analyze_node(state: AgentState) -> AgentState:
    """Analysis node: classify task → retrieve → rerank → LLM → format."""
    t_start = time.perf_counter()
    s = get_settings()
    raw_query = state["user_query"]

    # 1. Strip greeting noise (same as search agent)
    query = strip_conversational_prefix(raw_query)

    # 2. Detect task
    task = _detect_task(query)

    # 3. Referential enrichment
    search_query, recent_filename = _enrich_search_query(query, state)

    # ── 4. Retrieve depending on task ────────────────────────────────────────
    all_hits: list[dict] = []
    has_context = False
    best_score = 0.0
    context_str = ""
    stage_counts: dict = {}

    if task == "compare":
        # Rerank each side independently so the cross-encoder sees the right query
        subjects = _extract_compare_subjects(query)
        query_a = subjects[0] if subjects else query
        query_b = subjects[1] if subjects else query
        reranked_a, score_a, ok_a, counts_a = _retrieve_and_rerank(query_a, query_a, s.analyze_compare_limit, s)
        reranked_b, score_b, ok_b, counts_b = _retrieve_and_rerank(query_b, query_b, s.analyze_compare_limit, s)

        logger.info(
            "analyze: compare side_a hits=%d best_score=%.3f | side_b hits=%d best_score=%.3f",
            len(reranked_a), score_a, len(reranked_b), score_b,
        )

        has_context = ok_a or ok_b
        best_score = max(score_a, score_b)
        all_hits = reranked_a + reranked_b

        stage_counts = {
            "vector_hits": counts_a.get("vector_hits", 0) + counts_b.get("vector_hits", 0),
            "bm25_hits": counts_a.get("bm25_hits", 0) + counts_b.get("bm25_hits", 0),
            "graph_hits": counts_a.get("graph_hits", 0) + counts_b.get("graph_hits", 0),
            "merged_hits": counts_a.get("merged_hits", 0) + counts_b.get("merged_hits", 0),
            "reranked_hits": counts_a.get("reranked_hits", 0) + counts_b.get("reranked_hits", 0),
            "rerank_score_stats": _score_stats(
                [h.get("rerank_score", 0) for h in reranked_a + reranked_b]
            ),
        }

        context_str = _build_compare_context(reranked_a, reranked_b, subjects, s)

    elif task == "summary":
        # Summary needs broad coverage — use a larger hit limit
        reranked, best_score, has_context, stage_counts = _retrieve_and_rerank(
            search_query, query, s.analyze_summary_limit, s
        )
        all_hits = reranked
        context_str = _build_context_string(reranked, s=s)

    else:
        # qa / extract — standard retrieve
        reranked, best_score, has_context, stage_counts = _retrieve_and_rerank(
            search_query, query, s.rerank_top_k * 2, s
        )
        all_hits = reranked
        context_str = _build_context_string(reranked, s=s)

    # ── 5. No-context fallback — honest "not found" instead of hallucination ─
    if not has_context:
        logger.warning(
            "analyze_node: no usable context (best_score=%.3f, task=%s, query=%r)",
            best_score, task, query[:80],
        )
        elapsed = time.perf_counter() - t_start
        final_response = build_final_response(
            _NO_CONTEXT_RESPONSE, state.get("combined_responses") or []
        )
        return {
            **state,
            "analyze_result": {"task": task, "result": _NO_CONTEXT_RESPONSE, "confidence": 0.0},
            "citations": [],
            "final_response": final_response,
            "retrieval_metrics": {
                "node": "analyze",
                "intent": state.get("intent", ""),
                "tier": state.get("tier", ""),
                "task": task,
                "vector_hits": stage_counts.get("vector_hits", 0),
                "bm25_hits": stage_counts.get("bm25_hits", 0),
                "graph_hits": stage_counts.get("graph_hits", 0),
                "merged_hits": stage_counts.get("merged_hits", 0),
                "reranked_hits": stage_counts.get("reranked_hits", 0),
                "best_rerank_score": round(best_score, 4),
                "rerank_score_stats": stage_counts.get("rerank_score_stats", {}),
                "has_context": False,
                "json_parse_success": None,
                "elapsed_s": round(elapsed, 2),
            },
            "messages": state["messages"] + [AIMessage(content=final_response)],
        }

    # ── 6. LLM call ──────────────────────────────────────────────────────────
    raw, json_success = _run_analysis_llm(query, task, context_str, recent_filename, state, s)

    # ── 7–8. Parse + format ───────────────────────────────────────────────────
    analyze_result = _parse_result(raw, task, query, json_success=json_success)
    summary = _format_output(analyze_result, task)

    # ── 9. Citations ──────────────────────────────────────────────────────────
    citations = _build_citations(all_hits, s)

    final_response = build_final_response(summary, state.get("combined_responses") or [])
    elapsed = time.perf_counter() - t_start

    logger.info(
        "analyze_node: task=%s hits=%d best_score=%.3f confidence=%.2f elapsed=%.2fs",
        task, len(all_hits), best_score,
        analyze_result.get("confidence", 0.0), elapsed,
    )

    # json_parse_success: only meaningful for JSON tasks (compare, extract)
    _json_parse_success: bool | None = None
    if task in _JSON_TASKS:
        _json_parse_success = not analyze_result.get("_parse_failed", False)

    return {
        **state,
        "analyze_result": analyze_result,
        "citations": citations,
        "final_response": final_response,
        "retrieval_metrics": {
            "node": "analyze",
            "intent": state.get("intent", ""),
            "tier": state.get("tier", ""),
            "task": task,
            "vector_hits": stage_counts.get("vector_hits", 0),
            "bm25_hits": stage_counts.get("bm25_hits", 0),
            "graph_hits": stage_counts.get("graph_hits", 0),
            "merged_hits": stage_counts.get("merged_hits", 0),
            "reranked_hits": stage_counts.get("reranked_hits", 0),
            "best_rerank_score": round(best_score, 4),
            "rerank_score_stats": stage_counts.get("rerank_score_stats", {}),
            "has_context": has_context,
            "json_parse_success": _json_parse_success,
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
