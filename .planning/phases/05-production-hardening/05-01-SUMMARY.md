---
phase: 05-production-hardening
plan: 01
subsystem: testing
tags: [pytest, integration-tests, health-check, httpx, ollama, fastapi, testclient]

# Dependency graph
requires:
  - phase: 04-agent-logic-hardening
    provides: stabilized agent code to test against
provides:
  - integration test infrastructure (conftest with TestClient + mock graph)
  - 11 integration tests covering all 5 agents + health endpoint
  - Ollama connectivity check in /health/detailed
affects: [05-production-hardening]

# Tech tracking
tech-stack:
  added: [pytest, pytest-asyncio, httpx (test dep), slowapi]
  patterns: [sync TestClient with mocked LangGraph graph, get_settings.cache_clear before patching]

key-files:
  created:
    - tests/integration/__init__.py
    - tests/integration/conftest.py
    - tests/integration/test_search.py
    - tests/integration/test_analyze.py
    - tests/integration/test_verify.py
    - tests/integration/test_generate.py
    - tests/integration/test_ingest.py
    - tests/integration/test_health.py
  modified:
    - pyproject.toml
    - app/main.py

key-decisions:
  - "Sync TestClient over async httpx.AsyncClient to avoid event loop conflicts with pytest-asyncio"
  - "Client fixture yields (client, mock_graph) tuple for per-test graph.invoke override"
  - "Patch app.core.utils not app.main for health check mocks (local import in closure)"

patterns-established:
  - "Integration test pattern: conftest provides client fixture with all external deps mocked"
  - "get_settings.cache_clear() before every test to prevent lru_cache poisoning"

requirements-completed: [FR-6, NFR-1]

# Metrics
duration: 6min
completed: 2026-03-18
---

# Phase 5 Plan 1: Integration Test Infrastructure + Ollama Health Check Summary

**11 integration tests (search, analyze, verify, generate, ingest, health) with mocked LangGraph graph + Ollama /health/detailed check via httpx**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-18T10:56:44Z
- **Completed:** 2026-03-18T11:03:09Z
- **Tasks:** 3
- **Files modified:** 10

## Accomplishments
- Pytest infrastructure with asyncio_mode=auto and integration marker
- Shared conftest.py with TestClient fixture + mock_graph_result factory
- 11 passing integration tests covering all 5 agent endpoints + health
- Ollama connectivity check added to /health/detailed (httpx.get to /api/tags)

## Task Commits

Each task was committed atomically:

1. **Task 1: Pytest config + integration test scaffold with conftest** - `afa136c` (feat)
2. **Task 2: Integration tests for all 5 agents + health endpoint** - `495356c` (feat)
3. **Task 3: Add Ollama connectivity check to /health/detailed** - `60b5ad6` (feat)

## Files Created/Modified
- `pyproject.toml` - Added [tool.pytest.ini_options] with testpaths, asyncio_mode, integration marker
- `tests/integration/__init__.py` - Package marker
- `tests/integration/conftest.py` - Shared fixtures: mock_graph_result factory, sync TestClient with mocked graph/settings
- `tests/integration/test_search.py` - Search happy path + empty query 422
- `tests/integration/test_analyze.py` - Analyze QA + compare happy paths
- `tests/integration/test_verify.py` - Verify happy path with verify_result
- `tests/integration/test_generate.py` - Generate happy path with generate_result
- `tests/integration/test_ingest.py` - No-file 422 + PDF upload with mocked OCR proxy
- `tests/integration/test_health.py` - Liveness, detailed all-ok, Ollama-down error
- `app/main.py` - Added Ollama health check block in health_detailed

## Decisions Made
- Used sync TestClient (not httpx.AsyncClient) to avoid event loop conflicts with pytest-asyncio
- Client fixture yields (client, mock_graph) tuple so individual tests can override graph.invoke per-intent
- Patched app.core.utils.get_qdrant_client/get_neo4j_driver (not app.main) because health_detailed uses local import from .core.utils
- httpx.Response in ingest test requires request= parameter for raise_for_status to work

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed health test mock targets**
- **Found during:** Task 2 (test_health.py)
- **Issue:** Patching `app.main.get_qdrant_client` failed because health_detailed imports from .core.utils at call time (local import in closure)
- **Fix:** Changed patch targets to `app.core.utils.get_qdrant_client` and `app.core.utils.get_neo4j_driver`
- **Files modified:** tests/integration/test_health.py
- **Verification:** All health tests pass
- **Committed in:** 495356c (Task 2 commit)

**2. [Rule 1 - Bug] Fixed httpx.Response mock in ingest test**
- **Found during:** Task 2 (test_ingest.py)
- **Issue:** httpx.Response.raise_for_status() requires a request instance set on the response
- **Fix:** Added `request=httpx.Request(...)` parameter to httpx.Response constructor
- **Files modified:** tests/integration/test_ingest.py
- **Verification:** test_ingest_upload_pdf passes
- **Committed in:** 495356c (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes necessary for test correctness. No scope creep.

## Issues Encountered
- Pre-existing failure in tests/test_analyze_classify.py (test_compare) -- _detect_task returns "qa" instead of "compare" for some inputs. Out of scope for this plan; predates our changes.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Integration test infrastructure ready for additional tests
- All 5 agents have happy-path coverage
- Health endpoint now checks all 4 external dependencies (Qdrant, Neo4j, Redis, Ollama)

## Self-Check: PASSED

All 8 created files verified present. All 3 task commits verified in git log.

---
*Phase: 05-production-hardening*
*Completed: 2026-03-18*
