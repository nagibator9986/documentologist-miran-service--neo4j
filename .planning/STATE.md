---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: unknown
stopped_at: Completed 03-03-PLAN.md
last_updated: "2026-03-18T04:25:49.182Z"
progress:
  total_phases: 5
  completed_phases: 2
  total_plans: 11
  completed_plans: 10
---

# Project State

## Current Status
**Phase:** Phase 3 — Retrieval Calibration (Plan 3 of 4 complete)
**Milestone:** 1 — Production-Ready Agent System
**Date:** 2026-03-18

## Active Work
Phase 3 in progress. Plans 03-01, 03-02, and 03-03 complete. Query expansion removed, thresholds calibrated, score distribution stats added, analyze_agent retrieval parity achieved.

## Completed
- [x] Project initialization (PROJECT.md, REQUIREMENTS.md, ROADMAP.md)
- [x] Codebase analysis — all 5 agents read, architecture understood
- [x] Phase 1 Plan 01 — MLflow config, structured logging, tier in AgentState (commits: 3b66625, 8d2d3fd, 62a519d)
- [x] Phase 1 Plan 02 — Eval dataset (31 queries) + debug retrieval endpoint (commits: aab9bae, 779c2a7)
- [x] Phase 1 Plan 03 — Agent metrics enrichment + eval runner script (commits: 13ffc3e, dff09eb)

## Completed
- [x] Phase 3 Plan 01 — Query expansion removal + threshold calibration (commits: 434b651, 0a56eaa, a7b0273)
- [x] Phase 3 Plan 02 — Score statistics (min/max/p50) in search_node metrics (commit: 93d28cc)
- [x] Phase 3 Plan 03 — Analyze agent retrieval parity: pre-filter, INFO logging, stage_counts, metrics expansion (commits: e91f92a, 8018d78)

## Key Decisions
- **Removed query expansion entirely** — 7b model drops key legal terms, causing false negatives
- **min_relevance_score 0.35->0.25** — gives cross-encoder more candidates in normal path
- **search_min_confidence 0.25->0.15** — sigmoid scores on Russian text cluster 0.15-0.30
- **Used statistics.median for p50** — stdlib, no external dependency
- **Empty score list returns {} not zeros** — clean metrics when stage has no hits
- **Compare path merges stage_counts additively** — sum both sides for unified metrics
- **Replaced hits/best_score with per-stage keys** — vector_hits, bm25_hits, etc. + best_rerank_score
- **No rewrite** — evolutionary improvement of existing agents
- **Research skipped** — codebase already analyzed directly
- **Phases ordered by impact:** JSON fix (P0) before retrieval (P1) before routing (P2)
- **Eval first** — Phase 1 creates measurement infrastructure before any fixes
- **LOG_FORMAT read from env before Settings** — logging must be ready before lazy get_settings() called in lifespan
- **tier in AgentState not just logs** — enables programmatic access by downstream agents and eval scripts
- **BM25 debug re-scores vector hits** — bm25_search requires a documents list input, not a standalone index
- **Debug endpoint uses tools directly** — decoupled from search_agent internals for independent diagnostics
- **supervisor retrieval_metrics overwritten by downstream agent** — acceptable since each pipeline node replaces previous metrics
- **json_parse_success=None for analyze qa/summary** — these tasks produce free text, not JSON
- **Removed get_json_llm/safe_parse_json from analyze_agent** — fully replaced by parse_with_retry pipeline
- **Fallback dict includes _parse_failed=True** — maintains backward compat with json_parse_success metric

## Critical Context
- Код находится в: `documentologist-miran-service--neo4j/langgraph-agent/`
- Агенты: `app/agents/` — supervisor, search, analyze, verify, generate, ingest, memory
- Config: `app/core/config.py` (Settings, pydantic-settings)
- Промпты: `app/prompts/` (не читались — нужно проверить в Phase 2)
- JSON агенты используют `get_json_llm` из `app/core/llm.py` — нужно проверить реализацию
- Structured logging: `app/core/logging_config.py` — setup_logging(log_level, log_format)
- Eval dataset: `tests/eval/dataset.json` (31 queries)
- Eval runner: `tests/eval/run_eval.py` (routing_accuracy, json_validity_rate, retrieval_recall@5)

## Last Session
Stopped at: Completed 03-03-PLAN.md

## Next Action
Execute remaining Phase 3 plan (03-04).
