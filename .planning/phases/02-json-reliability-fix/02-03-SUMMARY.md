---
phase: 02-json-reliability-fix
plan: 03
subsystem: api
tags: [pydantic, parse-with-retry, schema-constrained, verify-agent, generate-agent]

# Dependency graph
requires:
  - phase: 02-json-reliability-fix
    plan: 01
    provides: "Pydantic schemas (VerifyResult, GeneratePlan) and parse_with_retry function"
provides:
  - "verify_agent wired to parse_with_retry + VerifyResult with num_predict=2048"
  - "generate_agent wired to parse_with_retry + GeneratePlan with num_predict=2048"
affects: [02-json-reliability-fix, 03-retrieval-quality]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Agent -> parse_with_retry(messages, Schema) -> model_dump() -> dict with fallback"]

key-files:
  created: []
  modified:
    - "langgraph-agent/app/agents/verify_agent.py"
    - "langgraph-agent/app/agents/generate_agent.py"

key-decisions:
  - "num_predict=2048 for both agents (Russian legal text is token-heavy)"
  - "verify_agent _parse_verify_result now accepts dict instead of raw string (no more safe_parse_json)"
  - "generate_agent fallback includes _parse_failed=True (was missing in original safe_parse_json fallback)"

patterns-established:
  - "Agent wiring pattern: build messages -> parse_with_retry(messages, Schema, num_predict=N) -> model_dump() on success, fallback dict with _parse_failed=True on failure"

requirements-completed: [FR-1]

# Metrics
duration: 6min
completed: 2026-03-17
---

# Phase 2 Plan 03: Agent Wiring Summary

**verify_agent and generate_agent wired to parse_with_retry + Pydantic schemas with num_predict=2048 and _parse_failed fallback preservation**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-17T16:31:02Z
- **Completed:** 2026-03-17T16:37:30Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- verify_agent._run_compliance_llm now uses parse_with_retry + VerifyResult, returns tuple[dict, bool] instead of raw string
- generate_agent._plan_document now uses parse_with_retry + GeneratePlan, returns validated dict with _parse_failed fallback
- num_predict increased from 1024 to 2048 in both agents for Russian legal text token requirements
- Removed unused imports (get_json_llm, safe_parse_json) from both agents; kept invoke_with_retry in generate_agent for _validate_draft

## Task Commits

Each task was committed atomically:

1. **Task 1: Wire verify_agent to use parse_with_retry + VerifyResult** - `f739425` (feat)
2. **Task 2: Wire generate_agent to use parse_with_retry + GeneratePlan** - `9fe8949` (feat)

## Files Created/Modified
- `langgraph-agent/app/agents/verify_agent.py` - Replaced get_json_llm+invoke_with_retry+safe_parse_json with parse_with_retry+VerifyResult; _parse_verify_result now accepts dict
- `langgraph-agent/app/agents/generate_agent.py` - Replaced get_json_llm+invoke_with_retry+safe_parse_json with parse_with_retry+GeneratePlan; added _parse_failed to fallback

## Decisions Made
- num_predict=2048 for both agents -- Russian legal text produces significantly more tokens than English equivalents
- _parse_verify_result signature changed from (raw: str) to (result: dict) -- safe_parse_json no longer needed since parse_with_retry handles parsing
- generate_agent fallback dict now includes _parse_failed=True explicitly -- the original safe_parse_json fallback dict omitted this, meaning json_parse_success would incorrectly report True on parse failure

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Python import verification not possible locally (langchain_ollama not installed outside Docker) -- verified via AST syntax check and grep acceptance criteria instead.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Both critical JSON-producing agents (verify, generate) now use schema-constrained generation with retry
- Ready for plan 02-04: wiring analyze_agent to parse_with_retry + AnalyzeCompareResult/AnalyzeExtractResult
- json_parse_success metric in retrieval_metrics correctly reflects parse_with_retry results for both agents

## Self-Check: PASSED

- [x] verify_agent.py exists and contains parse_with_retry + VerifyResult
- [x] generate_agent.py exists and contains parse_with_retry + GeneratePlan
- [x] Commit f739425 (Task 1) exists
- [x] Commit 9fe8949 (Task 2) exists
- [x] SUMMARY.md created

---
*Phase: 02-json-reliability-fix*
*Completed: 2026-03-17*
