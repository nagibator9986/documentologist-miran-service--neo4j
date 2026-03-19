"""Tool: BM25 full-text search over a list of documents."""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Tokenizer: split on any non-word character (handles punctuation and Cyrillic correctly)
_TOKEN_RE = re.compile(r"\W+")


def _tokenize(text: str) -> list[str]:
    """Lowercase and split text into non-empty tokens on word boundaries."""
    return [tok for tok in _TOKEN_RE.split(text.lower()) if tok]


@tool
def bm25_search(query: str, documents: list[dict[str, Any]], top_k: int = 20) -> list[dict[str, Any]]:
    """BM25 full-text search over a document list.

    Args:
        query: Search query string.
        documents: List of dicts with at least a 'content' key.
        top_k: Maximum results to return.

    Returns:
        Ranked list of matching documents with bm25_score.
    """
    try:
        from rank_bm25 import BM25Okapi
    except ImportError as exc:
        raise RuntimeError("BM25 requires `rank-bm25`. Install: pip install rank-bm25") from exc

    if not documents:
        return []

    tokenized_corpus = [_tokenize(doc.get("content", "")) for doc in documents]
    # BM25Okapi may fail on all-empty corpus; replace empty token lists with a placeholder
    tokenized_corpus = [toks if toks else ["__empty__"] for toks in tokenized_corpus]

    bm25 = BM25Okapi(tokenized_corpus)
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    scores = bm25.get_scores(query_tokens)

    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
    results = []
    for idx, score in indexed:
        if score > 0:
            hit = dict(documents[idx])
            hit["bm25_score"] = round(float(score), 4)
            results.append(hit)

    logger.debug("bm25_search '%s' → %d hits", query, len(results))
    return results


class _CorpusBM25Index:
    """In-memory BM25 index over the full Qdrant collection with TTL-based refresh.

    Singleton — one instance per process. Thread-safe via a lock.
    """

    _TTL: float = 300.0  # rebuild at most once per 5 minutes

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._corpus: list[dict[str, Any]] = []
        self._bm25: Any = None
        self._built_at: float = 0.0

    def _stale(self) -> bool:
        return time.monotonic() - self._built_at > self._TTL

    def _rebuild(self, chunks: list[dict[str, Any]]) -> None:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError as exc:
            raise RuntimeError("corpus_bm25_search requires `rank-bm25`. Install: pip install rank-bm25") from exc
        corpus = [_tokenize(c.get("content", "")) for c in chunks]
        corpus = [t or ["__empty__"] for t in corpus]
        self._corpus = chunks
        self._bm25 = BM25Okapi(corpus)
        self._built_at = time.monotonic()
        logger.info("CorpusBM25: index rebuilt — %d chunks", len(chunks))

    def search(self, query: str, top_k: int, loader: Any) -> list[dict[str, Any]]:
        with self._lock:
            if self._stale() or not self._corpus:
                try:
                    chunks = loader()
                except Exception as exc:
                    logger.error("CorpusBM25: failed to load corpus: %s", exc)
                    return []
                if not chunks:
                    logger.debug("CorpusBM25: corpus is empty (0 chunks loaded)")
                    return []
                self._rebuild(chunks)
            if not self._bm25:
                return []
            tokens = _tokenize(query)
            if not tokens:
                return []
            scores = self._bm25.get_scores(tokens)
            indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
            max_score = scores[indexed[0][0]] if indexed else 0.0
            results: list[dict[str, Any]] = []
            for idx, raw_score in indexed:
                if raw_score <= 0:
                    break
                hit = dict(self._corpus[idx])
                hit["bm25_score"] = round(float(raw_score), 4)
                # Normalize to 0–0.6 (below strong vector hits, above relevance filter)
                hit["score"] = round(min(0.6, raw_score / max_score * 0.6), 4) if max_score > 0 else 0.3
                results.append(hit)
            logger.debug("CorpusBM25.search '%s' → %d hits", query, len(results))
            return results


_corpus_index = _CorpusBM25Index()


def corpus_bm25_search(
    query: str,
    top_k: int = 20,
    collection: str = "",
) -> list[dict[str, Any]]:
    """BM25 search over the entire Qdrant corpus (catches what vector search misses).

    On first call loads all chunks from Qdrant; result is cached with 5-minute TTL.

    Args:
        query: Search query string.
        top_k: Maximum results to return.
        collection: Qdrant collection name (uses default if empty).

    Returns:
        Ranked hit dicts with score normalised to 0–0.6.
    """
    from .qdrant_search import scroll_all_chunks

    def _loader() -> list[dict[str, Any]]:
        return scroll_all_chunks(collection=collection)

    return _corpus_index.search(query, top_k=top_k, loader=_loader)
