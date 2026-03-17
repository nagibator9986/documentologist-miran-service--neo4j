"""Debug endpoints for retrieval pipeline diagnostics.

These endpoints expose internal retrieval stages without LLM calls,
enabling threshold calibration and quality debugging.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ...core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/debug", tags=["debug"])


class RetrievalStageResult(BaseModel):
    query_original: str
    query_for_embedding: str
    collection: str
    stages: dict[str, Any]  # each stage: {count, top_scores, items}
    thresholds: dict[str, float]  # active thresholds from config
    elapsed_s: float


def _top_scores(hits: list[dict], key: str = "score", n: int = 3) -> list[float]:
    """Extract top-N scores from a list of hit dicts."""
    scores = [float(h.get(key, 0)) for h in hits if h.get(key) is not None]
    scores.sort(reverse=True)
    return [round(s, 4) for s in scores[:n]]


@router.get("/retrieval", response_model=RetrievalStageResult)
async def debug_retrieval(
    q: str = Query(..., description="Search query"),
    collection: str = Query("", description="Qdrant collection (uses default if empty)"),
) -> RetrievalStageResult:
    """Run the full retrieval pipeline and return scores at each stage.

    No LLM calls are made. This endpoint is for debugging and threshold
    calibration only.
    """
    s = get_settings()
    col = collection or s.qdrant_collection
    t0 = time.perf_counter()
    stages: dict[str, Any] = {}

    # ── Stage 1: Vector search ────────────────────────────────────────────
    vector_hits: list[dict] = []
    try:
        from ...tools.qdrant_search import qdrant_search

        vector_hits = qdrant_search.invoke(
            {"query": q, "collection": col, "limit": s.qdrant_top_k, "offset": 0}
        )
        stages["vector"] = {
            "count": len(vector_hits),
            "top_scores": _top_scores(vector_hits, "score"),
        }
    except Exception as exc:
        logger.warning("debug/retrieval vector stage failed: %s", exc)
        stages["vector"] = {"count": 0, "top_scores": [], "error": str(exc)}

    # ── Stage 2: BM25 search ─────────────────────────────────────────────
    # BM25 operates on a document list — feed it the vector hits so it can
    # re-score them lexically (same pattern as the search agent pipeline).
    bm25_hits: list[dict] = []
    try:
        from ...tools.bm25_search import bm25_search

        if vector_hits:
            bm25_hits = bm25_search.invoke(
                {"query": q, "documents": vector_hits, "top_k": s.bm25_top_k}
            )
        stages["bm25"] = {
            "count": len(bm25_hits),
            "top_scores": _top_scores(bm25_hits, "bm25_score"),
        }
    except Exception as exc:
        logger.warning("debug/retrieval bm25 stage failed: %s", exc)
        stages["bm25"] = {"count": 0, "top_scores": [], "error": str(exc)}

    # ── Stage 3: Graph search ────────────────────────────────────────────
    graph_sections: list[dict] = []
    graph_entities: list[dict] = []
    try:
        from ...tools.neo4j_query import graph_entity_lookup, graph_section_search

        graph_sections = graph_section_search.invoke({"keywords": q, "limit": 5})
        graph_entities = graph_entity_lookup.invoke({"text": q, "label": "", "limit": 5})
        stages["graph"] = {
            "count": len(graph_sections) + len(graph_entities),
            "section_count": len(graph_sections),
            "entity_count": len(graph_entities),
        }
    except Exception as exc:
        logger.warning("debug/retrieval graph stage failed: %s", exc)
        stages["graph"] = {
            "count": 0,
            "section_count": 0,
            "entity_count": 0,
            "error": str(exc),
        }

    # ── Stage 4: Merge and deduplicate ───────────────────────────────────
    all_hits = list(vector_hits)
    seen_ids = {h.get("id") for h in all_hits if h.get("id")}

    for h in bm25_hits:
        hid = h.get("id")
        if hid and hid not in seen_ids:
            seen_ids.add(hid)
            all_hits.append(h)

    # Add graph section text as pseudo-hits for reranking
    for sec in graph_sections:
        text = sec.get("text", "")
        if text:
            all_hits.append({
                "id": sec.get("section_id", "graph"),
                "score": 0,
                "content": text,
                "metadata": sec,
            })

    total_before = len(vector_hits) + len(bm25_hits) + len(graph_sections)
    stages["merged"] = {
        "count": total_before,
        "after_dedup": len(all_hits),
    }

    # ── Stage 5: Reranking ───────────────────────────────────────────────
    try:
        from ...tools.reranker import reranker

        reranked = reranker.invoke(
            {"query": q, "documents": all_hits, "top_k": s.rerank_top_k}
        )
        stages["reranked"] = {
            "count": len(reranked),
            "scores": [round(float(h.get("rerank_score", 0)), 4) for h in reranked],
            "logits": [round(float(h.get("rerank_logit", 0)), 4) for h in reranked],
        }
    except Exception as exc:
        logger.warning("debug/retrieval rerank stage failed: %s", exc)
        stages["reranked"] = {"count": 0, "scores": [], "logits": [], "error": str(exc)}

    elapsed = round(time.perf_counter() - t0, 3)

    thresholds: dict[str, float] = {
        "min_relevance_score": s.min_relevance_score,
        "search_min_confidence": s.search_min_confidence,
        "qdrant_top_k": float(s.qdrant_top_k),
        "rerank_top_k": float(s.rerank_top_k),
    }

    return RetrievalStageResult(
        query_original=q,
        query_for_embedding=q,
        collection=col,
        stages=stages,
        thresholds=thresholds,
        elapsed_s=elapsed,
    )
