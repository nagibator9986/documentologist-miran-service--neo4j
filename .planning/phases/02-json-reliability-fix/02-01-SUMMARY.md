---
phase: 02-json-reliability-fix
plan: 01
subsystem: api
tags: [pydantic, ollama, json-schema, structured-output, langchain]

# Dependency graph
requires:
  - phase: 01-observability-eval
    provides: "Structured logging, eval infrastructure for measuring JSON validity"
provides:
  - "Pydantic v2 schemas for all 4 JSON-producing agents (VerifyResult, GeneratePlan, AnalyzeCompareResult, AnalyzeExtractResult)"
  - "parse_with_retry function for schema-constrained LLM calls with validation and retry"
  - "get_schema_llm factory for Ollama grammar-level JSON constraint"
affects: [02-json-reliability-fix, 03-retrieval-quality]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Ollama format=schema.model_json_schema() for grammar-level constraint", "parse_with_retry returns tuple[T|None, bool] for downstream failure tracking", "Russian retry instructions to match agent prompt language"]

key-files:
  created:
    - "langgraph-agent/app/core/json_output.py"
  modified:
    - "langgraph-agent/app/core/llm.py"

key-decisions:
  - "temperature=0.0 for schema LLM (deterministic structured output)"
  - "Russian retry instruction (_RETRY_INSTRUCTION_RU) to match all-Russian prompt context"
  - "safe_parse_json as secondary fallback inside parse_with_retry loop for belt-and-suspenders"
  - "No with_structured_output() — known ChatOllama bugs (GitHub #25343, #29410)"

patterns-established:
  - "Schema-constrained LLM: use get_schema_llm(MyModel) instead of get_json_llm()"
  - "Validated parse: use parse_with_retry(messages, Schema) instead of raw invoke + safe_parse_json"

requirements-completed: [FR-1]

# Metrics
duration: 2min
completed: 2026-03-17
---

# Phase 2 Plan 01: JSON Output Layer Summary

**Pydantic v2 schemas for 4 agent outputs + parse_with_retry with Ollama grammar-level JSON constraint and safe_parse_json fallback**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-17T11:11:34Z
- **Completed:** 2026-03-17T11:13:07Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- get_schema_llm factory in llm.py passes Pydantic model_json_schema() to Ollama format parameter for grammar-level token constraint
- 4 Pydantic v2 schemas matching current agent JSON structures: VerifyResult, GeneratePlan, AnalyzeCompareResult, AnalyzeExtractResult
- parse_with_retry with primary model_validate_json, secondary safe_parse_json fallback, and Russian retry instructions

## Task Commits

Each task was committed atomically:

1. **Task 1: Add get_schema_llm to llm.py** - `7d109ba` (feat)
2. **Task 2: Create json_output.py with Pydantic schemas and parse_with_retry** - `8ac0755` (feat)

## Files Created/Modified
- `langgraph-agent/app/core/json_output.py` - Pydantic schemas (VerifyResult, GeneratePlan, AnalyzeCompareResult, AnalyzeExtractResult) + parse_with_retry function
- `langgraph-agent/app/core/llm.py` - Added get_schema_llm factory function and pydantic BaseModel import

## Decisions Made
- temperature=0.0 for get_schema_llm (deterministic structured output, same as get_json_llm)
- Russian retry instruction to match all-Russian prompts -- avoids language mismatch on correction attempts
- safe_parse_json as secondary fallback for robustness against markdown fences in LLM output
- Avoided with_structured_output() due to known ChatOllama bugs

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Python import verification failed due to missing langchain_ollama in local dev environment (expected -- deps only available in Docker). Verified via AST syntax check + grep acceptance criteria instead.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- json_output.py ready for import by verify_agent (plan 02-02), generate_agent (plan 02-03), and analyze_agent (plan 02-04)
- get_schema_llm available for any agent needing schema-constrained generation

## Self-Check: PASSED

- [x] json_output.py exists at langgraph-agent/app/core/json_output.py
- [x] Commit 7d109ba (Task 1) exists
- [x] Commit 8ac0755 (Task 2) exists

---
*Phase: 02-json-reliability-fix*
*Completed: 2026-03-17*
