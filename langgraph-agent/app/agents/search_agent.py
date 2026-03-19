"""Search Agent — hybrid RAG pipeline orchestrator.

Refactored to use the unified retrieval module. The search agent is now a
thin orchestration layer:
  1. _prepare_query       — referential enrichment
  2. _detect_exact_search — quoted / prefix-based exact match detection
  3. _retrieve_exact      — Qdrant MatchText search for literal strings
  4. retrieve()           — unified retrieval (point / scoped / document)
  5. _build_llm_prompt    — assemble context string for the LLM call
  6. _generate_answer     — LLM generation with history
  7. _build_citations     — format structured citation list
  8. search_node          — orchestrator: wires the pipeline, returns state

Key changes:
  - Retrieval logic moved to ``app.tools.retrieval`` (Single Responsibility).
  - BM25-over-vector-hits removed (returned same IDs; cross-encoder re-sorts).
  - Cosine pre-filter before cross-encoder removed (incomparable score scales).
  - Supports SCOPED mode: search within a single document (top-20 instead of top-7).
  - Hallucination guard simplified: better context = fewer hallucinations.
"""
from __future__ import annotations

import logging
import re
import statistics as _statistics
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import Settings, get_settings
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
from ..prompts import SEARCH_EXPERT
from ..tools.qdrant_search import qdrant_text_search
from ..tools.retrieval import retrieve

logger = logging.getLogger(__name__)

# ── Hallucination guard ──────────────────────────────────────────────────────
_HALLUCINATION_STUBS = re.compile(
    r"(?:у меня нет (?:информации|данных)"
    r"|не располагаю информацией"
    r"|не (?:могу|удалось) найти (?:информацию|данные|ответ)"
    r"|в (?:предоставленных|загруженных) документах не найдено"
    r"|в базе знаний не найдено"
    r"|к сожалению.{0,30}(?:нет информации|не найдено))",
    re.IGNORECASE | re.UNICODE,
)

_OLLAMA_UNAVAILABLE_MSG = (
    "К сожалению, языковая модель временно недоступна. "
    "Найденные документы доступны в цитатах ниже. "
    "Попробуйте повторить запрос через несколько минут."
)

# ── Exact search patterns ────────────────────────────────────────────────────
_QUOTED_TEXT_RE = re.compile(
    r'["\u00ab\u201c\u2018](.{8,}?)["\u00bb\u201d\u2019]', re.UNICODE | re.DOTALL
)
_EXACT_SEARCH_KW = frozenset([
    "найди строку", "найди фрагмент", "найди текст", "найди цитату",
    "найди точный", "найди дословно", "есть ли строка", "содержит строку",
    "в каком документе", "в каком файле", "какой документ содержит",
])
_EXACT_SEARCH_PREFIXES: tuple[str, ...] = (
    "в каком документе находится этот абзац ",
    "в каком документе находится этот текст ",
    "в каком документе находится ",
    "в каком документе есть ",
    "в каком файле находится ",
    "какой документ содержит ",
    "найди строку ", "найди фрагмент ", "найди текст ", "найди цитату ",
    "найди точный ", "найди дословно ", "есть ли строка ", "содержит строку ",
    "в каком документе ", "в каком файле ",
)


# ── Score statistics helper ──────────────────────────────────────────────────

def _score_stats(scores: list[float]) -> dict:
    if not scores:
        return {}
    return {
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "p50": round(_statistics.median(scores), 4),
    }


# ── Stage 1: Query preparation ──────────────────────────────────────────────

def _prepare_query(query: str, state: AgentState) -> str:
    """Strip conversational prefix and enrich referential queries."""
    query = strip_conversational_prefix(query)
    if DOC_REF_RE.search(query):
        filename = extract_recent_filename(state)
        if filename:
            query = f"{filename} {query}"
            logger.info("search: referential query enriched with filename=%s", filename)
    return query


# ── Stage 2-3: Exact-string search ──────────────────────────────────────────

def _detect_exact_search(query: str) -> tuple[bool, str]:
    query_lower = query.lower()
    quoted = _QUOTED_TEXT_RE.search(query)
    is_exact = bool(quoted or any(kw in query_lower for kw in _EXACT_SEARCH_KW))
    if not is_exact:
        return False, ""
    if quoted:
        return True, quoted.group(1).strip()
    search_text = query
    for prefix in _EXACT_SEARCH_PREFIXES:
        if query_lower.startswith(prefix):
            search_text = query[len(prefix):].strip()
            break
    return True, search_text


def _retrieve_exact(exact_text: str, s: Settings) -> list[dict]:
    if not exact_text:
        return []
    try:
        hits = qdrant_text_search.invoke({
            "text": exact_text,
            "collection": s.qdrant_collection,
            "limit": 8,
        })
        logger.info("exact_search '%s' -> %d hits", exact_text[:60], len(hits))
        return hits
    except Exception as exc:
        logger.warning("exact_search failed: %s", exc)
        return []


# ── Stage 4: LLM prompt assembly ────────────────────────────────────────────

def _build_llm_prompt(
    query: str,
    is_exact: bool,
    exact_text: str,
    exact_hits: list[dict],
    exact_hit_ids: set[str],
    reranked: list[dict],
    has_context: bool,
    best_score: float,
    s: Settings,
) -> str:
    def _ctx_block(i: int, d: dict) -> str:
        filename = extract_hit_filename(d) or "—"
        page = extract_hit_page(d)
        page_suffix = f", стр. {page}" if page else ""
        limit = 1200 if str(d.get("id", "")) in exact_hit_ids else s.content_snippet_max_len
        content = d.get("content", "")[:limit]
        return f"[Источник {i + 1}] Документ: {filename}{page_suffix}\n{content}"

    if is_exact and exact_hits:
        exact_blocks = "\n\n".join(
            "─" * 60 + f"\n[Совпадение {i + 1}]\n"
            f"Документ: {extract_hit_filename(h) or '—'}\n"
            f"Страница: {extract_hit_page(h) or '?'}\n"
            f"Секция: {h.get('section', '—')}\n"
            f"Найденный фрагмент:\n{h.get('content', '')[:1000]}"
            for i, h in enumerate(exact_hits[:5])
        )
        return (
            f"Пользователь ищет точный фрагмент текста в базе документов.\n\n"
            f"ИСКОМЫЙ ФРАГМЕНТ:\n{exact_text[:600]}\n\n"
            f"НАЙДЕННЫЕ СОВПАДЕНИЯ:\n{exact_blocks}\n\n"
            "Укажи точно: в каком документе найден фрагмент, на какой странице, в какой секции. "
            "Процитируй первые 2-3 строки найденного фрагмента из источника."
        )

    if has_context:
        context = "\n\n".join(_ctx_block(i, d) for i, d in enumerate(reranked))
        return f"Контекст:\n{context}\n\nВопрос: {query}"

    logger.warning(
        "search: low confidence (best_score=%.3f, docs=%d) for query=%r",
        best_score, len(reranked), query,
    )
    return (
        f"Вопрос: {query}\n\n"
        "ВАЖНО: Релевантные документы в базе знаний не найдены (или уровень уверенности "
        "слишком низкий). Сообщи об этом пользователю прямо и честно. "
        "Не придумывай информацию. Предложи уточнить запрос или загрузить нужный документ."
    )


# ── Stage 5: LLM answer generation ──────────────────────────────────────────

def _generate_answer(user_msg: str, state: AgentState, s: Settings) -> str:
    llm = get_llm()
    history = build_history_messages(state, max_turns=s.history_turns)
    return invoke_with_retry(llm, [
        SystemMessage(content=SEARCH_EXPERT),
        *history,
        HumanMessage(content=user_msg),
    ])


# ── Stage 6: Citation builder ───────────────────────────────────────────────

def _build_citations(
    reranked: list[dict], exact_hit_ids: set[str], s: Settings,
) -> list[dict]:
    return [
        {
            "index": i + 1,
            "content_preview": d.get("content", "")[:s.citation_preview_max_len],
            "match_content": d.get("content", "") if str(d.get("id", "")) in exact_hit_ids else "",
            "is_exact_match": str(d.get("id", "")) in exact_hit_ids,
            "page_number": extract_hit_page(d),
            "section": d.get("section", ""),
            "score": d.get("rerank_score", d.get("score", 0)),
            "filename": extract_hit_filename(d),
            "metadata": d.get("metadata", {}),
        }
        for i, d in enumerate(reranked)
    ]


# ── Graph entry point ───────────────────────────────────────────────────────

def search_node(state: AgentState) -> AgentState:
    """Hybrid RAG pipeline node: unified retrieval → generate → cite."""
    t_start = time.perf_counter()
    s = get_settings()
    query = state["user_query"]

    # 1. Prepare query
    query = _prepare_query(query, state)

    # 2-3. Exact-string search (when requested)
    is_exact, exact_text = _detect_exact_search(query)
    exact_hits = _retrieve_exact(exact_text, s) if is_exact else []
    exact_hit_ids = {str(h.get("id")) for h in exact_hits if h.get("id")}

    # 4. Unified retrieval (scope-aware)
    scope = state.get("scope", "point")
    doc_ids = state.get("document_ids") or []
    doc_id = doc_ids[0] if doc_ids else None

    result = retrieve(
        query=query,
        scope=scope,
        doc_id=doc_id,
    )

    reranked = result.chunks
    best_score = result.best_score
    has_context = result.has_context

    # 5. Prompt + answer
    user_msg = _build_llm_prompt(
        query, is_exact, exact_text, exact_hits, exact_hit_ids,
        reranked, has_context, best_score, s,
    )
    answer = _generate_answer(user_msg, state, s)

    # Hallucination guard: retry once if LLM stubs despite having context
    if has_context and answer.strip() and _HALLUCINATION_STUBS.search(answer):
        logger.warning("search_node: hallucination stub detected, retrying")
        retry_prompt = (
            f"{user_msg}\n\n"
            "ВАЖНО: Ответь на основе предоставленного контекста. "
            "Документы содержат релевантную информацию — используй её."
        )
        answer = _generate_answer(retry_prompt, state, s)

    if not answer.strip():
        logger.error("search_node: LLM returned empty response")
        answer = _OLLAMA_UNAVAILABLE_MSG

    # 6. Citations
    citations = _build_citations(reranked, exact_hit_ids, s)

    final_response = build_final_response(answer, state.get("combined_responses") or [])

    elapsed = time.perf_counter() - t_start
    metrics = {
        "node": "search",
        "intent": state.get("intent", ""),
        "tier": state.get("tier", ""),
        "scope": scope,
        "query_len": len(query),
        **result.metrics,
        "reranked_hits": len(reranked),
        "rerank_score_stats": _score_stats([h.get("rerank_score", 0) for h in reranked]),
        "best_rerank_score": round(best_score, 4),
        "has_context": has_context,
        "is_exact_search": is_exact,
        "elapsed_s": round(elapsed, 2),
    }
    if answer == _OLLAMA_UNAVAILABLE_MSG:
        metrics["degraded"] = True
    logger.info("search_node metrics: %s", metrics)

    return {
        **state,
        "reranked_docs": reranked,
        "search_result": answer,
        "citations": citations,
        "final_response": final_response,
        "retrieval_metrics": metrics,
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
