"""Verify Agent — compliance check, risk scoring, law reference matching.

Improvements:
- Fetches actual document content when document_ids are provided
- Treats long user queries (>300 chars) as pasted document text
- Uses s.history_turns from config
- Uses invoke_with_retry for resilience
- Multi-intent: prepends combined_responses from prior agents
"""
from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_json_llm, invoke_with_retry
from ..core.utils import build_history_messages, safe_parse_json
from ..graph.state import AgentState
from ..tools.neo4j_query import graph_obligation_search, graph_section_search
from ..tools.qdrant_search import qdrant_search

logger = logging.getLogger(__name__)

_SYSTEM = """Ты — юридический эксперт по банковскому праву Казахстана.
Проанализируй документ / запрос на соответствие законодательству.
ВАЖНО: Все строки в JSON должны быть на русском языке.

Выдай JSON в точном формате:
{
  "compliant": true,
  "risk_score": 5,
  "issues": ["список нарушений или рисков"],
  "law_refs": ["ссылки на статьи законов"],
  "fix_hints": ["рекомендации по исправлению"]
}"""


def _fetch_document_content(state: AgentState, query: str) -> str:
    """Extract the document content to verify.

    Priority:
    1. document_ids provided -> fetch from Qdrant by scrolling for those IDs
    2. Long query (>300 chars) -> treat the query itself as pasted document text
    3. Otherwise -> search Qdrant for the most relevant document chunks
    """
    s = get_settings()

    # 1. Explicit document IDs — filter Qdrant by payload meta_json field.
    # NOTE: document_ids are OCR doc UUIDs (PostgreSQL), NOT Qdrant point UUIDs.
    # client.retrieve() expects Qdrant point IDs — must use scroll with payload filter instead.
    doc_ids = state.get("document_ids") or []
    if doc_ids:
        from ..core.utils import get_qdrant_client
        from qdrant_client import models as qmodels
        client = get_qdrant_client()
        try:
            chunks = []
            for doc_id in doc_ids[:3]:  # limit to 3 docs to avoid context overflow
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
                logger.info("verify_node: fetched %d chunks for document_ids=%s", len(chunks), doc_ids)
                return "\n\n".join(chunks)
        except Exception as exc:
            logger.warning("verify_node: failed to fetch document_ids from Qdrant: %s", exc)

    # 2. User pasted full document text inline (long query)
    if len(query) > 300:
        logger.info("verify_node: using long query (%d chars) as document text", len(query))
        return query

    # 3. Search for the most relevant chunks to verify
    hits = qdrant_search.invoke({"query": query, "limit": 5})
    if hits:
        return "\n\n".join(f"[{i+1}] {h.get('content', '')[:s.content_snippet_max_len]}"
                           for i, h in enumerate(hits))

    return query  # fallback: use the query text as-is


def verify_node(state: AgentState) -> AgentState:
    """Check compliance and compute risk score."""
    t_start = time.perf_counter()
    s = get_settings()
    query = state["user_query"]

    # 1. Get the actual document content to verify
    doc_content = _fetch_document_content(state, query)

    # 2. Retrieve legal context from Qdrant
    legal_hits = qdrant_search.invoke({"query": query, "limit": 5})
    legal_context = "\n".join(f"- {h.get('content', '')[:400]}" for h in legal_hits)

    # 3. Neo4j: relevant legal sections and article references
    graph_context_parts: list[str] = []
    try:
        sections = graph_section_search.invoke({"keywords": query[:60], "limit": 3})
        for sec in sections:
            text = (sec.get("text") or "")[:500]
            articles = sec.get("articles") or []
            article_refs = ", ".join(
                f"Статья {a.get('number')}" + (f" «{a.get('title')}»" if a.get("title") else "")
                for a in articles if a.get("number")
            )
            entry = text
            if article_refs:
                entry += f"\n[{article_refs}]"
            graph_context_parts.append(entry)
    except Exception as exc:
        logger.warning("Graph section search failed in verify_node: %s", exc)

    # 4. Neo4j: obligation context
    obl_context_parts: list[str] = []
    try:
        obls = graph_obligation_search.invoke({"keywords": query[:60], "limit": 4})
        for obl in obls:
            line = " — ".join(
                p for p in [obl.get("subject"), obl.get("action"), obl.get("object")] if p
            )
            if obl.get("deadline"):
                line += f" (срок: {obl['deadline']})"
            if line:
                obl_context_parts.append(line)
    except Exception as exc:
        logger.warning("Obligation search failed in verify_node: %s", exc)

    graph_context = "\n\n".join(graph_context_parts) if graph_context_parts else "нет данных"
    obl_context = "\n".join(f"- {o}" for o in obl_context_parts) or "нет данных"

    # Distinguish between user-query-as-document vs fetched content
    doc_label = "Содержимое документа" if len(doc_content) > 300 else "Запрос/фрагмент"
    prompt = (
        f"{doc_label}:\n{doc_content}\n\n"
        f"Применимые нормы (векторный поиск):\n{legal_context}\n\n"
        f"Релевантные статьи из графа знаний:\n{graph_context}\n\n"
        f"Выявленные обязательства из графа:\n{obl_context}"
    )

    llm = get_json_llm(num_predict=1024)
    history = build_history_messages(state, max_turns=s.history_turns)
    raw = invoke_with_retry(llm, [
        SystemMessage(content=_SYSTEM),
        *history,
        HumanMessage(content=prompt),
    ])

    _fallback: dict = {
        "compliant": None,
        "risk_score": -1,
        "issues": ["Не удалось разобрать ответ модели"],
        "law_refs": [],
        "fix_hints": [],
    }
    verify_result = safe_parse_json(raw, _fallback)

    # Trinary compliance label
    compliant = verify_result.get("compliant")
    if compliant is True:
        compliant_label = "Да"
    elif compliant is False:
        compliant_label = "Нет"
    else:
        compliant_label = "Не определено"

    # Clamp risk_score to [0, 10]
    raw_score = verify_result.get("risk_score", -1)
    try:
        risk_score = max(0, min(10, int(raw_score)))
        if raw_score != risk_score:
            logger.warning("verify_node: risk_score %r clamped to %d", raw_score, risk_score)
    except (TypeError, ValueError):
        logger.warning("verify_node: invalid risk_score %r — defaulting to 5", raw_score)
        risk_score = 5
    verify_result["risk_score"] = risk_score

    summary = (
        f"Соответствие: {compliant_label}\n"
        f"Риск-оценка: {risk_score}/10\n"
        f"Проблемы: {'; '.join(verify_result.get('issues', []))}\n"
        f"Рекомендации: {'; '.join(verify_result.get('fix_hints', []))}"
    )

    # Multi-intent: prepend previous agents' responses
    combined_prev = state.get("combined_responses") or []
    if combined_prev:
        final_response = "\n\n---\n\n".join(combined_prev) + "\n\n---\n\n" + summary
    else:
        final_response = summary

    elapsed = time.perf_counter() - t_start
    logger.info(
        "verify_node: compliant=%s risk=%d elapsed=%.2fs",
        compliant_label, risk_score, elapsed,
    )

    return {
        **state,
        "verify_result": verify_result,
        "final_response": final_response,
        "retrieval_metrics": {
            "node": "verify",
            "doc_content_len": len(doc_content),
            "legal_hits": len(legal_hits),
            "risk_score": risk_score,
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
