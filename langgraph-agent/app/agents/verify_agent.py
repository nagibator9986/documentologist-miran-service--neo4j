"""Verify Agent — compliance check and risk scoring.

Pipeline:
  1. _fetch_document_content — resolve what to verify (IDs / pasted text / search)
  2. _fetch_legal_context    — gather applicable norms from Qdrant + Neo4j
  3. _run_compliance_llm     — LLM JSON generation
  4. _parse_verify_result    — validate and normalise the JSON payload
  5. _format_summary         — produce human-readable summary string
  6. verify_node             — orchestrator
"""
from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_json_llm, invoke_with_retry
from ..core.utils import build_final_response, build_history_messages, safe_parse_json
from ..graph.state import AgentState
from ..prompts import VERIFY_COMPLIANCE
from ..tools.neo4j_query import graph_obligation_search, graph_section_search
from ..tools.qdrant_search import qdrant_search

logger = logging.getLogger(__name__)


# ── Stage 1: Document content resolution ─────────────────────────────────────

def _fetch_document_content(state: AgentState, query: str) -> str:
    """Resolve what text to verify, in priority order:

    1. Explicit document_ids → fetch chunks from Qdrant by payload filter.
    2. Long query (> verify_pasted_doc_threshold chars) → treat as pasted doc.
    3. Fallback → search Qdrant for the most relevant chunks.
    """
    s = get_settings()

    doc_ids = state.get("document_ids") or []
    if doc_ids:
        from ..core.utils import get_qdrant_client
        from qdrant_client import models as qmodels
        client = get_qdrant_client()
        try:
            chunks: list[str] = []
            for doc_id in doc_ids[:3]:
                records, _ = client.scroll(
                    collection_name=s.qdrant_collection,
                    scroll_filter=qmodels.Filter(
                        must=[qmodels.FieldCondition(
                            key="meta_json",
                            match=qmodels.MatchText(text=doc_id),
                        )]
                    ),
                    limit=10,
                    with_payload=True,
                    with_vectors=False,
                )
                for r in records:
                    payload = r.payload or {}
                    text = payload.get("answer") or payload.get("text", "")
                    if text:
                        chunks.append(text[:s.content_snippet_max_len])
            if chunks:
                logger.info(
                    "verify: fetched %d chunks for document_ids=%s", len(chunks), doc_ids
                )
                return "\n\n".join(chunks)
        except Exception as exc:
            logger.warning("verify: failed to fetch document_ids from Qdrant: %s", exc)

    if len(query) > s.verify_pasted_doc_threshold:
        logger.info("verify: using long query (%d chars) as document text", len(query))
        return query

    hits = qdrant_search.invoke({"query": query, "limit": 5})
    if hits:
        return "\n\n".join(
            f"[{i + 1}] {h.get('content', '')[:s.content_snippet_max_len]}"
            for i, h in enumerate(hits)
        )

    return query


# ── Stage 2: Legal context aggregation ───────────────────────────────────────

def _fetch_legal_context(query: str) -> tuple[str, str, str]:
    """Gather applicable norms from Qdrant and Neo4j.

    Returns:
        (legal_context, graph_context, obl_context) — all formatted as strings.
    """
    legal_hits = qdrant_search.invoke({"query": query, "limit": 5})
    legal_context = "\n".join(
        f"- {h.get('content', '')[:400]}" for h in legal_hits
    )

    graph_parts: list[str] = []
    try:
        sections = graph_section_search.invoke({"keywords": query[:60], "limit": 3})
        for sec in sections:
            text = (sec.get("text") or "")[:500]
            articles = sec.get("articles") or []
            refs = ", ".join(
                f"Статья {a.get('number')}" + (f" «{a.get('title')}»" if a.get("title") else "")
                for a in articles if a.get("number")
            )
            entry = text + (f"\n[{refs}]" if refs else "")
            graph_parts.append(entry)
    except Exception as exc:
        logger.warning("verify: graph section search failed: %s", exc)

    obl_parts: list[str] = []
    try:
        obls = graph_obligation_search.invoke({"keywords": query[:60], "limit": 4})
        for obl in obls:
            line = " — ".join(
                p for p in [obl.get("subject"), obl.get("action"), obl.get("object")] if p
            )
            if obl.get("deadline"):
                line += f" (срок: {obl['deadline']})"
            if line:
                obl_parts.append(line)
    except Exception as exc:
        logger.warning("verify: obligation search failed: %s", exc)

    graph_context = "\n\n".join(graph_parts) or "нет данных"
    obl_context = "\n".join(f"- {o}" for o in obl_parts) or "нет данных"
    return legal_context, graph_context, obl_context


# ── Stage 3: LLM compliance analysis ─────────────────────────────────────────

def _run_compliance_llm(
    doc_content: str,
    legal_context: str,
    graph_context: str,
    obl_context: str,
    state: AgentState,
) -> str:
    """Call LLM to produce compliance JSON."""
    s = get_settings()
    doc_label = (
        "Содержимое документа" if len(doc_content) > s.verify_pasted_doc_threshold
        else "Запрос/фрагмент"
    )
    prompt = (
        f"{doc_label}:\n{doc_content}\n\n"
        f"Применимые нормы (векторный поиск):\n{legal_context}\n\n"
        f"Релевантные статьи из графа знаний:\n{graph_context}\n\n"
        f"Выявленные обязательства из графа:\n{obl_context}"
    )
    llm = get_json_llm(num_predict=1024)
    history = build_history_messages(state, max_turns=s.history_turns)
    return invoke_with_retry(llm, [
        SystemMessage(content=VERIFY_COMPLIANCE),
        *history,
        HumanMessage(content=prompt),
    ])


# ── Stage 4: Result normalisation ────────────────────────────────────────────

def _parse_verify_result(raw: str) -> dict:
    """Parse LLM JSON, clamp risk_score to [0,10], add trinary compliance label."""
    fallback = {
        "compliant": None,
        "risk_score": -1,
        "issues": ["Не удалось разобрать ответ модели"],
        "law_refs": [],
        "fix_hints": [],
    }
    result = safe_parse_json(raw, fallback)

    compliant = result.get("compliant")
    if compliant is True:
        result["compliant_label"] = "Да"
    elif compliant is False:
        result["compliant_label"] = "Нет"
    else:
        result["compliant_label"] = "Не определено"

    raw_score = result.get("risk_score", -1)
    try:
        score = max(0, min(10, int(raw_score)))
        if raw_score != score:
            logger.warning("verify: risk_score %r clamped to %d", raw_score, score)
        result["risk_score"] = score
    except (TypeError, ValueError):
        logger.warning("verify: invalid risk_score %r — defaulting to 5", raw_score)
        result["risk_score"] = 5

    return result


# ── Stage 5: Human-readable summary ──────────────────────────────────────────

def _format_summary(result: dict) -> str:
    return (
        f"Соответствие: {result['compliant_label']}\n"
        f"Риск-оценка: {result['risk_score']}/10\n"
        f"Проблемы: {'; '.join(result.get('issues', []))}\n"
        f"Рекомендации: {'; '.join(result.get('fix_hints', []))}"
    )


# ── Graph entry point ─────────────────────────────────────────────────────────

def verify_node(state: AgentState) -> AgentState:
    """Compliance check node: fetch doc → gather norms → LLM → risk score."""
    t_start = time.perf_counter()
    query = state["user_query"]

    doc_content = _fetch_document_content(state, query)
    legal_context, graph_context, obl_context = _fetch_legal_context(query)
    raw = _run_compliance_llm(doc_content, legal_context, graph_context, obl_context, state)
    verify_result = _parse_verify_result(raw)
    summary = _format_summary(verify_result)
    final_response = build_final_response(summary, state.get("combined_responses") or [])

    elapsed = time.perf_counter() - t_start
    logger.info(
        "verify_node: compliant=%s risk=%d elapsed=%.2fs",
        verify_result["compliant_label"], verify_result["risk_score"], elapsed,
    )

    return {
        **state,
        "verify_result": verify_result,
        "final_response": final_response,
        "retrieval_metrics": {
            "node": "verify",
            "doc_content_len": len(doc_content),
            "risk_score": verify_result["risk_score"],
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
