"""Tool: rerank documents using a multilingual cross-encoder.

Uses mMARCO-trained cross-encoder that supports Russian and 12 other languages.
Cross-encoders are 30-60x faster than LLM-based reranking on CPU and produce
better relevance scores by jointly encoding query + document pairs.

Scores are normalised to [0, 1] via sigmoid so the UI can display them as
a meaningful percentage without further /10 hacks.
"""
from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Any

from langchain_core.tools import tool

from ..core.config import get_settings

logger = logging.getLogger(__name__)


def _sigmoid(x: float) -> float:
    """Map an unbounded logit score to [0, 1]."""
    return 1.0 / (1.0 + math.exp(-x))


@lru_cache(maxsize=1)
def _get_cross_encoder():
    """Load cross-encoder once and cache it in memory. Try multilingual first."""
    from sentence_transformers import CrossEncoder

    s = get_settings()
    for model_name in (s.reranker_model, s.reranker_fallback_model):
        try:
            logger.info("Loading cross-encoder model %s ...", model_name)
            model = CrossEncoder(model_name, max_length=512)
            logger.info("Cross-encoder loaded: %s", model_name)
            return model
        except Exception as exc:
            logger.warning("Failed to load %s (%s), trying fallback...", model_name, exc)

    raise RuntimeError("No cross-encoder model could be loaded.")


def prewarm_cross_encoder() -> None:
    """Load the cross-encoder model at startup so the first request isn't slow.

    Non-fatal — if loading fails here, the reranker falls back to cosine order
    and the error is already logged inside _get_cross_encoder().
    """
    try:
        _get_cross_encoder()
        logger.info("Cross-encoder pre-warmed successfully.")
    except Exception as exc:
        logger.warning("Cross-encoder pre-warm failed (non-critical): %s", exc)


@tool
def reranker(query: str, documents: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    """Rerank documents by relevance to the query using a multilingual cross-encoder.

    Args:
        query: The user's original query.
        documents: Documents with 'content' field.
        top_k: Number of top results to keep.

    Returns:
        Reranked list of documents (best first) with 'rerank_score' sigmoid-normalised
        to [0, 1] and 'rerank_logit' (raw value for debugging).
        Falls back to original cosine score order if cross-encoder fails.
    """
    if not documents:
        return []

    try:
        model = _get_cross_encoder()
        # 1024 chars instead of 512 — critical for long legal paragraphs where
        # the relevant sentence may be in the second half of a paragraph.
        pairs = [(query, doc.get("content", "")[:get_settings().reranker_max_content]) for doc in documents]
        raw_scores: list[float] = model.predict(pairs).tolist()

        scored = sorted(
            zip(documents, raw_scores),
            key=lambda x: x[1],
            reverse=True,
        )
        result = []
        for doc, logit in scored[:top_k]:
            d = dict(doc)
            d["rerank_logit"] = round(logit, 4)
            # Sigmoid normalisation: UI receives [0,1] — no more /10 hacks needed
            d["rerank_score"] = round(_sigmoid(logit), 4)
            result.append(d)

        logger.debug(
            "reranker '%s' -> top %d/%d, best_score=%.3f (logit=%.2f)",
            query[:60], len(result), len(documents),
            result[0]["rerank_score"] if result else 0,
            result[0]["rerank_logit"] if result else 0,
        )
        return result

    except Exception as exc:
        logger.warning("Cross-encoder reranking failed (%s) -- falling back to cosine order", exc)
        fallback = sorted(documents, key=lambda d: float(d.get("score", 0)), reverse=True)
        result = []
        for doc in fallback[:top_k]:
            d = dict(doc)
            cosine = float(d.get("score", 0))
            d["rerank_logit"] = cosine
            # Cosine scores are already [0, 1] — keep as-is
            d["rerank_score"] = round(min(1.0, max(0.0, cosine)), 4)
            result.append(d)
        return result
