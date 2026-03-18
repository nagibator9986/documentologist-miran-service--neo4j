---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: unknown
stopped_at: Completed 05-02-PLAN.md
last_updated: "2026-03-18T10:59:32Z"
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 17
  completed_plans: 16
---

# Project State

## Current Status
**Phase:** Phase 5 — Production Hardening (In Progress, 2 of 3 plans done)
**Milestone:** 1 — Production-Ready Agent System
**Date:** 2026-03-18

## Active Work
Phase 5 in progress. Plan 01 (pending), Plan 02 (env/docker/makefile hardening - complete), Plan 03 (pending).

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
- [x] Phase 3 Plan 04 — 10 probing retrieval entries in eval dataset, 41 total queries (commit: e21016e)

## Completed
- [x] Phase 4 Plan 01 — Supervisor keyword expansion: 14 new patterns, normalized logging, 8 routing eval entries (commits: 646b333, 41f2c24)
- [x] Phase 4 Plan 02 — Compare dedup: removed _retrieve_compare_pair, 4->2 Qdrant calls (commit: 97dfa13)
- [x] Phase 4 Plan 03 — Hallucination guard + graceful degradation: search/analyze empty guards, HTTP 500->200 (commits: d878e11, 22c4c87)

## Completed
- [x] Phase 5 Plan 02 — Env/Docker/Makefile hardening: 68 Settings fields in .env.example, Makefile eval/prod targets, docker-compose.prod.yml overlay (commits: adb7b2a, 4498aa8)

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
- **Probe expected_docs from existing entries** — used filenames from retrieval-01..08 as source of truth since API not running
- **Eval run deferred** — plan accounts for API-not-running case; documented verification commands
- **Deleted _retrieve_compare_pair entirely** — function was purely wasteful, _retrieve_and_rerank already provides full pipeline
- **Negative lookahead on можно ли** — excludes procedural queries (получить/оформить/подать) from verify routing
- **Domain-specific informational section in _SEARCH_KW** — порядок, условия, требования, ставка, обязанности, процедура, страхование
- **Hallucination guard retries once only** — avoids infinite loops if model consistently stubs
- **HTTP 500 replaced with 200 + error payload** — clients always get parseable responses
- **memory_agent Redis failure confirmed non-fatal** — no changes needed, both nodes already wrap Redis in try/except
- **All 68 Settings fields documented in .env.example** — with defaults and comments
- **docker-compose.prod.yml is overlay, not full copy** — merges on base via -f flag
- **--compatibility flag required** — deploy.resources.limits silently ignored without Swarm
- **langgraph-agent gets 4CPU/4G** — largest allocation due to cross-encoder workload
- **MLFLOW_ENABLED=false in prod overlay** — reduces overhead; enable explicitly when needed

## Critical Context
- Код находится в: `documentologist-miran-service--neo4j/langgraph-agent/`
- Агенты: `app/agents/` — supervisor, search, analyze, verify, generate, ingest, memory
- Config: `app/core/config.py` (Settings, pydantic-settings)
- Промпты: `app/prompts/` (не читались — нужно проверить в Phase 2)
- JSON агенты используют `get_json_llm` из `app/core/llm.py` — нужно проверить реализацию
- Structured logging: `app/core/logging_config.py` — setup_logging(log_level, log_format)
- Eval dataset: `tests/eval/dataset.json` (49 queries: 21 routing, 10 json_validity, 18 retrieval)
- Eval runner: `tests/eval/run_eval.py` (routing_accuracy, json_validity_rate, retrieval_recall@5)

## Last Session
Stopped at: Completed 05-02-PLAN.md

## Next Action
Phase 5 Plan 02 complete. Remaining: Plan 01 and Plan 03. Production Docker config ready for deployment.
