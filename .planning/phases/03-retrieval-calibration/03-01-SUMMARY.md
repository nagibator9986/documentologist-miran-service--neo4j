---
phase: 03-retrieval-calibration
plan: 01
subsystem: search
tags: [retrieval, query-expansion, thresholds, cross-encoder, russian-nlp]

# Dependency graph
requires:
  - phase: 01-observability-eval-infrastructure
    provides: retrieval_metrics logging in search_node
provides:
  - Search pipeline without query expansion (direct embedding of user query)
  - Calibrated thresholds for Russian legal text (min_relevance_score=0.25, search_min_confidence=0.15)
  - Clean AgentState without query_expanded field
affects: [03-retrieval-calibration, eval-runner]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "No LLM query rewrite before embedding -- direct user query preserves legal terms"
    - "Threshold calibration comments with date for audit trail"

key-files:
  created: []
  modified:
    - documentologist-miran-service--neo4j/langgraph-agent/app/agents/search_agent.py
    - documentologist-miran-service--neo4j/langgraph-agent/app/prompts/__init__.py
    - documentologist-miran-service--neo4j/langgraph-agent/app/core/config.py
    - documentologist-miran-service--neo4j/langgraph-agent/app/graph/state.py
    - documentologist-miran-service--neo4j/langgraph-agent/app/api/v1/chat.py
    - documentologist-miran-service--neo4j/langgraph-agent/app/api/v1/completions.py
    - documentologist-miran-service--neo4j/langgraph-agent/chainlit_app.py

key-decisions:
  - "Removed query expansion entirely rather than making it optional -- 7b model drops key legal terms"
  - "Lowered min_relevance_score from 0.35 to 0.25 -- gives cross-encoder more candidates in normal path"
  - "Lowered search_min_confidence from 0.25 to 0.15 -- sigmoid scores on Russian text cluster 0.15-0.30"
  - "Kept rerank_top_k=7 and rerank_candidate_pool=30 unchanged per user decision"

patterns-established:
  - "Threshold comments include calibration date for future re-tuning"

requirements-completed: [FR-2]

# Metrics
duration: 6min
completed: 2026-03-18
---

# Phase 3 Plan 1: Query Expansion Removal + Threshold Calibration Summary

**Removed 7b LLM query expansion (false-negative source) and lowered retrieval thresholds from 0.35/0.25 to 0.25/0.15 for Russian legal text sigmoid score distribution**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-18T04:10:40Z
- **Completed:** 2026-03-18T04:16:58Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Eliminated query expansion LLM call that was dropping key legal terms from Russian queries
- Simplified _prepare_query to return (query, query) -- both lexical and embedding use original query
- Lowered min_relevance_score (0.35 -> 0.25) so more candidates reach cross-encoder in normal path
- Lowered search_min_confidence (0.25 -> 0.15) matching real sigmoid distribution for Russian legal text
- Cleaned up orphaned query_expanded field across AgentState, API endpoints, and Chainlit UI

## Task Commits

Each task was committed atomically:

1. **Task 1: Remove query expansion from search_agent.py and delete SEARCH_EXPAND_QUERY prompt** - `434b651` (feat) + prior `93d28cc`
2. **Task 2: Update config.py thresholds with rationale comments** - `0a56eaa` (feat)

Deviation fix:
- **Remove orphaned query_expanded field** - `a7b0273` (fix)

## Files Created/Modified
- `app/agents/search_agent.py` - Removed _expand_query, simplified _prepare_query, removed query_expanded from metrics/state
- `app/prompts/__init__.py` - Deleted SEARCH_EXPAND_QUERY constant
- `app/core/config.py` - min_relevance_score 0.35->0.25, search_min_confidence 0.25->0.15 with rationale
- `app/graph/state.py` - Removed query_expanded from AgentState and RetrievalMetrics
- `app/api/v1/chat.py` - Removed query_expanded from initial state dict
- `app/api/v1/completions.py` - Removed query_expanded from initial state dict
- `chainlit_app.py` - Removed query_expanded UI indicator

## Decisions Made
- Removed query expansion entirely rather than making it optional -- the 7b model consistently drops key legal terms, causing false negatives
- Threshold values chosen based on observed cross-encoder sigmoid score distribution on Russian legal text (cluster 0.15-0.30)
- rerank_top_k (7) and rerank_candidate_pool (30) left unchanged per explicit user decision

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed orphaned query_expanded references across codebase**
- **Found during:** Task 1 (after removing query_expanded from search_node)
- **Issue:** query_expanded field still existed in AgentState TypedDict, RetrievalMetrics TypedDict, chat.py initial state, completions.py initial state, and chainlit_app.py UI indicator
- **Fix:** Removed the field from all locations
- **Files modified:** state.py, chat.py, completions.py, chainlit_app.py
- **Verification:** grep for query_expanded returns 0 matches across entire langgraph-agent
- **Committed in:** a7b0273

**2. [Rule 3 - Blocking] search_agent.py changes already committed in nested repo**
- **Found during:** Task 1 (attempting to commit)
- **Issue:** Prior session commit 93d28cc already contained search_agent.py changes (removal of _expand_query, get_draft_llm import, query_expanded fields, plus score stats addition)
- **Fix:** Committed only the remaining prompts/__init__.py change as 434b651
- **Impact:** No functional impact -- all planned changes are in place

---

**Total deviations:** 2 auto-fixed (1 bug, 1 blocking)
**Impact on plan:** Bug fix was necessary for correctness -- orphaned TypedDict fields would cause confusion. No scope creep.

## Issues Encountered
- search_agent.py was already modified in nested repo commit 93d28cc from a prior session. Identified via git diff and committed only the remaining prompts change.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Search pipeline ready for eval comparison (plan 03-03)
- Thresholds can be further tuned after collecting eval metrics
- No blockers for remaining phase 3 plans

## Self-Check: PASSED

- All 5 key files verified present on disk
- All 3 commits (434b651, 0a56eaa, a7b0273) verified in nested repo git log
- grep confirms 0 matches for _expand_query, SEARCH_EXPAND_QUERY, get_draft_llm, query_expanded

---
*Phase: 03-retrieval-calibration*
*Completed: 2026-03-18*
