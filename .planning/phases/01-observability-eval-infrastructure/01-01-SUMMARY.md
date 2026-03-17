---
phase: 01-observability-eval-infrastructure
plan: 01
subsystem: infrastructure
tags: [mlflow, logging, observability, agent-state]
dependency_graph:
  requires: []
  provides: [mlflow-enabled-by-default, json-logging, tier-in-agent-state]
  affects: [all-agents, chat-api, logging-pipeline]
tech_stack:
  added: [python-json-logger>=2.0]
  patterns: [centralized-logging-setup, structured-logging, agent-state-enrichment]
key_files:
  created:
    - documentologist-miran-service--neo4j/langgraph-agent/app/core/logging_config.py
  modified:
    - documentologist-miran-service--neo4j/langgraph-agent/docker-compose.yml
    - documentologist-miran-service--neo4j/langgraph-agent/.env.example
    - documentologist-miran-service--neo4j/langgraph-agent/app/core/config.py
    - documentologist-miran-service--neo4j/langgraph-agent/app/main.py
    - documentologist-miran-service--neo4j/langgraph-agent/requirements.txt
    - documentologist-miran-service--neo4j/langgraph-agent/app/graph/state.py
    - documentologist-miran-service--neo4j/langgraph-agent/app/agents/supervisor.py
    - documentologist-miran-service--neo4j/langgraph-agent/app/api/v1/chat.py
decisions:
  - "LOG_FORMAT read from env before Settings loads — logging must be ready before lazy get_settings() is called in lifespan"
  - "tier field added to AgentState (not just logged as text) so downstream agents and eval scripts can access it programmatically"
  - "python-json-logger used for JSON formatter — battle-tested, minimal dependency"
metrics:
  duration: ~15 minutes
  completed_date: 2026-03-17
  tasks_completed: 3
  tasks_total: 3
  files_modified: 8
---

# Phase 1 Plan 1: MLflow Config, Structured Logging, Tier in AgentState Summary

**One-liner:** MLflow enabled by default via docker-compose + config, JSON structured logging available via LOG_FORMAT=json env var, supervisor tier (compound/keyword/llm) propagated through AgentState for downstream observability.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Enable MLflow by default and update .env.example | 3b66625 | docker-compose.yml, config.py, .env.example |
| 2 | Create structured JSON logging module and wire into main.py | 8d2d3fd | logging_config.py (new), main.py, requirements.txt |
| 3 | Add tier field to AgentState and supervisor return | 62a519d | state.py, supervisor.py, chat.py |

## What Was Built

### Task 1: MLflow Default On
- `docker-compose.yml`: `MLFLOW_ENABLED` default changed from `false` to `true`
- `app/core/config.py`: `mlflow_enabled` default changed to `True`, new `log_format: str = "text"` setting added
- `.env.example`: New `# Observability` section documents all MLflow and LOG_FORMAT variables

### Task 2: Structured JSON Logging
- New `app/core/logging_config.py` with `setup_logging(log_level, log_format)`:
  - `log_format="text"` (default): human-readable `%(asctime)s %(levelname)s` format — no change for dev
  - `log_format="json"`: `pythonjsonlogger.JsonFormatter` with renamed fields (`timestamp`, `level`, `logger`)
- `app/main.py`: replaced `logging.basicConfig(...)` with `setup_logging()` using `os.getenv("LOG_FORMAT", "text")` — logging configured before Settings is loaded
- `requirements.txt`: added `python-json-logger>=2.0`

### Task 3: Tier in AgentState
- `app/graph/state.py`: `tier: str` added to `AgentState` TypedDict after `intents` field
- `app/agents/supervisor.py`: all three classification return paths now include `"tier"` key:
  - Compound heuristics path: `"tier": "compound"`
  - Keyword override path: `"tier": "keyword"`
  - LLM fallback path: `"tier": "llm"`
- `app/api/v1/chat.py`: `_build_initial_state()` initializes `"tier": ""`

## Verification Results

All plan success criteria confirmed:
- `MLFLOW_ENABLED: ${MLFLOW_ENABLED:-true}` in docker-compose.yml (line 59)
- `mlflow_enabled: bool = True` in config.py (line 195)
- `tier: str` in AgentState (state.py line 40)
- 3 `"tier":` occurrences in supervisor.py (one per classification path)
- `setup_logging()` in main.py, `logging.basicConfig` removed

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check: PASSED

Files confirmed present:
- FOUND: documentologist-miran-service--neo4j/langgraph-agent/app/core/logging_config.py
- FOUND: commits 3b66625, 8d2d3fd, 62a519d in git log
