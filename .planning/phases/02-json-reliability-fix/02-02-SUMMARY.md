---
phase: 02-json-reliability-fix
plan: 02
subsystem: prompts
tags: [llm-prompts, json-reliability, defense-in-depth]

# Dependency graph
requires:
  - phase: 01-observability-eval
    provides: "Eval infrastructure to measure JSON validity improvements"
provides:
  - "Anti-markdown instructions in all 4 JSON system prompts"
affects: [02-json-reliability-fix, 03-retrieval-quality]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Anti-markdown instruction block in Russian for JSON prompts"]

key-files:
  created: []
  modified:
    - "documentologist-miran-service--neo4j/langgraph-agent/app/prompts/__init__.py"

key-decisions:
  - "Identical anti-markdown block for all 4 prompts -- consistency over prompt-specific tuning"

patterns-established:
  - "FORMAT OTVET block: 5-line Russian anti-markdown instruction inserted before Vydaj JSON in every JSON prompt"

requirements-completed: [FR-1]

# Metrics
duration: 1min
completed: 2026-03-17
---

# Phase 2 Plan 2: Anti-Markdown Prompt Instructions Summary

**Added Russian anti-markdown instruction block (FORMАТ ОТВЕТА) to all 4 JSON system prompts as defense-in-depth against markdown-wrapped LLM output**

## Performance

- **Duration:** 1 min
- **Started:** 2026-03-17T11:11:30Z
- **Completed:** 2026-03-17T11:12:40Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Inserted 5-line anti-markdown block in VERIFY_COMPLIANCE, GENERATE_PLAN, ANALYZE_COMPARE, ANALYZE_EXTRACT
- Block instructs model to return raw JSON only, no backticks or markdown wrapping
- Non-JSON prompts (SUPERVISOR_CLASSIFY, SEARCH_EXPERT, ANALYZE_QA, ANALYZE_SUMMARY) left untouched
- Verified all 4 prompts contain the block and all non-JSON prompts are clean

## Task Commits

Each task was committed atomically:

1. **Task 1: Add anti-markdown instructions to all four JSON prompts** - `d57da80` (feat)

## Files Created/Modified
- `app/prompts/__init__.py` - Added ФОРМАТ ОТВЕТА anti-markdown block before each Выдай JSON: line in 4 prompts

## Decisions Made
- Used identical block text for all 4 prompts for consistency and maintainability

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Prompt-level defense-in-depth complete
- Ready for remaining Phase 2 plans (parse_with_retry, Pydantic schemas)
- Eval runner from Phase 1 can measure json_validity_rate improvement

## Self-Check: PASSED
- prompts/__init__.py: FOUND
- 02-02-SUMMARY.md: FOUND
- Commit d57da80: FOUND

---
*Phase: 02-json-reliability-fix*
*Completed: 2026-03-17*
