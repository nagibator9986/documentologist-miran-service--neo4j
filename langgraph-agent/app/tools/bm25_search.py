"""Tool: BM25 full-text search over a list of documents."""
from __future__ import annotations

import logging
import re
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
