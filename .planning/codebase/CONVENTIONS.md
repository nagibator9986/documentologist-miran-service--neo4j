# Coding Conventions

Observed across `langgraph-agent` and `ocr-service`, documented from direct source reading.

---

## Language and Runtime

- Python 3.11 minimum (`requires-python = ">=3.11"` in both `pyproject.toml` files).
- All new modules begin with `from __future__ import annotations` to enable postponed evaluation of type hints.

---

## Code Style

### Linter

Both services are linted with **ruff** (configured in `ocr-service/pyproject.toml`):

```
line-length = 100
target-version = "py311"
```

The `langgraph-agent` has no local `ruff` config; it inherits the CI defaults. The CI workflow at `.github/workflows/python-ci.yml` runs `ruff check ocr-service` and `ruff check langgraph-agent` as separate steps.

### Formatting rules (inferred from code)

- Line length cap: 100 characters.
- Single-quoted strings for identifiers; double-quoted where readability demands it.
- One blank line between top-level section blocks within a module; two blank lines between top-level functions/classes.
- Trailing commas in multi-line argument lists and data structures.

---

## Module-Level Documentation

Every production module carries a top-level **module docstring** that describes:

1. The module's single responsibility (one sentence).
2. A numbered pipeline or stage breakdown for non-trivial orchestrators.
3. Design principles or key decisions where relevant.

Examples:

- `langgraph-agent/app/agents/supervisor.py` — three-tier resolution strategy, statelessness rationale.
- `langgraph-agent/app/agents/search_agent.py` — 13-step pipeline with stage names and brief descriptions.
- `langgraph-agent/app/agents/analyze_agent.py` — 10-step pipeline, noting which tasks emit JSON vs. free text.
- `langgraph-agent/app/agents/ingest_agent.py` — flow diagram from upload to search readiness, design notes on dead-code removal.

---

## Function Docstrings

### Langgraph-agent style

Public node functions (graph entry points) use one-line summary docstrings:

```python
def search_node(state: AgentState) -> AgentState:
    """Hybrid RAG pipeline node: vector + BM25 + graph -> rerank -> generate -> cite."""
```

Private helper functions document return values explicitly using `Returns:` blocks:

```python
def _detect_exact_search(query: str) -> tuple[bool, str]:
    """Determine whether the query requests a literal substring match.

    Returns:
        (is_exact, exact_text) — exact_text is the string to search for.
    """
```

Multi-return helpers always name each element of the tuple in the docstring.

Complex utility functions document arguments and return value:

```python
def build_final_response(answer: str, combined_responses: list[str]) -> str:
    """Merge multi-intent responses into a single final_response string.

    Args:
        answer: The current agent's answer.
        combined_responses: Answers accumulated from prior agents in this turn.

    Returns:
        Combined string, or just `answer` if there are no prior responses.
    """
```

### OCR-service style

The OCR service mixes docstring styles:

- Route handlers use inline docstrings (FastAPI OpenAPI documentation).
- Service classes (`SuryaOCRService`) document methods with one-line summaries and inline comments for non-obvious implementation decisions.
- Some short private helpers have no docstring at all (e.g., `_detect_magic_type`, `_table_to_markdown`).

---

## Naming Patterns

### Functions and variables

- `snake_case` throughout.
- Private helpers prefixed with `_`: `_keyword_classify`, `_detect_task`, `_build_llm_prompt`, `_retrieve_graph`.
- Node functions (LangGraph graph entry points) named `<verb>_node`: `search_node`, `analyze_node`, `ingest_node`.
- Factory functions follow `get_<thing>`: `get_settings`, `get_llm`, `get_draft_llm`, `get_json_llm`, `get_qdrant_client`, `get_neo4j_driver`, `get_ocr_service`.
- Boolean variables use `is_` or `has_` prefixes: `is_exact`, `has_context`, `is_duplicate`.

### Constants and module-level data

- Module-level compiled regex: `_ARTICLE_NUM_RE`, `_ENTITY_ORG_RE`, `_COMPARE_STEMS`, `DOC_REF_RE` (exported), `FILENAME_RE` (exported).
- Exported regex use `UPPER_SNAKE_CASE`; private ones `_UPPER_SNAKE_CASE`.
- Intent keyword sets use `frozenset` for O(1) membership testing: `_OBL_KEYWORDS`, `_EXACT_SEARCH_KW`, `_RU_STOPWORDS`.
- Stage-separator comments use the visual pattern `# ── <Stage Name> ────────────────────────────────────────`.
- Thread pool and client singletons are named `_<thing>` with a paired `_<thing>_lock`.

### Classes

- `PascalCase` throughout.
- Settings classes both named `Settings`, extending `pydantic_settings.BaseSettings`.
- State definition: `AgentState` (TypedDict), `RetrievalMetrics` (TypedDict, total=False).

### Type aliases

- `Intent = Literal["ingest", "search", "verify", "generate", "analyze"]`
- `TaskType = Literal["qa", "compare", "extract", "summary"]`

---

## Configuration Pattern

Both services use **Pydantic Settings** with `lru_cache`:

```python
@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- `model_config` uses `env_file=".env"`, `extra="ignore"`, `case_sensitive=False`.
- All settings have sensible defaults; required secrets (passwords, API keys) default to placeholder strings with inline comments indicating they must be overridden.
- Settings are accessed via `get_settings()` call-sites, never via global module-level constants, enabling test overrides through monkeypatching.

---

## Error Handling

### Non-fatal failures

Infrastructure calls that can fail without halting the request are wrapped in bare `except Exception` blocks and degraded gracefully. The pattern is consistent across both services:

```python
try:
    result = external_call(...)
except Exception as exc:
    logger.warning("human-readable context: %s", exc)
    return fallback_value  # [] or "" or None
```

Used for: Neo4j graph enrichment, Qdrant exact-text search, query expansion LLM calls, Neo4j index creation at startup, layout/table OCR predictors.

### Fatal / user-facing failures (OCR service)

The `ocr-service` routes raise `HTTPException` with Russian-language user messages and appropriate HTTP status codes: 400 (bad input), 413 (file too large), 415 (unsupported type), 500 (internal), 502 (upstream storage).

### Retry logic (langgraph-agent)

LLM calls use **tenacity** with exponential backoff configured in `langgraph-agent/app/core/llm.py`:

- Retry on `ConnectionError`, `TimeoutError`, `OSError`.
- `stop_after_attempt(n)` where `n` comes from `settings.ollama_max_retries`.
- `wait_exponential(multiplier=1, min=2, max=15)`.
- `invoke_with_retry()` catches `RetryError` and returns `""` rather than propagating.

### Transaction rollback (OCR service)

The upload route in `ocr-service/app/api/routes.py` implements a careful compensating-action pattern:

1. DB record created first.
2. MinIO upload attempted; on failure: `db.rollback()`, raise 502.
3. DB commit; on failure: `db.rollback()`, `minio.delete_source_file(path)`, raise 500.

This ensures no orphan DB records or orphan MinIO objects.

---

## Logging

### Langgraph-agent

Uses the standard library `logging` module exclusively:

```python
import logging
logger = logging.getLogger(__name__)
```

- `logging.basicConfig` configured in `app/main.py` with format `"%(asctime)s %(levelname)s %(name)s — %(message)s"`.
- Log level from `os.getenv("LOG_LEVEL", "INFO")`.
- `logger.info(...)` for normal pipeline milestones and structured metrics dicts.
- `logger.warning(...)` for non-fatal degradation (graph timeouts, low-confidence search fallbacks, all-hits-below-threshold).
- `logger.debug(...)` for verbose retrieval internals (query expansion, stripped prefix).
- `logger.error(...)` for retry exhaustion or non-retryable LLM failures.
- Structured metrics always logged at INFO as a single dict: `logger.info("search_node metrics: %s", metrics)`.
- Query substrings are always truncated in log messages: `query[:80]`, `raw[:40]`.

### OCR-service

Uses **loguru** (`from loguru import logger`) instead of stdlib `logging`:

```python
from loguru import logger
logger.info("...")
logger.warning("...")
logger.error("...")
```

- Emoji prefixes used in startup/shutdown messages (e.g., `"🚀 Starting …"`, `"✓ DetectionPredictor"`).
- Device detection and model loading progress logged at INFO level with structured context strings.

---

## Thread Safety and Singletons

The double-checked locking pattern is used consistently for all expensive singletons:

```python
_client = None
_lock = threading.Lock()

def get_client():
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = ExpensiveInit()
    return _client
```

Applied to: `QdrantClient` (`utils.py`), `Neo4j GraphDatabase.driver` (`utils.py`), `SuryaOCRService` (`ocr.py`), `ThreadPoolExecutor` for graph queries (`search_agent.py`).

---

## API Structure

Both services follow a consistent FastAPI layout:

```
app/
  main.py          — app factory (create_app()), lifespan hooks, system endpoints
  api/v1/          — route modules, one per domain
  core/
    config.py      — Settings (pydantic-settings), get_settings()
    ...            — other infrastructure helpers
  services/        — business logic, separated from routes
  models/          — SQLAlchemy ORM models (ocr-service)
  schemas/         — Pydantic I/O schemas
```

- Routes use FastAPI `Depends()` for dependency injection (DB sessions, settings).
- CORS and rate limiting (SlowAPI) are applied as middleware in `main.py`.
- Health endpoints always included: `/health` (liveness) and `/health/detailed` (readiness, checks all backends).
- Prometheus metrics exposed at `/metrics` in the OCR service.
- MLflow tracing optionally enabled in the langgraph-agent (zero overhead when disabled).
