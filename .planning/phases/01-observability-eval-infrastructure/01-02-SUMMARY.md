---
phase: 01-observability-eval-infrastructure
plan: 02
subsystem: testing, api
tags: [eval, dataset, debug, retrieval, fastapi, qdrant, bm25, reranker, neo4j]

# Dependency graph
requires:
  - phase: 01-observability-eval-infrastructure (plan 01)
    provides: MLflow config, structured logging, tier in AgentState
provides:
  - "Eval dataset with 31 curated test queries covering routing, JSON validity, and retrieval"
  - "Debug retrieval endpoint at GET /api/v1/debug/retrieval exposing all pipeline stages"
affects: [01-03-eval-harness, phase-02-json-fix, phase-03-retrieval]

# Tech tracking
tech-stack:
  added: []
  patterns: [debug endpoint pattern for pipeline introspection without LLM cost]

key-files:
  created:
    - tests/eval/__init__.py
    - tests/eval/dataset.json
    - app/api/v1/debug.py
  modified:
    - app/main.py

key-decisions:
  - "BM25 in debug endpoint re-scores vector hits (same as search agent pipeline) since bm25_search requires a documents list input"
  - "Each retrieval stage wrapped in independent try/except for graceful degradation"
  - "Retrieval test expected_docs use placeholder filenames to be calibrated after first eval run"

patterns-established:
  - "Debug endpoint pattern: replicate pipeline stages using direct tool imports, not agent internals"
  - "Eval dataset schema: id, query, expected_intent, expected_docs, expected_json_valid, category, tags"

requirements-completed: [FR-2, FR-4]

# Metrics
duration: 4min
completed: 2026-03-17
---

# Phase 1 Plan 02: Eval Dataset & Debug Retrieval Endpoint Summary

**31-entry Russian eval dataset covering routing/JSON/retrieval categories, plus zero-LLM-cost debug endpoint exposing vector, BM25, graph, merge, and rerank pipeline stages**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-17T09:46:18Z
- **Completed:** 2026-03-17T09:50:38Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Eval dataset with 31 entries: 13 routing, 10 JSON validity, 8 retrieval tests across 5 intents
- Debug retrieval endpoint at GET /api/v1/debug/retrieval?q=... returning all pipeline stage scores
- Debug endpoint uses tools directly (qdrant_search, bm25_search, graph_section_search, graph_entity_lookup, reranker) — no LLM calls

## Task Commits

Each task was committed atomically:

1. **Task 1: Create eval dataset with 31 test queries** - `aab9bae` (feat)
2. **Task 2: Create debug retrieval endpoint** - `779c2a7` (feat)

## Files Created/Modified
- `tests/eval/__init__.py` - Python package marker for eval test directory
- `tests/eval/dataset.json` - 31-entry eval dataset with Russian queries for banking domain
- `app/api/v1/debug.py` - Debug retrieval endpoint with RetrievalStageResult response model
- `app/main.py` - Added debug_router registration

## Decisions Made
- BM25 stage in debug endpoint feeds vector search results as input documents, matching the search agent pipeline pattern (bm25_search is not a standalone index — it takes a document list)
- Each pipeline stage is independently wrapped in try/except so partial failures don't crash the endpoint
- Retrieval test entries use placeholder document filenames (zakon_o_bankah.pdf, etc.) to be calibrated after first real eval run

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- FastAPI not installed in system Python (app runs in Docker) — switched to AST-based and content-based verification instead of import-based checks

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Eval dataset ready for consumption by 01-03 eval harness
- Debug endpoint ready for threshold calibration in Phase 3
- All 5 intent types covered in dataset for routing accuracy measurement

## Self-Check: PASSED

- All 4 files exist on disk
- Commit aab9bae found (Task 1)
- Commit 779c2a7 found (Task 2)

---
*Phase: 01-observability-eval-infrastructure*
*Completed: 2026-03-17*
