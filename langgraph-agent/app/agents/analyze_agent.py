"""Analyze Agent — Q&A, comparison, extraction, and summarisation.

Refactored to use the unified retrieval module.

Pipeline (per request):
  1. strip_conversational_prefix — remove greeting noise before retrieval
  2. _detect_task                — classify into qa / compare / extract / summary
  3. _enrich_search_query        — expand "этот документ" refs to real filenames
  4. retrieve()                  — unified retrieval (point / scoped / document)
  5. _build_context_string       — assemble LLM-ready context with source headers
  6. _run_analysis_llm           — task-specific prompt + LLM call
  7. _parse_result               — validated dict for JSON tasks; pass-through text
  8. _format_output              — readable markdown with human-friendly headers
  9. _build_citations            — filename + page + rerank_score per hit
 10. analyze_node                — orchestrator; returns updated AgentState

Key changes:
  - Uses unified ``retrieve()`` instead of inline retrieval pipeline.
  - SCOPED mode for document-specific queries (top-20 from correct document).
  - DOCUMENT mode for summary/extract — Map-Reduce over all chunks.
  - Compare uses two SCOPED retrievals when doc_ids available.
  - Same retrieval quality as search_agent (corpus BM25 + full Neo4j in point mode).
"""
from __future__ import annotations

import logging
import re
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
from ..tools.retrieval import retrieve

logger = logging.getLogger(__name__)

_OLLAMA_UNAVAILABLE_MSG = (
    "К сожалению, языковая модель временно недоступна. "
    "Попробуйте повторить запрос через несколько минут."
)


# ── Task classification ──────────────────────────────────────────────────────

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

_TASK_LABELS: dict[TaskType, str] = {
    "qa": "Ответ",
    "compare": "Сравнительный анализ",
    "extract": "Извлечённые данные",
    "summary": "Резюме документа",
}

_COMPARE_PAIR_RE = re.compile(
    r"(?:сравн\w*|сопостав\w*|отличи[ея]\s+\w+\s+от)\s+(.+?)\s+(?:и|с|vs\.?)\s+(.+?)$",
    re.IGNORECASE | re.UNICODE,
)


def _detect_task(query: str) -> TaskType:
    q = query.lower()
    if _COMPARE_STEMS.search(q):
        return "compare"
    if _EXTRACT_STEMS.search(q):
        return "extract"
    if _SUMMARY_STEMS.search(q):
        return "summary"
    return "qa"


# ── Referential enrichment ───────────────────────────────────────────────────

def _enrich_search_query(query: str, state: AgentState) -> tuple[str, str | None]:
    if DOC_REF_RE.search(query):
        filename = extract_recent_filename(state)
        if filename:
            logger.info("analyze: referential query enriched with filename=%s", filename)
            return f"{filename} {query}", filename
    return query, None


def _extract_compare_subjects(query: str) -> tuple[str, str] | None:
    m = _COMPARE_PAIR_RE.search(query)
    if m:
        a = m.group(1).strip()
        b = m.group(2).strip()
        if len(a) >= 2 and len(b) >= 2:
            return a, b
    return None


# ── Context string assembly ──────────────────────────────────────────────────

def _build_context_string(
    hits: list[dict],
    label: str = "Контекст",
    s: Settings | None = None,
) -> str:
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
    label_a = f"Документ А ({subjects[0]})" if subjects else "Документ А"
    label_b = f"Документ Б ({subjects[1]})" if subjects else "Документ Б"
    ctx_a = _build_context_string(hits_a, label=label_a, s=s)
    ctx_b = _build_context_string(hits_b, label=label_b, s=s)
    return f"{ctx_a}\n\n{'─' * 60}\n\n{ctx_b}"


# ── LLM call ─────────────────────────────────────────────────────────────────

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
    system_prompt = _TASK_SYSTEM_PROMPTS[task]
    doc_hint = f"\nАнализируемый документ: {recent_filename}\n" if recent_filename else ""
    user_msg = f"Запрос: {query}\n{doc_hint}\n{context_str}"
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
        return "", False
    else:
        llm = get_llm(num_predict=2000)
        raw = invoke_with_retry(llm, [
            SystemMessage(content=system_prompt),
            *history,
            HumanMessage(content=user_msg),
        ])
        if not raw.strip():
            logger.error("analyze: LLM returned empty for task=%s", task)
            raw = _OLLAMA_UNAVAILABLE_MSG
        return raw, True


# ── Result parsing ───────────────────────────────────────────────────────────

def _parse_result(raw: str | dict, task: TaskType, query: str, json_success: bool = True) -> dict:
    if task in _JSON_TASKS:
        if json_success and isinstance(raw, dict):
            return raw
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
    raw_str = raw if isinstance(raw, str) else str(raw)
    return {
        "task": task,
        "result": raw_str.strip(),
        "confidence": 0.9 if raw_str.strip() else 0.0,
    }


# ── Output formatting ───────────────────────────────────────────────────────

def _format_output(result: dict, task: TaskType) -> str:
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


# ── Citations ────────────────────────────────────────────────────────────────

def _build_citations(hits: list[dict], s: Settings) -> list[dict]:
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


# ── Graph entry point ───────────────────────────────────────────────────────

def analyze_node(state: AgentState) -> AgentState:
    """Analysis node: classify task → unified retrieve → LLM → format."""
    t_start = time.perf_counter()
    s = get_settings()
    raw_query = state["user_query"]

    # 1. Strip greeting noise
    query = strip_conversational_prefix(raw_query)

    # 2. Detect task
    task = _detect_task(query)

    # 3. Referential enrichment
    search_query, recent_filename = _enrich_search_query(query, state)

    # Scope and doc_id from supervisor
    scope = state.get("scope", "point")
    doc_ids = state.get("document_ids") or []
    doc_id = doc_ids[0] if doc_ids else None

    # ── 4. Retrieve depending on task + scope ────────────────────────────────
    all_hits: list[dict] = []
    has_context = False
    best_score = 0.0
    context_str = ""
    retrieval_metrics: dict = {}

    if task == "compare":
        # Compare: two independent retrievals
        subjects = _extract_compare_subjects(query)
        query_a = subjects[0] if subjects else query
        query_b = subjects[1] if subjects else query

        # Use scoped if doc_ids available, point otherwise.
        # When only one doc_id: side_a = scoped (search within the document),
        # side_b = point (search entire collection) to avoid comparing
        # the same document against itself.
        result_a = retrieve(query_a, scope=scope, doc_id=doc_id, top_k=15)
        if len(doc_ids) > 1:
            doc_id_b = doc_ids[1]
            scope_b = scope
        else:
            doc_id_b = doc_id
            scope_b = "point" if subjects else scope
        result_b = retrieve(query_b, scope=scope_b, doc_id=doc_id_b, top_k=15)

        has_context = result_a.has_context or result_b.has_context
        best_score = max(result_a.best_score, result_b.best_score)
        all_hits = result_a.chunks + result_b.chunks
        context_str = _build_compare_context(result_a.chunks, result_b.chunks, subjects, s)

        retrieval_metrics = {
            "side_a": result_a.metrics,
            "side_b": result_b.metrics,
            "scope": scope,
        }

    elif task == "summary" and scope == "document" and doc_id:
        # Map-Reduce summary over entire document
        result = retrieve(
            search_query, scope="document", doc_id=doc_id, task_type="summary",
        )
        all_hits = result.chunks
        has_context = result.has_context
        best_score = result.best_score
        # Map-Reduce returns pre-processed content — use it directly
        context_str = _build_context_string(result.chunks, s=s)
        retrieval_metrics = result.metrics

    elif task == "extract" and scope == "document" and doc_id:
        # Map-Reduce extraction over entire document
        result = retrieve(
            search_query, scope="document", doc_id=doc_id, task_type="extract",
        )
        all_hits = result.chunks
        has_context = result.has_context
        best_score = result.best_score
        context_str = _build_context_string(result.chunks, s=s)
        retrieval_metrics = result.metrics

    else:
        # qa / extract / summary — standard retrieval (scoped or point)
        # In scoped mode: top-20 from the correct document
        # In point mode: top-7 from entire collection (same as search_agent)
        result = retrieve(search_query, scope=scope, doc_id=doc_id)
        all_hits = result.chunks
        has_context = result.has_context
        best_score = result.best_score
        context_str = _build_context_string(result.chunks, s=s)
        retrieval_metrics = result.metrics

    # ── 5. No-context fallback ───────────────────────────────────────────────
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
                "scope": scope,
                "task": task,
                **retrieval_metrics,
                "best_rerank_score": round(best_score, 4),
                "has_context": False,
                "elapsed_s": round(elapsed, 2),
            },
            "messages": state["messages"] + [AIMessage(content=final_response)],
        }

    # ── 6. LLM call ─────────────────────────────────────────────────────────
    raw, json_success = _run_analysis_llm(query, task, context_str, recent_filename, state, s)

    # ── 7–8. Parse + format ──────────────────────────────────────────────────
    analyze_result = _parse_result(raw, task, query, json_success=json_success)
    summary = _format_output(analyze_result, task)

    # ── 9. Citations ─────────────────────────────────────────────────────────
    citations = _build_citations(all_hits, s)

    final_response = build_final_response(summary, state.get("combined_responses") or [])
    elapsed = time.perf_counter() - t_start

    logger.info(
        "analyze_node: task=%s scope=%s hits=%d best_score=%.3f confidence=%.2f elapsed=%.2fs",
        task, scope, len(all_hits), best_score,
        analyze_result.get("confidence", 0.0), elapsed,
    )

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
            "scope": scope,
            "task": task,
            **retrieval_metrics,
            "best_rerank_score": round(best_score, 4),
            "has_context": has_context,
            "json_parse_success": _json_parse_success,
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
