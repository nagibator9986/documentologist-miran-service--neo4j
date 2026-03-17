---
phase: 02-json-reliability-fix
plan: 04
subsystem: api
tags: [pydantic, parse-with-retry, analyze-agent, json-schema, structured-output]

# Dependency graph
requires:
  - phase: 02-json-reliability-fix
    provides: "Pydantic schemas (AnalyzeCompareResult, AnalyzeExtractResult) and parse_with_retry from plan 02-01"
provides:
  - "analyze_agent compare/extract tasks wired to parse_with_retry + Pydantic schema validation"
  - "_JSON_TASK_SCHEMAS mapping for schema-constrained generation in analyze_agent"
affects: [02-json-reliability-fix]

# Tech tracking
tech-stack:
  added: []
  patterns: ["parse_with_retry replaces get_json_llm + safe_parse_json for JSON tasks in analyze_agent", "_JSON_TASK_SCHEMAS dict maps task name to Pydantic schema class"]

key-files:
  created: []
  modified:
    - "langgraph-agent/app/agents/analyze_agent.py"

key-decisions:
  - "Removed get_json_llm and safe_parse_json imports — fully replaced by parse_with_retry pipeline"
  - "Fallback dict includes _parse_failed=True for metric tracking compatibility"

patterns-established:
  - "JSON agent wiring: _JSON_TASK_SCHEMAS maps task -> schema, _run_analysis_llm returns (result, success) tuple"

requirements-completed: [FR-1]

# Metrics
duration: 5min
completed: 2026-03-17
---

# Phase 2 Plan 04: Analyze Agent Wiring Summary

**analyze_agent compare/extract tasks wired to parse_with_retry + AnalyzeCompareResult/AnalyzeExtractResult Pydantic schemas, replacing get_json_llm + safe_parse_json**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-17T16:31:01Z
- **Completed:** 2026-03-17T16:36:14Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments
- Replaced get_json_llm + safe_parse_json pattern with parse_with_retry + Pydantic schema validation for compare and extract tasks
- Added _JSON_TASK_SCHEMAS mapping linking task types to their Pydantic schema classes
- Preserved _parse_failed fallback tracking and json_parse_success metric for observability
- Left qa and summary text tasks completely unchanged (still use get_llm + free text)

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire analyze_agent compare/extract to use parse_with_retry** - `d5e5127` (feat)

## Files Created/Modified
- `langgraph-agent/app/agents/analyze_agent.py` - Replaced JSON task handling with parse_with_retry + Pydantic schemas; removed get_json_llm/safe_parse_json imports; added _JSON_TASK_SCHEMAS; updated _run_analysis_llm to return (result, success) tuple; updated _parse_result to handle validated dicts

## Decisions Made
- Removed get_json_llm and safe_parse_json imports entirely since they are no longer used in analyze_agent (fully replaced by parse_with_retry pipeline from json_output.py)
- Fallback dict on parse failure includes _parse_failed=True key to maintain backward compatibility with json_parse_success metric in retrieval_metrics

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Python import verification limited to AST parse + grep (deps only available in Docker) -- same approach as prior plans

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All 4 JSON-producing agent outputs now use parse_with_retry + Pydantic schemas (verify_agent, generate_agent, analyze_agent compare, analyze_agent extract)
- Phase 2 JSON reliability fix is complete across all agents

## Self-Check: PASSED

- [x] analyze_agent.py exists at langgraph-agent/app/agents/analyze_agent.py
- [x] Commit d5e5127 (Task 1) exists

---
*Phase: 02-json-reliability-fix*
*Completed: 2026-03-17*
