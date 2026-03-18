# Phase 5: Production Hardening - Research

**Researched:** 2026-03-18
**Domain:** Integration testing, Docker production config, health endpoints, CI tooling
**Confidence:** HIGH

## Summary

Phase 5 is the final milestone phase. Its goal is making the system deployment-ready: integration tests for all agents, a Makefile for standard operations, production Docker configuration, complete environment documentation, enhanced health checks, and final eval metrics baseline.

The codebase already has significant infrastructure in place: a working `docker-compose.yml` with health checks for all services, a basic `Makefile` (test/lint/build/up/down), a `.env.example` with most variables, a `/health` liveness endpoint and `/health/detailed` readiness probe (Qdrant + Neo4j + Redis), pytest + httpx + pytest-asyncio in dev dependencies, and one existing unit test (`test_analyze_classify.py`). The eval suite (`tests/eval/run_eval.py`) is complete with 49 queries.

**Primary recommendation:** Build integration tests using `httpx.AsyncClient` with FastAPI's `TestClient` (ASGI transport) for tests that can mock external services, and a separate `conftest.py` with real-service fixtures for true integration tests. Extend the existing Makefile and `.env.example` rather than replacing them. The `/health/detailed` endpoint already checks Qdrant, Neo4j, Redis -- add Ollama connectivity check. Create `docker-compose.prod.yml` as an override file that layers production settings on top of the base compose.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FR-6 | Integration tests: pytest suite covers happy path for each agent, CI-ready via `make test` | Integration test patterns, conftest fixtures, httpx AsyncClient approach |
| FR-7 | Production Docker config: resource limits, health checks, restart policies, volume mounts | docker-compose.prod.yml override pattern, deploy block syntax |
| NFR-1 | Stability: system does not crash on LLM timeout | Covered by Phase 4 graceful degradation; integration tests verify it |
| NFR-2 | Latency: search/analyze/verify P95 < 30s, generate P95 < 90s | Eval suite captures latency metrics |
| NFR-3 | Privacy: all data on-premise | docker-compose.prod.yml keeps all services local |
| NFR-4 | Maintainability: configs in Settings, documented env vars | .env.example completion |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | >=8.0 | Test framework | Already in pyproject.toml dev deps |
| pytest-asyncio | >=0.23.0 | Async test support | Already in pyproject.toml dev deps |
| httpx | >=0.27.0 | Async HTTP test client | Already in requirements.txt + dev deps |
| ruff | (latest) | Linting | Already used via `make lint` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| respx | >=0.21 | Mock httpx calls to external services (Ollama, OCR) | Integration tests that need to stub LLM/OCR responses |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| respx | unittest.mock.patch | respx is purpose-built for httpx mocking, cleaner for HTTP stubs |
| TestClient (sync) | httpx.AsyncClient(transport=ASGITransport) | AsyncClient is needed since graph uses async; TestClient wraps it |

**Installation (dev only):**
```bash
pip install respx
```

Note: pytest, pytest-asyncio, httpx are already declared in `pyproject.toml [project.optional-dependencies] dev`.

## Architecture Patterns

### Recommended Test Structure
```
tests/
├── conftest.py              # Shared fixtures, settings override
├── test_analyze_classify.py # Existing unit test (keep)
├── integration/
│   ├── __init__.py
│   ├── conftest.py          # Integration-specific fixtures (mock Ollama responses)
│   ├── test_search.py       # search agent happy path
│   ├── test_analyze.py      # analyze qa happy path
│   ├── test_verify.py       # verify agent happy path
│   ├── test_generate.py     # generate agent happy path
│   └── test_ingest.py       # ingest agent happy path
├── eval/
│   ├── dataset.json         # Existing (49 queries)
│   └── run_eval.py          # Existing eval runner
```

### Pattern 1: FastAPI TestClient with Mocked External Services
**What:** Use FastAPI's built-in TestClient (backed by httpx) to call endpoints. Mock Ollama/OCR responses so tests don't depend on a running LLM.
**When to use:** Integration tests that verify the full HTTP -> router -> graph -> agent pipeline.
**Example:**
```python
# tests/integration/conftest.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

@pytest.fixture
def client():
    """Create test client with mocked external services."""
    # Override settings to point to test-safe URLs
    from app.core.config import Settings
    test_settings = Settings(
        redis_url="redis://localhost:6379",
        qdrant_url="http://localhost:6333",
        neo4j_uri="bolt://localhost:7687",
        neo4j_password="password",
        postgres_dsn="postgresql://postgres:postgres@localhost:5432/agent_memory",
        mlflow_enabled=False,
        ollama_url="http://localhost:11434",
    )
    with patch("app.core.config.get_settings", return_value=test_settings):
        from app.main import create_app
        app = create_app()
        with TestClient(app) as c:
            yield c
```

### Pattern 2: Ollama Response Mocking
**What:** Patch `langchain_ollama.ChatOllama.invoke` to return canned responses, so integration tests run without a live LLM.
**When to use:** All integration tests that trigger LLM calls.
**Example:**
```python
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage

def mock_ollama_response(content: str):
    """Create a mock that returns a fixed AIMessage."""
    mock = MagicMock()
    mock.invoke.return_value = AIMessage(content=content)
    return mock
```

### Pattern 3: docker-compose.prod.yml as Override
**What:** Use docker compose `-f docker-compose.yml -f docker-compose.prod.yml` overlay pattern. The prod file only adds/overrides production-specific settings (resource limits, restart policies) without duplicating the full service definitions.
**When to use:** Production deployment.
**Example:**
```yaml
# docker-compose.prod.yml
version: "3.9"
services:
  langgraph-agent:
    deploy:
      resources:
        limits:
          cpus: "4.0"
          memory: 4G
        reservations:
          cpus: "1.0"
          memory: 2G
    restart: always
    environment:
      APP_ENV: prod
      LOG_FORMAT: json
```

### Anti-Patterns to Avoid
- **Running integration tests against live Ollama in CI:** LLM responses are non-deterministic and slow. Mock the LLM layer; use eval suite for live testing.
- **Duplicating full docker-compose in prod file:** Use override/merge pattern instead -- only specify deltas.
- **Hardcoding test URLs:** Use fixtures and env overrides for all service URLs.
- **Testing agent internals directly:** Integration tests should call HTTP endpoints, not import agent functions (except for unit tests like the existing classify test).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP mocking | Custom request interceptor | `respx` or `unittest.mock.patch` on httpx/langchain | Battle-tested, handles edge cases |
| Health check logic | Custom TCP probes | Docker HEALTHCHECK + `/health/detailed` endpoint | Standard docker health pattern |
| Makefile task runner | Shell scripts | GNU Make | Universal, already in use |
| Env documentation | Manual tracking | Generate from `Settings` class fields | Single source of truth |

## Common Pitfalls

### Pitfall 1: get_settings() LRU Cache in Tests
**What goes wrong:** `get_settings()` uses `@lru_cache`, so patching `Settings` after first call has no effect.
**Why it happens:** The cache returns the original settings instance.
**How to avoid:** Clear the cache before each test: `get_settings.cache_clear()` in fixture setup.
**Warning signs:** Tests pass individually but fail when run together.

### Pitfall 2: Docker Compose Deploy Block Requires Swarm Mode
**What goes wrong:** `deploy.resources.limits` in docker-compose v3 is only enforced in Swarm mode. With `docker compose up`, limits are silently ignored.
**Why it happens:** Docker Compose v3 spec design decision.
**How to avoid:** Use `docker compose --compatibility up` which translates deploy limits to container-level `--memory` and `--cpus` flags. Or use compose v2-style `mem_limit`/`cpus` keys. Document the flag in Makefile.
**Warning signs:** Container uses unlimited resources despite limits in compose file.

### Pitfall 3: Event Loop Conflicts in Async Tests
**What goes wrong:** pytest-asyncio and FastAPI's TestClient can conflict on event loop ownership.
**Why it happens:** TestClient runs its own sync-to-async loop internally.
**How to avoid:** Use sync `TestClient` (not `httpx.AsyncClient`) for endpoint tests. The TestClient handles async internally. Only use `@pytest.mark.asyncio` for tests that directly `await` async functions.
**Warning signs:** `RuntimeError: This event loop is already running`.

### Pitfall 4: .env.example Out of Sync with Settings
**What goes wrong:** New settings added in Phase 1-4 (search_min_confidence, rerank_candidate_pool, etc.) are not in `.env.example`.
**Why it happens:** Settings class grew across 4 phases; `.env.example` was not updated.
**How to avoid:** Systematically compare `Settings` class fields with `.env.example` entries and add all missing ones.
**Warning signs:** New deployment fails because required env var is undocumented.

### Pitfall 5: Health Endpoint Missing Ollama Check
**What goes wrong:** `/health/detailed` checks Qdrant, Neo4j, Redis but not Ollama -- the most critical dependency.
**Why it happens:** Ollama is accessed via langchain-ollama, not a direct HTTP client.
**How to avoid:** Add a simple `httpx.get(f"{ollama_url}/api/tags")` check to the health endpoint.
**Warning signs:** Health shows "ok" while Ollama is down, requests fail with timeout.

## Code Examples

### Integration Test: Search Agent Happy Path
```python
# tests/integration/test_search.py
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage

def test_search_happy_path(client):
    """POST /api/v1/chat/ with a search query returns 200 with response."""
    # Mock the LangGraph graph.invoke to return a known state
    mock_state = {
        "intent": "search",
        "intents": ["search"],
        "final_response": "Ответ на ваш вопрос...",
        "citations": [{"filename": "test.pdf", "snippet": "..."}],
        "verify_result": {},
        "generate_result": {},
        "analyze_result": {},
        "retrieval_metrics": {"node": "search", "merged_hits": 5},
        "export_path": None,
        "session_id": "test-session",
    }
    with patch("app.api.v1.chat.graph") as mock_graph:
        mock_graph.invoke.return_value = mock_state
        resp = client.post("/api/v1/chat/", json={
            "query": "Что такое овердрафт?",
            "session_id": "test-session",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "search"
    assert len(body["response"]) > 0
```

### Health Endpoint with Ollama Check
```python
# Addition to /health/detailed in app/main.py
# Ollama
try:
    import httpx as _httpx
    r = _httpx.get(f"{s.ollama_url}/api/tags", timeout=3)
    r.raise_for_status()
    checks["ollama"] = "ok"
except Exception as exc:
    checks["ollama"] = f"error: {exc}"
```

### Makefile Additions
```makefile
# Phase 5 additions to existing Makefile
.PHONY: eval docker-up docker-down

eval:
	$(PYTHON) tests/eval/run_eval.py

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-prod-up:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml --compatibility up -d

docker-prod-down:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

### pytest Configuration (pyproject.toml)
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "integration: marks tests requiring running infrastructure",
]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| docker-compose v2 mem_limit | v3 deploy.resources (Swarm) or --compatibility flag | Docker Compose v2.x | Use --compatibility for non-Swarm |
| pytest-asyncio auto mode off by default | asyncio_mode = "auto" is standard | pytest-asyncio 0.21+ | Explicit marker no longer needed per-test |
| Manual health checks | `/health` (liveness) + `/health/detailed` (readiness) pattern | K8s standard | Two-tier health is production standard |

## Existing Infrastructure Inventory

Critical for planning -- what already exists vs. what needs to be created:

| Asset | Status | Location | Action Needed |
|-------|--------|----------|---------------|
| Makefile | EXISTS, basic | `langgraph-agent/Makefile` | Extend with eval, docker-prod-up, docker-prod-down |
| .env.example | EXISTS, incomplete | `langgraph-agent/.env.example` | Add ~15 missing vars from Settings class |
| docker-compose.yml | EXISTS, complete | `langgraph-agent/docker-compose.yml` | Base is fine; create prod overlay |
| /health | EXISTS | `app/main.py` line 117 | Liveness probe -- keep as-is |
| /health/detailed | EXISTS, partial | `app/main.py` line 120 | Add Ollama check |
| Dockerfile | EXISTS | `langgraph-agent/Dockerfile` | Already has HEALTHCHECK -- adequate |
| pytest dev deps | EXISTS | `pyproject.toml` | Add pytest config section |
| tests/ | EXISTS, minimal | `tests/test_analyze_classify.py` + `tests/eval/` | Add `tests/integration/` |
| Eval suite | EXISTS, complete | `tests/eval/run_eval.py` + `dataset.json` (49 queries) | Run final baseline, document v1.0 metrics |

### Missing Settings in .env.example

Settings class fields NOT in current `.env.example`:
- `OLLAMA_GENERATE_DRAFT_MODEL` -- bulk generation model
- `OLLAMA_MAX_RETRIES` -- tenacity retries
- `RERANK_CANDIDATE_POOL` -- cross-encoder candidate pool size
- `RERANK_CANDIDATE_MULTIPLIER` -- legacy multiplier
- `SEARCH_MIN_CONFIDENCE` -- rerank score threshold (calibrated in Phase 3)
- `GRAPH_ENRICHMENT_TIMEOUT` -- Neo4j enrichment timeout
- `SUPERVISOR_NUM_PREDICT` -- supervisor LLM token limit
- `VERIFY_PASTED_DOC_THRESHOLD` -- char threshold for pasted docs
- `QDRANT_NAMED_VECTOR` -- named vector key
- `NEO4J_FULLTEXT_INDEX` -- fulltext index name
- `PG_POOL_MIN_SIZE`, `PG_POOL_MAX_SIZE`, `PG_THREAD_WORKERS` -- PG pool tuning
- `OCR_SERVICE_URL` -- OCR service base URL
- `MAX_UPLOAD_SIZE_MB` -- upload limit
- `QDRANT_MAX_SCAN_BATCHES` -- scan limit
- `SSE_WORD_CHUNK_SIZE` -- streaming chunk size
- `ANALYZE_SUMMARY_LIMIT`, `ANALYZE_COMPARE_LIMIT` -- analyze agent tuning
- `MEMORY_MAX_HISTORY` -- history loading limit
- `GRAPH_POOL_WORKERS` -- graph query thread pool
- `MINIO_PUBLIC_ENDPOINT` -- public-facing MinIO URL

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >=8.0 + pytest-asyncio >=0.23.0 |
| Config file | `pyproject.toml` [tool.pytest.ini_options] -- needs creation (Wave 0) |
| Quick run command | `python -m pytest tests/ -v --ignore=tests/eval -x` |
| Full suite command | `python -m pytest tests/ -v --ignore=tests/eval` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FR-6-search | Search agent returns response for search query | integration | `pytest tests/integration/test_search.py -x` | Wave 0 |
| FR-6-analyze | Analyze agent returns response for QA query | integration | `pytest tests/integration/test_analyze.py -x` | Wave 0 |
| FR-6-verify | Verify agent returns structured result | integration | `pytest tests/integration/test_verify.py -x` | Wave 0 |
| FR-6-generate | Generate agent returns document plan | integration | `pytest tests/integration/test_generate.py -x` | Wave 0 |
| FR-6-ingest | Ingest endpoint accepts file upload | integration | `pytest tests/integration/test_ingest.py -x` | Wave 0 |
| FR-7-health | /health/detailed checks all deps including Ollama | integration | `pytest tests/integration/test_health.py -x` | Wave 0 |
| FR-7-docker | docker-compose.prod.yml starts without errors | smoke | `docker compose -f ... config --quiet` | manual |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/ -v --ignore=tests/eval -x`
- **Per wave merge:** `python -m pytest tests/ -v --ignore=tests/eval`
- **Phase gate:** Full suite green + eval suite run with documented metrics

### Wave 0 Gaps
- [ ] `pyproject.toml` [tool.pytest.ini_options] section -- pytest configuration
- [ ] `tests/integration/__init__.py` -- package marker
- [ ] `tests/integration/conftest.py` -- shared fixtures (mock Ollama, test client)

## Open Questions

1. **Mock depth for integration tests**
   - What we know: LLM responses are non-deterministic; mocking graph.invoke is simplest but tests less code
   - What's unclear: Should we mock at graph level or at individual agent's LLM call level?
   - Recommendation: Mock at `graph.invoke` level for basic integration tests (verifies HTTP layer + response serialization). More granular mocking adds complexity without proportional value for happy-path tests.

2. **Ingest test without OCR service**
   - What we know: Ingest endpoint proxies to an external OCR service (`ocr_service_url`)
   - What's unclear: Is OCR service available in test environment?
   - Recommendation: Mock the httpx call to OCR service in the ingest test. Test the endpoint validation (file type, size) with real calls; proxy behavior with mocked OCR response.

3. **Eval suite live run timing**
   - What we know: Eval requires all services running + documents indexed in Qdrant
   - What's unclear: Whether the system will be fully deployed when Phase 5 executes
   - Recommendation: Document the eval run command and expected metrics thresholds. If live run is not possible, create a `METRICS-V1.md` template with target thresholds and mark as "pending live deployment".

## Sources

### Primary (HIGH confidence)
- Codebase analysis: `app/main.py`, `app/core/config.py`, `docker-compose.yml`, `Makefile`, `.env.example`, `pyproject.toml`, `Dockerfile`
- Existing test: `tests/test_analyze_classify.py` -- confirms pytest patterns in use
- Existing eval: `tests/eval/run_eval.py` -- complete eval runner with httpx

### Secondary (MEDIUM confidence)
- Docker Compose v3 deploy spec -- `deploy.resources` behavior with/without Swarm
- pytest-asyncio auto mode -- standard since 0.21

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already in project, just extending usage
- Architecture: HIGH -- patterns derived from existing codebase analysis
- Pitfalls: HIGH -- identified from actual code (get_settings cache, compose deploy block, missing Ollama health check)

**Research date:** 2026-03-18
**Valid until:** 2026-04-18 (stable domain, no fast-moving dependencies)
