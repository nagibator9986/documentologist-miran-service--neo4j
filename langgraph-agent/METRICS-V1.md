# Miran Agent v1.0 — Metrics Baseline

## Target Thresholds (Milestone 1)

| Metric | Target | How Measured |
|--------|--------|--------------|
| JSON parse success rate | >= 95% | `json_validity_rate` in eval output |
| Supervisor routing accuracy | >= 90% | `routing_accuracy` in eval output |
| Retrieval recall@5 (doc in base) | >= 80% | `retrieval_recall_at_5` in eval output |
| System crash on LLM timeout | 0 | Integration tests + graceful degradation (Phase 4) |
| Integration tests passing | 100% | `make test` |

## How to Run

### Unit + Integration Tests (no live services needed)

```bash
make test
# or: python -m pytest tests/ -v --ignore=tests/eval
```

All 38 tests should pass (11 integration + 27 unit).

### Eval Suite (requires full stack)

The eval suite runs 49 queries against the live API and measures routing accuracy,
JSON validity rate, and retrieval recall@5.

**Prerequisites:** Qdrant, Ollama, Neo4j, Redis, PostgreSQL must be running with
indexed documents.

```bash
# 1. Start all services
make up
# or for production config:
make docker-prod-up

# 2. Wait for health check
curl http://localhost:8001/health/detailed

# 3. Run eval (49 queries)
make eval
# or: python tests/eval/run_eval.py

# 4. Record output metrics in the "Recorded Runs" table below
```

**CLI options:**

```bash
# Run specific category only
python tests/eval/run_eval.py --category routing
python tests/eval/run_eval.py --category json_validity
python tests/eval/run_eval.py --category retrieval

# Custom base URL and timeout
python tests/eval/run_eval.py --base-url http://localhost:8001/api/v1 --timeout 120
```

**Output:** The runner prints a summary table to stdout and saves detailed
per-query results to `tests/eval/results.json` (gitignored).

## Eval Dataset

| Category | Count | What's Tested |
|----------|-------|---------------|
| Routing | 21 | Supervisor intent classification accuracy |
| JSON validity | 10 | Structured output parsing for verify/generate/analyze |
| Retrieval | 18 | Document found in top-5 for known queries |
| **Total** | **49** | |

### Output Metrics

The eval runner (`tests/eval/run_eval.py`) computes and reports:

| Metric Key | Description |
|------------|-------------|
| `routing_accuracy` | Fraction of queries where `response.intent == expected_intent` |
| `json_validity_rate` | Fraction of JSON queries where structured result is non-empty dict |
| `retrieval_recall_at_5` | Fraction of retrieval queries where expected doc appears in citations |
| `avg_elapsed_s` | Mean per-query latency |
| `p95_elapsed_s` | 95th percentile per-query latency |

## Recorded Runs

| Date | routing_accuracy | json_validity_rate | retrieval_recall_at_5 | avg_elapsed_s | Notes |
|------|-----------------|-------------------|----------------------|---------------|-------|
| _pending_ | -- | -- | -- | -- | Awaiting live stack deployment |

> **Note:** Eval suite requires all services running with indexed documents.
> Run `make eval` after deployment and fill in the table above.

## Test Suite Summary

As of v1.0 baseline (2026-03-18):

- **38 tests total** (11 integration + 27 unit)
- **All passing** (`python -m pytest tests/ -v --ignore=tests/eval -x` exits 0)
- Integration tests cover: search, analyze (qa + compare), verify, generate, ingest, health (liveness + detailed + Ollama-down)
- Unit tests cover: analyze_agent `_detect_task` classification (compare/extract/summary/qa + case sensitivity + priority)
