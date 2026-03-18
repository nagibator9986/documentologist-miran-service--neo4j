---
phase: 04-agent-logic-hardening
plan: 02
subsystem: api
tags: [qdrant, retrieval, compare, deduplication, performance]

# Dependency graph
requires:
  - phase: 03-retrieval-calibration
    provides: "_retrieve_and_rerank pipeline with cross-encoder reranking"
provides:
  - "Deduplicated compare retrieval path (2 Qdrant calls instead of 4)"
affects: [04-agent-logic-hardening]

# Tech tracking
tech-stack:
  added: []
  patterns: ["compare path reuses _retrieve_and_rerank directly"]

key-files:
  created: []
  modified:
    - "documentologist-miran-service--neo4j/langgraph-agent/app/agents/analyze_agent.py"

key-decisions:
  - "Deleted _retrieve_compare_pair entirely rather than refactoring it"

patterns-established:
  - "Compare path uses same _retrieve_and_rerank as other task types"

requirements-completed: [P4-DEDUP]

# Metrics
duration: 2min
completed: 2026-03-18
---

# Phase 4 Plan 2: Compare Dedup Summary

**Removed _retrieve_compare_pair to eliminate 2 wasted Qdrant vector calls in compare path, cutting retrieval from 4 to 2 calls**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-18T05:30:08Z
- **Completed:** 2026-03-18T05:32:01Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Deleted `_retrieve_compare_pair` function (26 lines) that performed 2 Qdrant searches whose results were never used
- Simplified compare branch in `analyze_node` to call `_retrieve_and_rerank` directly with extracted subjects
- 50% reduction in Qdrant calls for compare queries (4 to 2)

## Task Commits

Each task was committed atomically:

1. **Task 1: Remove _retrieve_compare_pair and simplify compare path** - `97dfa13` (feat)

## Files Created/Modified
- `documentologist-miran-service--neo4j/langgraph-agent/app/agents/analyze_agent.py` - Removed _retrieve_compare_pair function, updated module docstring, simplified compare branch in analyze_node

## Decisions Made
- Deleted _retrieve_compare_pair entirely rather than refactoring -- the function was purely wasteful since _retrieve_and_rerank already handles the same retrieval with better pipeline (BM25 + graph + cross-encoder)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- File is in a nested git repo (`documentologist-miran-service--neo4j/`) separate from the planning repo -- committed in the nested repo

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Compare path now uses the same retrieval pipeline as all other task types
- Ready for further agent hardening in subsequent plans

## Self-Check: PASSED

- [x] analyze_agent.py exists and has no references to _retrieve_compare_pair
- [x] 04-02-SUMMARY.md exists
- [x] Commit 97dfa13 exists in nested repo

---
*Phase: 04-agent-logic-hardening*
*Completed: 2026-03-18*
