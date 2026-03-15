"""Analyze Agent — Q&A on document, comparison, extraction, summary.

Pipeline:
  1. _detect_task         — classify query into qa/compare/extract/summary
  2. _enrich_search_query — referential enrichment ("этот документ" → filename)
  3. _fetch_context       — Qdrant chunks + Neo4j sections + Neo4j entities
  4. _run_analysis_llm    — LLM JSON generation (task-specific system prompt)
  5. _format_summary      — assemble human-readable markdown summary
  6. analyze_node         — orchestrator
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_json_llm, invoke_with_retry
from ..core.utils import (
    DOC_REF_RE,
    build_final_response,
    build_history_messages,
    extract_recent_filename,
    safe_parse_json,
)
from ..graph.state import AgentState
from ..prompts import ANALYZE_COMPARE, ANALYZE_DOCUMENT
from ..tools.neo4j_query import graph_entity_lookup, graph_section_search
from ..tools.qdrant_search import qdrant_search

logger = logging.getLogger(__name__)

# ── Module-level thread pool for parallel Neo4j queries ──────────────────────
_analyze_pool: ThreadPoolExecutor | None = None
_analyze_pool_lock = threading.Lock()


def _get_analyze_pool() -> ThreadPoolExecutor:
    global _analyze_pool
    if _analyze_pool is None:
        with _analyze_pool_lock:
            if _analyze_pool is None:
                _analyze_pool = ThreadPoolExecutor(
                    max_workers=3, thread_name_prefix="analyze-ctx"
                )
    return _analyze_pool


def shutdown_analyze_pool() -> None:
    """Gracefully shut down the analyze context thread pool. Call on app shutdown."""
    global _analyze_pool
    with _analyze_pool_lock:
        if _analyze_pool is not None:
            _analyze_pool.shutdown(wait=True, cancel_futures=False)
            _analyze_pool = None
            logger.info("Analyze context thread pool shut down.")


# Task classification patterns (Russian morphology) — pre-compiled for performance.
# re.search() on a pattern string recompiles on every call; using compiled
# Pattern objects avoids that overhead when _detect_task is called per request.
_COMPARE_RE = re.compile(
    r"сравн|отличи[ея]|разниц|различи[ея]",
    re.IGNORECASE | re.UNICODE,
)
_EXTRACT_RE = re.compile(
    r"извлек|вытащ|выдел[и]|найди все|укажи все|перечисл",
    re.IGNORECASE | re.UNICODE,
)
_SUMMARY_RE = re.compile(
    r"резюм|суммар|кратк|суммаризу",
    re.IGNORECASE | re.UNICODE,
)


# ── Stage 1: Task classification ─────────────────────────────────────────────

def _detect_task(query: str) -> str:
    """Return one of: compare, extract, summary, qa (default)."""
    if _COMPARE_RE.search(query):
        return "compare"
    if _EXTRACT_RE.search(query):
        return "extract"
    if _SUMMARY_RE.search(query):
        return "summary"
    return "qa"


# ── Stage 2: Referential query enrichment ────────────────────────────────────

def _enrich_search_query(query: str, state: AgentState) -> tuple[str, str | None]:
    """Expand "этот документ" references with the actual filename.

    Returns:
        (search_query, recent_filename) — search_query may be enriched.
    """
    if DOC_REF_RE.search(query):
        filename = extract_recent_filename(state)
        if filename:
            logger.info("analyze: referential query enriched with filename=%s", filename)
            return f"{filename} {query}", filename
    return query, None


# ── Stage 3: Context retrieval ────────────────────────────────────────────────

def _fetch_context(
    search_query: str, task: str
) -> tuple[str, str, str, list[dict]]:
    """Gather document context from Qdrant and Neo4j in parallel.

    Runs up to 3 I/O tasks concurrently:
      - Qdrant vector search
      - Neo4j section search (always)
      - Neo4j entity lookup (only for qa/extract tasks)

    Returns:
        (qdrant_chunks_str, graph_sections_str, entity_context_str, raw_hits)
    """
    needs_entities = task in ("extract", "qa")
    term = " ".join(search_query.split()[:5])

    def _qdrant() -> list[dict]:
        return qdrant_search.invoke({"query": search_query, "limit": 6})

    def _sections() -> list[dict]:
        return graph_section_search.invoke({"keywords": search_query[:80], "limit": 3})

    def _entities() -> list[dict]:
        if not needs_entities:
            return []
        return graph_entity_lookup.invoke({"text": term[:60], "limit": 5})

    pool = _get_analyze_pool()
    task_map = {"qdrant": _qdrant, "sections": _sections, "entities": _entities}
    futures = {pool.submit(fn): name for name, fn in task_map.items()}
    raw: dict[str, list[dict]] = {k: [] for k in task_map}

    for future in as_completed(futures, timeout=15):
        name = futures[future]
        try:
            raw[name] = future.result()
        except Exception as exc:
            logger.warning("analyze: %s lookup failed: %s", name, exc)

    # ── Format Qdrant results ─────────────────────────────────────────────────
    hits = raw["qdrant"]
    qdrant_chunks = "\n\n".join(
        f"[{i + 1}] {h.get('content', '')[:500]}" for i, h in enumerate(hits)
    )

    # ── Format graph section results ──────────────────────────────────────────
    graph_parts: list[str] = []
    for sec in raw["sections"]:
        text = (sec.get("text") or "")[:400]
        articles = sec.get("articles") or []
        refs = ", ".join(
            f"Статья {a.get('number')}" + (f" «{a.get('title')}»" if a.get("title") else "")
            for a in articles if a.get("number")
        )
        entry = text + (f"\n[Статьи: {refs}]" if refs else "")
        graph_parts.append(entry)
    graph_context = "\n".join(graph_parts) or "нет данных"

    # ── Format entity results ─────────────────────────────────────────────────
    entity_parts: list[str] = []
    for ent in raw["entities"]:
        line = f"{ent.get('text')} [{ent.get('label')}]"
        if ent.get("role"):
            line += f" роль: {ent['role']}"
        docs = ent.get("documents") or []
        if docs:
            line += f" — из: {', '.join(docs[:2])}"
        entity_parts.append(line)
    entity_context = "\n".join(f"- {e}" for e in entity_parts) or "нет данных"

    return qdrant_chunks, graph_context, entity_context, hits


# ── Stage 4: LLM analysis ────────────────────────────────────────────────────

def _run_analysis_llm(
    query: str,
    task: str,
    recent_filename: str | None,
    qdrant_chunks: str,
    graph_context: str,
    entity_context: str,
    state: AgentState,
) -> dict:
    """Call LLM with task-specific system prompt and return parsed JSON result."""
    s = get_settings()
    system_prompt = ANALYZE_COMPARE if task == "compare" else ANALYZE_DOCUMENT
    doc_hint = f"\nАнализируемый документ: {recent_filename}\n" if recent_filename else ""
    prompt = (
        f"Запрос: {query}\n{doc_hint}\n"
        f"Фрагменты документов (Qdrant):\n{qdrant_chunks}\n\n"
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
    return safe_parse_json(
        raw, {"task": task, "result": raw, "entities": [], "key_points": [], "confidence": 0.5}
    )


# ── Stage 5: Human-readable summary ──────────────────────────────────────────

def _format_summary(result: dict, task: str) -> str:
    parts = [f"**Анализ ({task.upper()}):**\n\n{result.get('result', '')}"]
    if result.get("key_points"):
        parts.append("**Ключевые тезисы:**\n" + "\n".join(f"- {p}" for p in result["key_points"]))
    if result.get("similarities"):
        parts.append("**Сходства:**\n" + "\n".join(f"- {s}" for s in result["similarities"]))
    if result.get("differences"):
        parts.append("**Различия:**\n" + "\n".join(f"- {d}" for d in result["differences"]))
    if result.get("legal_conflicts"):
        parts.append("**Противоречия:**\n" + "\n".join(f"- {c}" for c in result["legal_conflicts"]))
    if result.get("entities"):
        entity_str = ", ".join(
            f"{e.get('value', e.get('name', ''))} [{e.get('type', '')}]"
            for e in result["entities"]
            if e.get("value") or e.get("name")
        )
        if entity_str:
            parts.append(f"**Сущности:** {entity_str}")
    if result.get("recommendations"):
        parts.append("**Рекомендации:**\n" + "\n".join(f"- {r}" for r in result["recommendations"]))
    parts.append(f"*Уверенность: {result.get('confidence', 0.5):.0%}*")
    return "\n\n".join(parts)


# ── Graph entry point ─────────────────────────────────────────────────────────

def analyze_node(state: AgentState) -> AgentState:
    """Analysis node: detect task → enrich query → retrieve → LLM → format."""
    t_start = time.perf_counter()
    query = state["user_query"]

    task = _detect_task(query)
    search_query, recent_filename = _enrich_search_query(query, state)
    qdrant_chunks, graph_context, entity_context, hits = _fetch_context(search_query, task)
    analyze_result = _run_analysis_llm(
        query, task, recent_filename, qdrant_chunks, graph_context, entity_context, state
    )
    summary = _format_summary(analyze_result, task)
    final_response = build_final_response(summary, state.get("combined_responses") or [])

    citations = [
        {"index": i + 1, "content": h.get("content", "")[:200], "score": h.get("score", 0.0)}
        for i, h in enumerate(hits[:3])
    ]

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
            "confidence": analyze_result.get("confidence", 0),
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
