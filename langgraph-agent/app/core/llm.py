"""Central LLM and embeddings factory — single point to swap backends."""
from __future__ import annotations

import logging
from functools import lru_cache

from langchain_ollama import ChatOllama, OllamaEmbeddings
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Retry decorator for Ollama calls
# Retries on connection errors and timeouts with exponential backoff.
# ──────────────────────────────────────────────────────────────────────────────

def _ollama_retry(max_retries: int | None = None):
    """Return a tenacity retry decorator configured from settings."""
    s = get_settings()
    attempts = max_retries if max_retries is not None else s.ollama_max_retries
    return retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
        before_sleep=lambda rs: logger.warning(
            "Ollama call failed (attempt %d/%d), retrying in %.1fs…",
            rs.attempt_number, attempts, rs.next_action.sleep,  # type: ignore[union-attr]
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
# LLM factories
# ──────────────────────────────────────────────────────────────────────────────

def get_llm(*, temperature: float | None = None, num_predict: int | None = None) -> ChatOllama:
    """Return a ChatOllama instance with project defaults and retry wrapper."""
    s = get_settings()
    llm = ChatOllama(
        base_url=s.ollama_url,
        model=s.ollama_model,
        temperature=temperature if temperature is not None else s.temperature,
        num_predict=num_predict or s.max_tokens_response,
        timeout=s.ollama_timeout,
    )
    return llm


def get_json_llm(*, num_predict: int | None = None) -> ChatOllama:
    """Return a ChatOllama instance that forces JSON output."""
    s = get_settings()
    llm = ChatOllama(
        base_url=s.ollama_url,
        model=s.ollama_model,
        temperature=0.0,
        num_predict=num_predict or 1024,
        format="json",
        timeout=s.ollama_timeout,
    )
    return llm


# ──────────────────────────────────────────────────────────────────────────────
# Embeddings — cached singleton
# Creating OllamaEmbeddings is cheap, but the underlying HTTP client connection
# should be reused. @lru_cache ensures one instance per unique (url, model) pair.
# ──────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=4)
def _embeddings_singleton(base_url: str, model: str) -> OllamaEmbeddings:
    logger.info("Creating OllamaEmbeddings singleton: model=%s url=%s", model, base_url)
    return OllamaEmbeddings(base_url=base_url, model=model)


def get_embeddings() -> OllamaEmbeddings:
    """Return a cached OllamaEmbeddings instance (one per url+model pair)."""
    s = get_settings()
    return _embeddings_singleton(s.ollama_url, s.ollama_embedding_model)


# ──────────────────────────────────────────────────────────────────────────────
# Retry-wrapped invoke helpers
# Use these in agents instead of llm.invoke() for resilience.
# ──────────────────────────────────────────────────────────────────────────────

def invoke_with_retry(llm: ChatOllama, messages: list, *, max_retries: int | None = None) -> str:
    """Call llm.invoke(messages) with exponential-backoff retry.

    Returns the content string. Falls back to empty string on repeated failure.
    """
    s = get_settings()
    attempts = max_retries if max_retries is not None else s.ollama_max_retries

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=False,
        before_sleep=lambda rs: logger.warning(
            "LLM invoke failed (attempt %d/%d), retrying…", rs.attempt_number, attempts
        ),
    )
    def _call():
        return llm.invoke(messages).content

    try:
        return _call() or ""
    except RetryError as exc:
        logger.error("LLM invoke exhausted %d retries: %s", attempts, exc)
        return ""
    except Exception as exc:
        logger.error("LLM invoke failed (non-retryable): %s", exc)
        return ""
