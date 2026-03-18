---
phase: 03-retrieval-calibration
plan: 04
subsystem: testing
tags: [eval, retrieval, dataset, probing-queries, russian-nlp]

# Dependency graph
requires:
  - phase: 03-01
    provides: "Calibrated thresholds and query expansion removal"
  - phase: 03-02
    provides: "Score distribution stats in search metrics"
  - phase: 03-03
    provides: "Analyze agent retrieval parity"
provides:
  - "10 probing retrieval entries (probe-01 to probe-10) in eval dataset"
  - "41-entry eval dataset covering routing, json_validity, and retrieval categories"
  - "Verification commands for live eval run"
affects: [eval-runner, phase-gate-validation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Probe entries use actual indexed filenames from existing retrieval entries"
    - "Each probe maps to 2 expected docs for recall measurement"

key-files:
  created:
    - ".planning/phases/03-retrieval-calibration/03-VERIFICATION.md"
  modified:
    - "documentologist-miran-service--neo4j/langgraph-agent/tests/eval/dataset.json"

key-decisions:
  - "Used filenames from existing retrieval entries (retrieval-01 to retrieval-08) as source of truth for expected_docs"
  - "Eval run deferred -- API server not running locally; documented verification commands for live run"

patterns-established:
  - "Probe entry naming: probe-XX with tags including 'probe' for filtering"

requirements-completed: [FR-2]

# Metrics
duration: 4min
completed: 2026-03-18
---

# Phase 3 Plan 04: Probing Retrieval Entries + Eval Validation Summary

**10 probing retrieval queries added to eval dataset covering deposits, credit, rates, collateral, insurance, and obligations topics with actual indexed filenames**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-18T04:26:38Z
- **Completed:** 2026-03-18T04:30:38Z
- **Tasks:** 2 (1 fully completed, 1 deferred pending API server)
- **Files modified:** 1

## Accomplishments
- Added 10 probing retrieval entries (probe-01 through probe-10) to eval dataset
- All entries use actual indexed filenames mapped to query topics (depozity.pdf, bankovskie_operacii.pdf, zakon_o_bankah.pdf, etc.)
- Dataset expanded from 31 to 41 entries, retrieval category from 8 to 18 entries
- JSON structure validated: no duplicate IDs, all probes have category=retrieval and intent=search
- Documented eval verification commands for when API server is available

## Task Commits

Each task was committed atomically:

1. **Task 1: Add 10 probing retrieval entries to dataset.json** - `e21016e` (feat)
2. **Task 2: Run eval suite** - Deferred (API server not running; commands documented in 03-VERIFICATION.md)

## Files Created/Modified
- `documentologist-miran-service--neo4j/langgraph-agent/tests/eval/dataset.json` - Added 10 probe entries (probe-01 to probe-10) with category=retrieval, intent=search, actual indexed filenames in expected_docs
- `.planning/phases/03-retrieval-calibration/03-VERIFICATION.md` - Eval verification commands for live run

## Decisions Made
- Used filenames from existing retrieval entries (retrieval-01 through retrieval-08) as the source of truth for expected_docs, since the API server is not running to query actual indexed documents
- Each probe entry maps to 2 expected docs for meaningful recall measurement
- Eval run deferred -- the plan explicitly accounts for this: "If the eval runner requires a running API server, note this and document the command to run once the server is up"

## Deviations from Plan

None - plan executed exactly as written. The plan explicitly handles the case where the API server is not available.

## Issues Encountered
- API server (localhost:8001) not running in dev environment, preventing live eval execution. This is expected for local development -- the eval run is a UAT gate to be executed when the full stack is deployed.

## User Setup Required

To complete the phase gate validation, run:
```bash
cd documentologist-miran-service--neo4j/langgraph-agent
python tests/eval/run_eval.py --timeout 120
```
Expected: `retrieval_recall_at_5 >= 0.80`

See `.planning/phases/03-retrieval-calibration/03-VERIFICATION.md` for full details.

## Next Phase Readiness
- Eval dataset complete with 41 entries across all three categories
- Phase 3 code changes complete (query expansion removal, threshold calibration, score stats, analyze agent parity)
- Eval validation pending live API deployment
- Ready for Phase 4 (routing) once retrieval recall is confirmed >= 0.80

---
*Phase: 03-retrieval-calibration*
*Completed: 2026-03-18*
