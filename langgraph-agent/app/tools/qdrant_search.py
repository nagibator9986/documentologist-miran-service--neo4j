"""Tool: semantic vector search via Qdrant."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from qdrant_client.http.exceptions import UnexpectedResponse

from ..core.config import get_settings
from ..core.llm import get_embeddings
from ..core.utils import get_qdrant_client

logger = logging.getLogger(__name__)


def _embed_query(text: str) -> list[float]:
    return get_embeddings().embed_query(text)


@tool
def qdrant_search(
    query: str,
    collection: str = "",
    limit: int = 10,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Semantic search in Qdrant vector store.

    Args:
        query: Natural language search query.
        collection: Qdrant collection name (uses default if empty).
        limit: Maximum number of results to return.
        offset: Number of results to skip (for pagination).

    Returns:
        List of hits with score, content, and metadata.
    """
    s = get_settings()
    col = collection or s.qdrant_collection
    client = get_qdrant_client()

    vector = _embed_query(query)

    # Try named vector first (dual-vector collections from bank_knowledge)
    try:
        response = client.query_points(
            collection_name=col,
            query=vector,
            using="q_vec",
            limit=limit,
            offset=offset,
            with_payload=True,
        )
        results = response.points
        logger.debug("qdrant_search used named vector 'q_vec' for collection '%s'", col)
    except (UnexpectedResponse, Exception) as exc:
        # Named vector not available — fall back to default vector
        logger.debug("qdrant_search 'q_vec' unavailable (%s), trying default vector", type(exc).__name__)
        try:
            response = client.query_points(
                collection_name=col,
                query=vector,
                limit=limit,
                offset=offset,
                with_payload=True,
            )
            results = response.points
            logger.debug("qdrant_search used default vector for collection '%s'", col)
        except Exception as exc2:
            logger.error(
                "qdrant_search failed for collection='%s' query='%.80s': %s",
                col, query, exc2,
            )
            return []

    hits = []
    for r in results:
        payload = r.payload or {}
        hits.append({
            "id": str(r.id),
            "score": r.score,
            "content": payload.get("answer") or payload.get("text", ""),
            "question": payload.get("question", ""),
            "section": payload.get("section_title", ""),
            "metadata": payload,
        })
    logger.debug("qdrant_search '%s' → %d hits (offset=%d)", query, len(hits), offset)
    return hits


@tool
def qdrant_text_search(
    text: str,
    collection: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Exact substring search across all Qdrant chunks.

    Uses server-side MatchText payload filter first (fast), falls back to
    Python-side scroll scan only when filter is unavailable.

    Args:
        text: Exact substring to search for (case-insensitive).
        collection: Qdrant collection name (uses default if empty).
        limit: Maximum number of matching chunks to return.

    Returns:
        List of matching chunks with score=1.0 and full metadata.
    """
    from qdrant_client import models as qmodels

    s = get_settings()
    col = collection or s.qdrant_collection
    client = get_qdrant_client()

    search_lower = text.strip().lower()
    if not search_lower:
        return []

    def _to_hit(r: Any) -> dict[str, Any]:
        payload = r.payload or {}
        content = payload.get("answer") or payload.get("text", "")
        return {
            "id": str(r.id),
            "score": 1.0,
            "content": content,
            "question": payload.get("question", ""),
            "section": payload.get("section_title", ""),
            "metadata": payload,
        }

    # ── Strategy 1: server-side MatchText filter (no full collection scan) ────
    found: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for field in ("answer", "text"):
        if len(found) >= limit:
            break
        try:
            records, _ = client.scroll(
                collection_name=col,
                scroll_filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(
                        key=field,
                        match=qmodels.MatchText(text=search_lower),
                    )]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            for r in records:
                rid = str(r.id)
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    found.append(_to_hit(r))
        except Exception as exc:
            logger.debug(
                "qdrant_text_search server filter on '%s' failed (%s) — will use scan fallback",
                field, type(exc).__name__,
            )

    if found:
        logger.debug("qdrant_text_search (server filter) '%s' → %d matches", text[:60], len(found))
        return found[:limit]

    # ── Strategy 2: Python-side full-scan fallback ────────────────────────────
    # Hard cap: scan at most _MAX_SCAN_BATCHES × 200 chunks (= 10 000 max).
    # Prevents unbounded O(n) scans on large collections if server-side filter
    # is unavailable (e.g. index not created yet).
    _MAX_SCAN_BATCHES = 50
    logger.warning(
        "qdrant_text_search: server filter returned nothing, falling back to full scan "
        "(capped at %d batches × 200 = %d chunks)",
        _MAX_SCAN_BATCHES, _MAX_SCAN_BATCHES * 200,
    )
    pg_offset = None
    batches_done = 0
    while len(found) < limit and batches_done < _MAX_SCAN_BATCHES:
        try:
            records, next_offset = client.scroll(
                collection_name=col,
                limit=200,
                offset=pg_offset,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            logger.error("qdrant_text_search scroll failed: %s", exc)
            break

        batches_done += 1
        for r in records:
            payload = r.payload or {}
            content = payload.get("answer") or payload.get("text", "")
            if search_lower in content.lower():
                rid = str(r.id)
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    found.append(_to_hit(r))
                if len(found) >= limit:
                    break

        if not records or next_offset is None or len(found) >= limit:
            break
        pg_offset = next_offset

    if batches_done >= _MAX_SCAN_BATCHES:
        logger.warning(
            "qdrant_text_search: scan cap reached (%d batches) — results may be incomplete",
            _MAX_SCAN_BATCHES,
        )
    logger.debug(
        "qdrant_text_search (fallback scan) '%s' → %d matches in %d batches",
        text[:60], len(found), batches_done,
    )
    return found
