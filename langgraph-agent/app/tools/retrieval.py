"""Unified Retrieval Module — single entry point for all retrieval strategies.

Three retrieval scopes:
  POINT    — search across the entire collection (vector + corpus BM25 + Neo4j graph)
  SCOPED   — search within a single document (scroll all doc chunks → rerank)
  DOCUMENT — process entire document via Map-Reduce (for summary / verify / extract)

Design principles:
  - Single Responsibility: each strategy is an isolated private function.
  - Open/Closed: new strategies can be added without modifying ``retrieve()``.
  - Dependency Inversion: agents depend on ``RetrievalResult``, not on Qdrant/BM25 internals.
  - No cosine pre-filter before cross-encoder (incomparable score scales were dropping
    relevant corpus BM25 / graph hits).
  - BM25-over-vector-hits removed (returned the same IDs, cross-encoder re-sorts anyway).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..core.config import Settings, get_settings
from ..core.llm import get_llm, invoke_with_retry
from ..graph.state import RetrievalResult, Scope
from ..prompts import (
    MAP_EXTRACT,
    MAP_SUMMARY,
    MAP_VERIFY,
    REDUCE_EXTRACT,
    REDUCE_SUMMARY,
    REDUCE_VERIFY,
)

logger = logging.getLogger(__name__)

# ── Module-level thread pool for parallel Neo4j queries ──────────────────────

_graph_pool: ThreadPoolExecutor | None = None
_graph_pool_lock = threading.Lock()


def _get_graph_pool() -> ThreadPoolExecutor:
    global _graph_pool
    if _graph_pool is None:
        with _graph_pool_lock:
            if _graph_pool is None:
                _graph_pool = ThreadPoolExecutor(
                    max_workers=get_settings().graph_pool_workers,
                    thread_name_prefix="graph-query",
                )
    return _graph_pool


# ── Domain-specific regex patterns ───────────────────────────────────────────

_ARTICLE_NUM_RE = re.compile(r"[Сс]тать[яей]\s+(\d+)", re.UNICODE)
_ENTITY_ORG_RE = re.compile(
    r"\b(ТОО|АО|НАО|ОАО|ЗАО|ГП|ЧП|МФО|БВУ|Kaspi|Halyk|БТА|Цесна|Jusan|Нурбанк|Евразийский|Bereke)\b",
    re.UNICODE,
)
_CAPS_SEQUENCE_RE = re.compile(
    r"\b[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{2,}){1,}\b", re.UNICODE,
)
_LAW_QUERY_RE = re.compile(
    r"(?:Закон\s+(?:РК|Республики\s+Казахстан)\s+о[б]?\s+[а-яёА-ЯЁ][а-яё\s,]{4,50}"
    r"|(?:Гражданский|Налоговый|Трудовой|Уголовный)\s+кодекс"
    r"|(?:ГК|НК|ТК|КоАП|УК)\s+(?:РК|Республики\s+Казахстан))",
    re.IGNORECASE | re.UNICODE,
)
_OBL_KEYWORDS = frozenset([
    "обяза", "должен", "обязан", "ответствен", "вправе", "право ",
    "запрещ", "недопустим", "обязательств",
])
_RU_STOPWORDS = frozenset([
    "и", "в", "на", "по", "с", "к", "о", "об", "из", "от", "до", "для",
    "что", "как", "это", "все", "при", "или", "но", "не", "да", "же",
    "а", "то", "так", "где", "когда", "если", "чтобы", "кто", "который",
    "мне", "мы", "вы", "он", "она", "они", "его", "её", "их", "этот",
    "эта", "эти", "того", "тот", "за", "со", "во", "без", "под", "над",
])


def _extract_graph_keywords(query: str, max_words: int) -> str:
    words = re.findall(r"[а-яёА-ЯЁa-zA-Z0-9]+", query)
    significant = [w for w in words if w.lower() not in _RU_STOPWORDS and len(w) >= 3]
    return " ".join(significant[:max_words])


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═════════════════════════════════════════════════════════════════════════════

def retrieve(
    query: str,
    scope: Scope = "point",
    *,
    doc_id: str | None = None,
    top_k: int | None = None,
    task_type: str = "summary",
    legal_context: str = "",
) -> RetrievalResult:
    """Unified retrieval entry point.

    Args:
        query: User's search query (already stripped of conversational prefix).
        scope: Retrieval strategy — ``"point"``, ``"scoped"``, or ``"document"``.
        doc_id: Required for ``scoped`` and ``document`` scopes.
        top_k: Override for number of chunks returned to LLM.
                Defaults: point=7, scoped=20, document=all.
        task_type: For ``document`` scope — ``"summary"`` | ``"verify"`` | ``"extract"``.
        legal_context: For ``document`` + verify — pre-fetched legal norms string.

    Returns:
        ``RetrievalResult`` with chunks, scores, and metrics.
    """
    s = get_settings()
    t_start = time.perf_counter()

    if scope == "scoped" and doc_id:
        result = _scoped_retrieval(query, doc_id, top_k or s.scoped_rerank_top_k, s)
    elif scope == "document" and doc_id:
        result = _document_retrieval(query, doc_id, task_type, legal_context, s)
    else:
        result = _point_retrieval(query, top_k or s.rerank_top_k, s)

    elapsed = time.perf_counter() - t_start
    result.metrics["elapsed_s"] = round(elapsed, 2)
    result.metrics["scope"] = scope
    return result


# ═════════════════════════════════════════════════════════════════════════════
# POINT RETRIEVAL — search entire collection
# ═════════════════════════════════════════════════════════════════════════════

def _point_retrieval(query: str, top_k: int, s: Settings) -> RetrievalResult:
    """Vector(40) + corpus BM25(40) + Neo4j(5 types) → merge → cross-encoder → top_k.

    Changes vs original search_agent:
      - Removed BM25-over-vector-hits (returned same IDs, cross-encoder re-sorts).
      - Removed cosine pre-filter before cross-encoder (incomparable score scales).
    """
    from .bm25_search import corpus_bm25_search
    from .qdrant_search import qdrant_search
    from .reranker import reranker

    # 1. Vector search — broad semantic recall
    vector_hits: list[dict] = qdrant_search.invoke({
        "query": query,
        "collection": s.qdrant_collection,
        "limit": s.qdrant_top_k,
    })

    # 2. Corpus BM25 — catches what vector search misses (article numbers, law names)
    corpus_hits: list[dict] = corpus_bm25_search(
        query=query,
        top_k=s.bm25_top_k,
        collection=s.qdrant_collection,
    )

    # 3. Neo4j graph enrichment (5 types in parallel)
    graph_results = _retrieve_graph(query, s)
    graph_hits = _normalize_graph_hits(graph_results, s)

    # 4. Merge + dedup (no BM25-over-vector — it was redundant)
    merged = _merge_hits(vector_hits, corpus_hits, graph_hits)

    # 5. Relevance filter (cosine floor — only removes obvious noise)
    filtered = [h for h in merged if h.get("score", 0) >= s.min_relevance_score]
    if not filtered and merged:
        filtered = merged

    # 6. Cross-encoder rerank ALL candidates (no cosine pre-filter)
    reranked: list[dict] = (
        reranker.invoke({"query": query, "documents": filtered, "top_k": top_k})
        if filtered else []
    )

    best_score = reranked[0].get("rerank_score", 0.0) if reranked else 0.0
    has_context = bool(reranked) and best_score >= s.search_min_confidence

    return RetrievalResult(
        chunks=reranked,
        best_score=best_score,
        has_context=has_context,
        scope="point",
        metrics={
            "vector_hits": len(vector_hits),
            "corpus_bm25_hits": len(corpus_hits),
            "graph_hits": len(graph_hits),
            "merged_hits": len(merged),
            "filtered_hits": len(filtered),
            "reranked_hits": len(reranked),
            "best_rerank_score": round(best_score, 4),
            "has_context": has_context,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# SCOPED RETRIEVAL — search within a single document
# ═════════════════════════════════════════════════════════════════════════════

def _scoped_retrieval(
    query: str, doc_id: str, top_k: int, s: Settings,
) -> RetrievalResult:
    """Scroll all chunks of one document → BM25 pre-filter → cross-encoder → top_k.

    For small documents (≤ scoped_small_doc_threshold chunks) all chunks go
    directly to LLM context without reranking.
    """
    from .bm25_search import bm25_search
    from .qdrant_search import scroll_all_by_doc_id
    from .reranker import reranker

    all_chunks = scroll_all_by_doc_id(doc_id, collection=s.qdrant_collection)
    total = len(all_chunks)

    if total == 0:
        return RetrievalResult(
            scope="scoped",
            total_doc_chunks=0,
            metrics={"doc_id": doc_id, "total_doc_chunks": 0, "strategy": "empty"},
        )

    # Small document — pass everything to LLM, no reranking needed
    if total <= s.scoped_small_doc_threshold:
        return RetrievalResult(
            chunks=all_chunks,
            best_score=1.0,
            has_context=True,
            scope="scoped",
            total_doc_chunks=total,
            metrics={
                "doc_id": doc_id,
                "total_doc_chunks": total,
                "strategy": "full_context",
                "chunks_to_llm": total,
            },
        )

    # Medium/large document — BM25 pre-filter then cross-encoder
    # BM25 narrows from 200+ to ~60 candidates before expensive cross-encoder
    bm25_candidates: list[dict] = bm25_search.invoke({
        "query": query,
        "documents": all_chunks,
        "top_k": min(total, 60),
    })
    # If BM25 returned nothing (all scores 0), fall back to all chunks
    candidates = bm25_candidates if bm25_candidates else all_chunks

    reranked: list[dict] = reranker.invoke({
        "query": query,
        "documents": candidates,
        "top_k": top_k,
    })

    best_score = reranked[0].get("rerank_score", 0.0) if reranked else 0.0

    return RetrievalResult(
        chunks=reranked,
        best_score=best_score,
        has_context=bool(reranked) and best_score >= s.search_min_confidence,
        scope="scoped",
        total_doc_chunks=total,
        metrics={
            "doc_id": doc_id,
            "total_doc_chunks": total,
            "strategy": "scoped_rerank",
            "bm25_candidates": len(candidates),
            "reranked_hits": len(reranked),
            "best_rerank_score": round(best_score, 4),
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# DOCUMENT RETRIEVAL — Map-Reduce over entire document
# ═════════════════════════════════════════════════════════════════════════════

def _document_retrieval(
    query: str,
    doc_id: str,
    task_type: str,
    legal_context: str,
    s: Settings,
) -> RetrievalResult:
    """Map-Reduce: split document into batches → LLM each → merge results.

    Supports task_type: summary, verify, extract.
    """
    from .qdrant_search import scroll_all_by_doc_id

    all_chunks = scroll_all_by_doc_id(doc_id, collection=s.qdrant_collection)
    total = len(all_chunks)

    if total == 0:
        return RetrievalResult(
            scope="document",
            total_doc_chunks=0,
            metrics={"doc_id": doc_id, "total_doc_chunks": 0, "strategy": "empty"},
        )

    # Small document — skip Map phase, send everything directly
    if total <= s.scoped_small_doc_threshold:
        return RetrievalResult(
            chunks=all_chunks,
            best_score=1.0,
            has_context=True,
            scope="document",
            total_doc_chunks=total,
            metrics={
                "doc_id": doc_id,
                "total_doc_chunks": total,
                "strategy": "full_context",
            },
        )

    # ── MAP phase ────────────────────────────────────────────────────────────
    map_prompt = _get_map_prompt(task_type)
    batch_size = s.map_reduce_batch_size
    batches = [
        all_chunks[i:i + batch_size]
        for i in range(0, total, batch_size)
    ]

    intermediate_results: list[str] = []
    llm = get_llm(temperature=0.1, num_predict=s.map_reduce_map_num_predict)

    for batch_idx, batch in enumerate(batches):
        batch_text = "\n\n".join(
            f"[Фрагмент {i + 1}]\n{c.get('content', '')[:s.content_snippet_max_len]}"
            for i, c in enumerate(batch)
        )

        user_msg = f"Запрос пользователя: {query}\n\n{batch_text}"
        if task_type == "verify" and legal_context:
            user_msg += f"\n\nПрименимые нормы:\n{legal_context}"

        result = invoke_with_retry(llm, [
            SystemMessage(content=map_prompt),
            HumanMessage(content=user_msg),
        ])

        if result.strip():
            intermediate_results.append(
                f"--- Часть {batch_idx + 1}/{len(batches)} ---\n{result.strip()}"
            )

        logger.debug(
            "map_reduce MAP batch %d/%d: %d chars",
            batch_idx + 1, len(batches), len(result),
        )

    # ── REDUCE phase ─────────────────────────────────────────────────────────
    reduce_prompt = _get_reduce_prompt(task_type).format(
        batch_count=len(batches),
        total_chunks=total,
    )

    reduce_llm = get_llm(temperature=0.1, num_predict=s.map_reduce_reduce_num_predict)
    combined_intermediates = "\n\n".join(intermediate_results)

    reduce_result = invoke_with_retry(reduce_llm, [
        SystemMessage(content=reduce_prompt),
        HumanMessage(content=f"Запрос: {query}\n\n{combined_intermediates}"),
    ])

    logger.info(
        "map_reduce: doc_id=%r total=%d batches=%d task=%s reduce_len=%d",
        doc_id, total, len(batches), task_type, len(reduce_result),
    )

    # Return reduce result as a single "chunk" for uniform handling
    return RetrievalResult(
        chunks=[{
            "id": f"mapreduce:{doc_id}",
            "content": reduce_result,
            "score": 1.0,
            "rerank_score": 1.0,
            "section": f"Map-Reduce ({total} фрагментов)",
            "metadata": {"doc_id": doc_id, "strategy": "map_reduce"},
        }],
        best_score=1.0,
        has_context=bool(reduce_result.strip()),
        scope="document",
        total_doc_chunks=total,
        metrics={
            "doc_id": doc_id,
            "total_doc_chunks": total,
            "strategy": "map_reduce",
            "batch_count": len(batches),
            "intermediate_count": len(intermediate_results),
            "reduce_len": len(reduce_result),
        },
    )


def _get_map_prompt(task_type: str) -> str:
    return {
        "summary": MAP_SUMMARY,
        "verify": MAP_VERIFY,
        "extract": MAP_EXTRACT,
    }.get(task_type, MAP_SUMMARY)


def _get_reduce_prompt(task_type: str) -> str:
    return {
        "summary": REDUCE_SUMMARY,
        "verify": REDUCE_VERIFY,
        "extract": REDUCE_EXTRACT,
    }.get(task_type, REDUCE_SUMMARY)


# ═════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS — Neo4j graph retrieval + merge + normalize
# ═════════════════════════════════════════════════════════════════════════════

def _retrieve_graph(query: str, s: Settings) -> dict[str, list[dict]]:
    """Run 5 graph queries in parallel with a timeout."""
    from .neo4j_query import (
        graph_entity_lookup,
        graph_obligation_search,
        graph_section_search,
        neo4j_query,
    )

    kw = _extract_graph_keywords(query, s.graph_kw_max_words)
    art_match = _ARTICLE_NUM_RE.search(query)
    needs_obligations = any(ob_kw in query.lower() for ob_kw in _OBL_KEYWORDS)
    needs_entities = bool(_ENTITY_ORG_RE.search(query) or _CAPS_SEQUENCE_RE.search(query))
    law_match = _LAW_QUERY_RE.search(query)

    def _sections() -> list[dict]:
        return graph_section_search.invoke({"keywords": kw, "limit": 4})

    def _articles() -> list[dict]:
        if not art_match:
            return []
        return neo4j_query.invoke({
            "cypher": (
                "MATCH (a:Article {number: $num})<-[:HAS_ARTICLE]-(s:Section)"
                "<-[:CONTAINS]-(d:Document) "
                "RETURN a.number AS number, a.title AS title, "
                "s.text_preview AS text, d.filename AS source LIMIT 3"
            ),
            "params": {"num": int(art_match.group(1))},
        })

    def _obligations() -> list[dict]:
        if not needs_obligations:
            return []
        return graph_obligation_search.invoke({"keywords": kw, "limit": 3})

    def _entities() -> list[dict]:
        if not needs_entities:
            return []
        return graph_entity_lookup.invoke({"text": kw[:60], "limit": 4})

    def _laws() -> list[dict]:
        if not law_match:
            return []
        return neo4j_query.invoke({
            "cypher": (
                "MATCH (l:Law) "
                "WHERE toLower(l.title) CONTAINS toLower($kw) "
                "OPTIONAL MATCH (l)-[:HAS_ARTICLE]->(a:Article) "
                "RETURN l.law_id AS law_id, l.title AS title, "
                "collect({number: a.number, title: a.title}) AS articles LIMIT 3"
            ),
            "params": {"kw": law_match.group(0)[:80]},
        })

    results: dict[str, list[dict]] = {
        k: [] for k in ("sections", "articles", "obligations", "entities", "laws")
    }
    task_map = {
        "sections": _sections,
        "articles": _articles,
        "obligations": _obligations,
        "entities": _entities,
        "laws": _laws,
    }

    pool = _get_graph_pool()
    futures = {pool.submit(fn): name for name, fn in task_map.items()}
    try:
        for future in as_completed(futures, timeout=s.graph_enrichment_timeout):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                logger.warning("graph_%s lookup failed: %s", name, exc)
    except TimeoutError:
        logger.warning("graph enrichment timeout (%.1fs)", s.graph_enrichment_timeout)

    return results


def _normalize_graph_hits(
    graph_results: dict[str, list[dict]], s: Settings,
) -> list[dict]:
    """Convert raw Neo4j records to unified hit dict format."""
    hits: list[dict] = []

    for sec in graph_results.get("sections", []):
        text = sec.get("text") or ""
        if text:
            hits.append({
                "id": sec.get("section_id", ""),
                "content": text[:s.content_snippet_max_len],
                "score": float(sec.get("score", 0.0)),
                "section": f"стр. {sec.get('page', '?')} — {sec.get('source', '')}",
                "metadata": {"articles": sec.get("articles", [])},
            })

    for rec in graph_results.get("articles", []):
        if rec.get("text"):
            art_num = rec.get("number", "?")
            title_part = f": {rec['title']}" if rec.get("title") else ""
            hits.append({
                "id": f"article:{art_num}:{rec.get('source', '')}",
                "content": f"Статья {art_num}{title_part}\n{rec['text'][:s.content_snippet_max_len]}",
                "score": 1.0,
                "section": rec.get("source", ""),
                "metadata": {"article_number": art_num, "article_title": rec.get("title", "")},
            })

    for rec in graph_results.get("obligations", []):
        content = " — ".join(
            p for p in [rec.get("subject"), rec.get("action"), rec.get("object")] if p
        )
        if rec.get("evidence"):
            content += f"\n{rec['evidence']}"
        hits.append({
            "id": f"obl:{rec.get('doc_id', '')}:{hash(content)}",
            "content": content[:s.content_snippet_max_len],
            "score": float(rec.get("confidence", 0.7)),
            "section": rec.get("document", ""),
            "metadata": {"type": "obligation", "deadline": rec.get("deadline", "")},
        })

    for rec in graph_results.get("entities", []):
        ent_text = rec.get("text", "")
        if not ent_text:
            continue
        ent_label = rec.get("label", "")
        content_parts = [f"{ent_label}: {ent_text}"]
        if rec.get("role"):
            content_parts.append(f"Роль: {rec['role']}")
        if rec.get("evidence"):
            content_parts.append(rec["evidence"])
        docs = rec.get("documents") or []
        hits.append({
            "id": f"ent:{ent_label}:{ent_text}",
            "content": "\n".join(content_parts)[:s.content_snippet_max_len],
            "score": float(rec.get("confidence", 0.6)),
            "section": ", ".join(docs[:2]) if docs else "",
            "metadata": {"type": "entity", "entity_label": ent_label},
        })

    for rec in graph_results.get("laws", []):
        law_title = rec.get("title", "")
        if not law_title:
            continue
        articles = rec.get("articles") or []
        art_list = ", ".join(
            f"ст.{a.get('number', '')}{': ' + a['title'] if a.get('title') else ''}"
            for a in articles[:10] if a.get("number")
        )
        content = f"Закон: {law_title}"
        if art_list:
            content += f"\nСтатьи: {art_list}"
        hits.append({
            "id": f"law:{rec.get('law_id', law_title)}",
            "content": content[:s.content_snippet_max_len],
            "score": 1.0,
            "section": law_title,
            "metadata": {"type": "law", "law_id": rec.get("law_id", "")},
        })

    return hits


def _merge_hits(*sources: list[dict]) -> list[dict]:
    """Merge multiple hit lists, deduplicating by ID."""
    seen_ids: set[str] = set()
    merged: list[dict] = []

    for idx, source in enumerate(sources):
        for hit in source:
            raw_id = hit.get("id")
            hit_id = str(raw_id) if raw_id else f"src:{idx}:{hash(hit.get('content', ''))}"
            if hit_id not in seen_ids:
                seen_ids.add(hit_id)
                merged.append(hit)

    return merged
