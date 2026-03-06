"""Analyze Agent — Q&A on document, comparison, extraction, summary.

Improvements:
- Uses s.history_turns from config (not hardcoded 2)
- Uses invoke_with_retry for resilience
- Multi-intent: prepends combined_responses from prior agents
- Structured metrics logging
"""
from __future__ import annotations

import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_json_llm, invoke_with_retry
from ..core.utils import build_history_messages, safe_parse_json
from ..graph.state import AgentState
from ..tools.neo4j_query import graph_entity_lookup, graph_section_search
from ..tools.qdrant_search import qdrant_search

logger = logging.getLogger(__name__)

_DOC_REF_RE = re.compile(
    r"\b(этот\s+документ|этого\s+документа|в\s+нём|в\s+нем|в\s+ней|об\s+этом|данный\s+документ|"
    r"этот\s+файл|в\s+этом\s+документе|из\s+этого\s+документа|этот\s+текст)\b",
    re.IGNORECASE | re.UNICODE,
)
_FILENAME_RE = re.compile(r"[\w\-]+\.(?:pdf|docx|doc|txt|json)\b", re.IGNORECASE)

_SYSTEM = """Ты — аналитик документов по банковскому праву Казахстана.
ВАЖНО: Все текстовые значения в JSON должны быть на русском языке.

Выдай JSON:
{
  "task": "qa",
  "result": "основной результат на русском",
  "entities": [{"type": "org", "value": "название"}],
  "key_points": ["тезис 1 на русском"],
  "recommendations": ["рекомендация на русском"],
  "confidence": 0.9
}"""

_COMPARE_SYSTEM = """Ты — аналитик документов по банковскому праву Казахстана.
ВАЖНО: Все текстовые значения в JSON должны быть на русском языке.

Выдай JSON:
{
  "task": "compare",
  "similarities": ["сходство на русском"],
  "differences": ["различие на русском"],
  "legal_conflicts": ["противоречие на русском"],
  "recommendation": "итог на русском",
  "confidence": 0.9
}"""


def _detect_task(query: str) -> str:
    q = query.lower()
    _compare_stems = [r"сравн", r"отличи[ея]", r"разниц", r"различи[ея]"]
    _extract_stems = [r"извлек", r"вытащ", r"выдел[и]", r"найди все", r"укажи все", r"перечисл"]
    _summary_stems = [r"резюм", r"суммар", r"кратк", r"суммаризу"]
    for stem in _compare_stems:
        if re.search(stem, q):
            return "compare"
    for stem in _extract_stems:
        if re.search(stem, q):
            return "extract"
    for stem in _summary_stems:
        if re.search(stem, q):
            return "summary"
    return "qa"


def _extract_recent_filename(state: AgentState) -> str | None:
    messages = state.get("messages", [])
    for msg in reversed(messages[:-1]):
        content = getattr(msg, "content", "") or ""
        matches = _FILENAME_RE.findall(content)
        if matches:
            return matches[0]
    return None


def analyze_node(state: AgentState) -> AgentState:
    """Analyze a document: Q&A, compare, extract entities, or summarize."""
    t_start = time.perf_counter()
    s = get_settings()
    query = state["user_query"]
    task = _detect_task(query)

    # 1. Detect follow-up reference ("этот документ") and enrich search query
    search_query = query
    recent_filename: str | None = None
    if _DOC_REF_RE.search(query):
        recent_filename = _extract_recent_filename(state)
        if recent_filename:
            search_query = f"{recent_filename} {query}"
            logger.info("analyze_node: referential query enriched with filename=%s", recent_filename)

    # 2. Qdrant context
    hits = qdrant_search.invoke({"query": search_query, "limit": 6})
    context_chunks = "\n\n".join(
        f"[{i+1}] {h.get('content', '')[:500]}" for i, h in enumerate(hits)
    )

    # 3. Neo4j graph context
    graph_context_parts: list[str] = []
    try:
        sections = graph_section_search.invoke({"keywords": search_query[:80], "limit": 3})
        for sec in sections:
            text = (sec.get("text") or "")[:400]
            articles = sec.get("articles") or []
            article_refs = ", ".join(
                f"Статья {a.get('number')}" + (f" «{a.get('title')}»" if a.get("title") else "")
                for a in articles if a.get("number")
            )
            entry = text
            if article_refs:
                entry += f"\n[Статьи: {article_refs}]"
            graph_context_parts.append(entry)
    except Exception as exc:
        logger.warning("Graph section search failed in analyze_node: %s", exc)

    graph_context = "\n".join(graph_context_parts) if graph_context_parts else "нет данных"

    # 4. Entity lookup from graph
    entity_context_parts: list[str] = []
    if task in ("extract", "qa"):
        try:
            search_term = " ".join(search_query.split()[:5])
            ents = graph_entity_lookup.invoke({"text": search_term[:60], "limit": 5})
            for ent in ents:
                line = f"{ent.get('text')} [{ent.get('label')}]"
                if ent.get("role"):
                    line += f" роль: {ent['role']}"
                docs = ent.get("documents") or []
                if docs:
                    line += f" — из: {', '.join(docs[:2])}"
                entity_context_parts.append(line)
        except Exception as exc:
            logger.warning("Entity lookup failed in analyze_node: %s", exc)

    entity_context = "\n".join(f"- {e}" for e in entity_context_parts) or "нет данных"

    system_prompt = _COMPARE_SYSTEM if task == "compare" else _SYSTEM
    doc_hint = f"\nАнализируемый документ: {recent_filename}\n" if recent_filename else ""
    prompt = (
        f"Запрос: {query}\n{doc_hint}\n"
        f"Фрагменты документов (Qdrant):\n{context_chunks}\n\n"
        f"Релевантные секции из графа знаний (Neo4j):\n{graph_context}\n\n"
        f"Сущности из графа (организации, стороны, роли):\n{entity_context}"
    )

    llm = get_json_llm(num_predict=1500)
    history = build_history_messages(state, max_turns=s.history_turns)
    raw = invoke_with_retry(llm, [
        SystemMessage(content=system_prompt),
        *history,
        HumanMessage(content=prompt),
    ])

    analyze_result = safe_parse_json(
        raw, {"task": task, "result": raw, "entities": [], "key_points": [], "confidence": 0.5}
    )

    # Build summary
    parts = [f"**Анализ ({task.upper()}):**\n\n{analyze_result.get('result', '')}"]
    if analyze_result.get("key_points"):
        parts.append("**Ключевые тезисы:**\n" + "\n".join(f"- {p}" for p in analyze_result["key_points"]))
    if analyze_result.get("similarities"):
        parts.append("**Сходства:**\n" + "\n".join(f"- {s}" for s in analyze_result["similarities"]))
    if analyze_result.get("differences"):
        parts.append("**Различия:**\n" + "\n".join(f"- {d}" for d in analyze_result["differences"]))
    if analyze_result.get("legal_conflicts"):
        parts.append("**Противоречия:**\n" + "\n".join(f"- {c}" for c in analyze_result["legal_conflicts"]))
    if analyze_result.get("entities"):
        entity_str = ", ".join(
            f"{e.get('value', e.get('name', ''))} [{e.get('type', '')}]"
            for e in analyze_result["entities"]
            if e.get("value") or e.get("name")
        )
        if entity_str:
            parts.append(f"**Сущности:** {entity_str}")
    if analyze_result.get("recommendations"):
        parts.append("**Рекомендации:**\n" + "\n".join(f"- {r}" for r in analyze_result["recommendations"]))
    parts.append(f"*Уверенность: {analyze_result.get('confidence', 0.5):.0%}*")

    summary = "\n\n".join(parts)

    citations = [
        {"index": i + 1, "content": h.get("content", "")[:200], "score": h.get("score", 0.0)}
        for i, h in enumerate(hits[:3])
    ]

    # Multi-intent: prepend previous agents' responses
    combined_prev = state.get("combined_responses") or []
    if combined_prev:
        final_response = "\n\n---\n\n".join(combined_prev) + "\n\n---\n\n" + summary
    else:
        final_response = summary

    elapsed = time.perf_counter() - t_start
    logger.info(
        "analyze_node: task=%s hits=%d confidence=%.2f elapsed=%.2fs",
        task, len(hits), analyze_result.get("confidence", 0), elapsed,
    )

    return {
        **state,
        "analyze_result": analyze_result,
        "citations": citations,
        "final_response": final_response,
        "retrieval_metrics": {
            "node": "analyze",
            "task": task,
            "qdrant_hits": len(hits),
            "graph_sections": len(graph_context_parts),
            "entities": len(entity_context_parts),
            "confidence": analyze_result.get("confidence", 0),
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
