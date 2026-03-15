"""Central LLM and embeddings factory — single point to swap backends."""
from __future__ import annotations

import logging
import threading
import time
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
# Circuit breaker — prevents request pile-up when Ollama is down
# ──────────────────────────────────────────────────────────────────────────────

class _CircuitBreaker:
    """Thread-safe circuit breaker for Ollama calls.

    States:
        CLOSED    — normal operation; all calls pass through.
        OPEN      — Ollama unreachable; calls fail fast until recovery_timeout.
        HALF_OPEN — one trial call allowed to probe Ollama health.

    Transitions:
        CLOSED → OPEN      : failure_threshold consecutive failures.
        OPEN   → HALF_OPEN : recovery_timeout seconds elapsed since opening.
        HALF_OPEN → CLOSED : trial call succeeds.
        HALF_OPEN → OPEN   : trial call fails (resets the timeout).
    """

    _CLOSED = "closed"
    _OPEN = "open"
    _HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failures = 0
        self._state = self._CLOSED
        self._opened_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        """Return True if calls should be short-circuited (no Ollama call)."""
        with self._lock:
            if self._state == self._OPEN:
                if time.monotonic() - self._opened_at >= self._recovery_timeout:
                    self._state = self._HALF_OPEN
                    logger.info("CircuitBreaker → HALF_OPEN: allowing trial call to Ollama")
                    return False  # allow the trial
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state != self._CLOSED:
                logger.info("CircuitBreaker → CLOSED: Ollama is healthy again")
            self._failures = 0
            self._state = self._CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == self._HALF_OPEN or self._failures >= self._failure_threshold:
                self._opened_at = time.monotonic()
                self._state = self._OPEN
                logger.error(
                    "CircuitBreaker → OPEN after %d failures — "
                    "fast-failing requests for %.0fs",
                    self._failures, self._recovery_timeout,
                )


_circuit_breaker = _CircuitBreaker()


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
    """Call llm.invoke(messages) with circuit breaker + exponential-backoff retry.

    Short-circuits immediately when the circuit is OPEN (Ollama is down).
    Records success/failure to drive circuit breaker state transitions.
    Returns the content string, or empty string on repeated failure.
    """
    if _circuit_breaker.is_open:
        logger.warning("invoke_with_retry: circuit OPEN — skipping Ollama call")
        return ""

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
        result = _call() or ""
        _circuit_breaker.record_success()
        return result
    except RetryError as exc:
        logger.error("LLM invoke exhausted %d retries: %s", attempts, exc)
        _circuit_breaker.record_failure()
        return ""
    except Exception as exc:
        logger.error("LLM invoke failed (non-retryable): %s", exc)
        _circuit_breaker.record_failure()
        return ""
