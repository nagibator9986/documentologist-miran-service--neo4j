# Phase 1: Observability & Eval Infrastructure - Research

**Researched:** 2026-03-17
**Domain:** MLflow tracing, structured logging, evaluation infrastructure, FastAPI debug endpoints
**Confidence:** HIGH

## Summary

Phase 1 builds measurement infrastructure before any agent fixes. The codebase already has substantial groundwork: MLflow integration exists in `tracing.py` with autolog, trace_node decorator, and prompt registry. The MLflow service is already defined in `docker-compose.yml` (with MinIO artifact storage, health checks, and all dependencies). The main gap is that `MLFLOW_ENABLED` defaults to `false` -- it needs to be `true` by default. Logging already has partial structured data in `retrieval_metrics` dicts inside search, verify, generate, and analyze agents, but it uses `logging.basicConfig` with text format, not JSON structured logging. There are no existing eval tests -- only one unit test file (`test_analyze_classify.py`) using pytest.

The primary work involves: (1) flipping MLflow to enabled-by-default and adding it to `.env.example`, (2) adding a JSON log formatter and enriching log fields across all agents, (3) creating a 30+ entry eval dataset with expected outcomes, (4) building a `run_eval.py` script that hits the API and reports metrics, and (5) creating a debug retrieval endpoint that exposes all pipeline stages without the LLM call.

**Primary recommendation:** Leverage existing infrastructure maximally -- MLflow setup, `retrieval_metrics` dicts, `safe_parse_json` return values, and supervisor tier logging are already 60-70% done. Focus effort on the eval dataset design and the debug endpoint.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| mlflow | >=2.14.0 (already in requirements.txt) | Tracing, metrics, prompt versioning | Already integrated; langchain autolog works with ChatOllama |
| pytest | >=8.0 (already in dev deps) | Test runner for eval suite | Already used for existing test |
| python-json-logger | >=2.0 | Structured JSON log output | Standard Python structured logging; zero-config with stdlib logger |
| httpx | >=0.27.0 (already in dev deps) | HTTP client for eval script | Already a dependency; async-capable, works with FastAPI TestClient |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| fastapi (existing) | >=0.110.0 | Debug endpoint | Already the web framework |
| pydantic (existing) | via fastapi | Eval dataset schema validation | Validate eval dataset entries |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| python-json-logger | structlog | structlog is more powerful but heavier; python-json-logger is minimal and sufficient |
| pytest eval runner | standalone script | Script is what ROADMAP specifies; pytest can be added later for CI |
| JSON eval dataset | YAML | JSON is native to Python stdlib; YAML would add pyyaml dependency |

**Installation:**
```bash
pip install python-json-logger>=2.0
```

Note: mlflow, pytest, httpx are already in requirements.txt / pyproject.toml.

## Architecture Patterns

### Recommended Project Structure
```
langgraph-agent/
├── app/
│   ├── api/v1/
│   │   ├── debug.py              # NEW: debug/retrieval endpoint
│   │   └── ...existing...
│   ├── core/
│   │   ├── logging_config.py     # NEW: JSON structured logging setup
│   │   └── ...existing...
│   └── agents/                   # MODIFY: add structured log fields
├── tests/
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── dataset.json          # NEW: 30+ eval queries
│   │   └── run_eval.py           # NEW: eval runner script
│   └── test_analyze_classify.py  # EXISTING
├── docker-compose.yml            # MODIFY: MLFLOW_ENABLED=true default
└── .env.example                  # MODIFY: add MLFLOW_ENABLED=true
```

### Pattern 1: Structured JSON Logging
**What:** Replace `logging.basicConfig` text format with JSON structured logging using `python-json-logger`.
**When to use:** All agents already log via `logger.info()` -- format change is centralized in `main.py`.
**Example:**
```python
# app/core/logging_config.py
import logging
import sys
from pythonjsonlogger import jsonlogger

def setup_logging(log_level: str = "INFO"):
    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)
```

### Pattern 2: Eval Dataset as JSON
**What:** Static JSON file with eval entries, loaded by `run_eval.py`.
**When to use:** Eval dataset is human-curated, versioned in git, loaded once per eval run.
**Example:**
```python
# tests/eval/dataset.json structure
[
  {
    "id": "route-01",
    "query": "что такое овердрафт",
    "expected_intent": "search",
    "expected_docs": [],          // empty = routing-only test
    "expected_json_valid": null,  // null = not a JSON agent
    "category": "routing",
    "tags": ["search", "keyword"]
  },
  {
    "id": "json-01",
    "query": "проверь: банк выдаёт кредит без лицензии НБ РК",
    "expected_intent": "verify",
    "expected_docs": [],
    "expected_json_valid": true,
    "category": "json_validity",
    "tags": ["verify", "json"]
  },
  {
    "id": "retrieval-01",
    "query": "какие требования к минимальному капиталу банка",
    "expected_intent": "search",
    "expected_docs": ["zakon_o_bankah.pdf"],
    "expected_json_valid": null,
    "category": "retrieval",
    "tags": ["search", "retrieval"]
  }
]
```

### Pattern 3: Debug Endpoint (no LLM call)
**What:** `GET /api/v1/debug/retrieval?q=...` runs the full retrieval pipeline (vector, BM25, graph, rerank) without the LLM generation step. Returns all stage scores.
**When to use:** Diagnosing retrieval quality, threshold calibration (Phase 3 depends on this).
**Example:**
```python
# app/api/v1/debug.py
@router.get("/debug/retrieval", tags=["debug"])
async def debug_retrieval(q: str, collection: str | None = None):
    """Run retrieval pipeline without LLM, return all stage scores."""
    # Reuse search_agent private functions:
    # _prepare_query, _retrieve_vector_bm25, _retrieve_graph,
    # _normalize_graph_hits, _merge_all_hits, _filter_by_relevance,
    # _rerank_and_calibrate
    # Return structured JSON with counts and scores at each stage
```

### Pattern 4: Enriching retrieval_metrics Across All Agents
**What:** Each agent already returns a `retrieval_metrics` dict. Add missing fields: `intent`, `tier`, `json_parse_success`.
**When to use:** In each agent's `*_node()` function, extend the existing metrics dict.

### Anti-Patterns to Avoid
- **Importing agent internals in debug endpoint:** Search agent functions are private (`_retrieve_*`). Either make them module-level or create a shared retrieval pipeline module. Simplest approach: make the needed functions public with a `retrieval_` prefix, or duplicate the pipeline in debug.py (worse).
- **Running eval against production:** Eval script should hit a local dev instance. Never the production endpoint.
- **Putting eval queries in pytest parametrize:** Use a standalone JSON dataset file -- pytest parametrize makes it hard to share the dataset with non-pytest tools and to compute aggregate metrics.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON log formatting | Custom JSON serializer | python-json-logger | Handles all edge cases (exceptions, extra fields, unicode) |
| LLM call tracing | Custom timing/logging | MLflow autolog (already integrated) | Records inputs, outputs, latency, tokens automatically |
| Eval metrics computation | Manual accuracy calculation | Simple counters + json output | Eval is straightforward (accuracy = matches / total); no need for heavy frameworks |
| Cross-encoder score normalization | Custom normalization | Already done in reranker.py (_sigmoid) | Sigmoid normalization is already implemented and correct |

**Key insight:** The codebase has done significant preparatory work. MLflow integration, retrieval_metrics, safe_parse_json, tier logging in supervisor are all partially implemented. This phase is about filling gaps and connecting existing pieces, not building from scratch.

## Common Pitfalls

### Pitfall 1: MLflow Container Startup Ordering
**What goes wrong:** `langgraph-agent` depends on `mlflow` service which depends on `minio` + `minio-init`. If MinIO bucket creation fails, MLflow never starts, blocking the agent container.
**Why it happens:** Docker compose dependency chain: agent -> mlflow -> minio-init -> minio. First startup with MLFLOW_ENABLED=true can hang.
**How to avoid:** MLflow dependency already exists in docker-compose.yml (line 72-73). The agent's `setup_mlflow()` is non-fatal (catches all exceptions). The risk is minimal because this is already handled. Just verify the `minio-init` service runs successfully on fresh deploy.
**Warning signs:** Agent container restarting in a loop; check `docker compose logs mlflow`.

### Pitfall 2: Structured Logging Breaking Existing Log Parsing
**What goes wrong:** Switching from text to JSON logging breaks any log monitoring that expects the current text format.
**Why it happens:** Current format is `%(asctime)s %(levelname)s %(name)s -- %(message)s`. JSON output looks completely different.
**How to avoid:** Make JSON logging opt-in via `LOG_FORMAT=json` env var. Default stays as text format for development; JSON for production. This avoids breaking dev workflows.
**Warning signs:** Logs are unreadable in development terminal.

### Pitfall 3: Eval Script Requiring Full Infrastructure
**What goes wrong:** `run_eval.py` requires ALL services running (Qdrant, Neo4j, Redis, Postgres, Ollama, MLflow) to execute a single eval query.
**Why it happens:** The chat endpoint runs the full graph pipeline including memory_load (Redis/Postgres), supervisor, and agent (Qdrant, Neo4j, Ollama).
**How to avoid:** Design eval script with clear prerequisites documentation. Use the existing `make infra-up` to bring up infrastructure. Add a timeout per query (30-60s) and graceful error handling for individual test cases.
**Warning signs:** Eval script hangs or crashes on first query.

### Pitfall 4: json_parse_success Field Location
**What goes wrong:** Not understanding where JSON parsing happens to track `json_parse_success`.
**Why it happens:** `safe_parse_json` is called in verify_agent, generate_agent, and analyze_agent (for compare/extract tasks). The `_parse_failed` key in the fallback dict already signals failure.
**How to avoid:** Track `json_parse_success` by checking whether the result from `safe_parse_json` has `_parse_failed: true`. Add this as a field to `retrieval_metrics` in each JSON-producing agent.
**Warning signs:** `json_parse_success` always reads `null` because it was never set.

### Pitfall 5: Debug Endpoint Coupling to Search Agent Internals
**What goes wrong:** Debug endpoint imports private functions from search_agent.py, creating tight coupling. Future refactors break the debug endpoint silently.
**Why it happens:** Retrieval stages are private functions designed for the search pipeline only.
**How to avoid:** Extract retrieval stages into a shared module (e.g., `app/core/retrieval.py`) or make the specific functions explicitly public in search_agent. Alternatively, keep it simple: have the debug endpoint construct its own lightweight pipeline using the same tools (qdrant_search, bm25_search, reranker) directly.
**Warning signs:** ImportError or AttributeError when running the debug endpoint.

## Code Examples

### Existing MLflow Setup (already working in tracing.py)
```python
# app/core/tracing.py -- setup_mlflow() already:
# 1. Sets tracking URI from settings
# 2. Sets experiment name
# 3. Enables langchain autolog (captures ChatOllama calls)
# 4. Is non-fatal (catches ImportError, all exceptions)
# 5. trace_node() decorator logs retrieval_metrics to MLflow
```

### Existing Retrieval Metrics in search_agent.py (already has most fields)
```python
# search_agent.py lines 632-648 -- search_node already produces:
metrics = {
    "node": "search",
    "query_len": len(lexical_query),
    "query_expanded": embed_query != lexical_query,
    "vector_hits": len(vector_hits),
    "bm25_hits": len(bm25_hits),
    "graph_hits": len(graph_hits),
    "entity_hits": len(graph_results.get("entities", [])),
    "law_hits": len(graph_results.get("laws", [])),
    "merged_hits": len(merged),
    "relevant_hits": len(relevant),
    "reranked_hits": len(reranked),
    "best_rerank_score": round(best_score, 4),
    "has_context": has_context,
    "is_exact_search": is_exact,
    "elapsed_s": round(elapsed, 2),
}
```

### Fields That Need Adding Across Agents
```python
# MISSING in retrieval_metrics across all agents:
#   "intent": state.get("intent", "")     -- needs to be set in each *_node
#   "tier": "compound|keyword|llm"        -- only supervisor knows this; needs to be in state

# MISSING in verify/generate/analyze agents:
#   "json_parse_success": not result.get("_parse_failed", False)
#   "retrieved_docs": len(...)            -- count varies per agent

# supervisor.py already logs tier but doesn't put it in state:
#   logger.info("Supervisor: keyword intent=%s query=%r", ...)
#   logger.info("Supervisor: llm intent=%s query=%r raw=%r", ...)
#   logger.info("Supervisor: compound [%s, %s] query=%r", ...)
# Need to add "tier" field to state return dict
```

### Eval Script Structure
```python
# tests/eval/run_eval.py -- outline
import json
import httpx
import time
import sys

BASE_URL = "http://localhost:8001/api/v1"

def load_dataset() -> list[dict]:
    with open("tests/eval/dataset.json") as f:
        return json.load(f)

def run_single(client: httpx.Client, entry: dict) -> dict:
    """Run one eval query, return result with timing."""
    t0 = time.perf_counter()
    resp = client.post(f"{BASE_URL}/chat/", json={
        "query": entry["query"],
        "session_id": f"eval-{entry['id']}",
    }, timeout=120)
    elapsed = time.perf_counter() - t0
    data = resp.json()
    return {
        "id": entry["id"],
        "status": resp.status_code,
        "intent": data.get("intent"),
        "expected_intent": entry["expected_intent"],
        "routing_correct": data.get("intent") == entry["expected_intent"],
        "json_valid": _check_json_validity(data, entry),
        "elapsed_s": round(elapsed, 2),
    }

def compute_metrics(results: list[dict]) -> dict:
    routing = [r for r in results if r["expected_intent"]]
    json_tests = [r for r in results if r.get("json_valid") is not None]
    return {
        "routing_accuracy": sum(r["routing_correct"] for r in routing) / len(routing) if routing else 0,
        "json_validity_rate": sum(r["json_valid"] for r in json_tests) / len(json_tests) if json_tests else 0,
        "avg_elapsed_s": sum(r["elapsed_s"] for r in results) / len(results) if results else 0,
        "total": len(results),
        "errors": sum(1 for r in results if r["status"] != 200),
    }
```

### Debug Retrieval Endpoint Structure
```python
# app/api/v1/debug.py
from fastapi import APIRouter, Query
from ...core.config import get_settings
from ...tools.qdrant_search import qdrant_search
from ...tools.bm25_search import bm25_search
from ...tools.reranker import reranker

router = APIRouter(prefix="/debug", tags=["debug"])

@router.get("/retrieval")
async def debug_retrieval(
    q: str = Query(..., min_length=1, description="Search query"),
    collection: str | None = None,
):
    """Run retrieval pipeline without LLM generation. Returns all stage scores."""
    import asyncio
    s = get_settings()
    coll = collection or s.qdrant_collection

    # Run in thread pool (same pattern as _run_graph_async in chat.py)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _retrieval_pipeline, q, coll, s)
    return result
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Text logs only | MLflow tracing + structured metrics | Already integrated (tracing.py) | Automatic LLM call tracking via autolog |
| Manual eval via API client | Automated eval suite with dataset | Phase 1 introduces this | Reproducible baseline metrics |
| No debug endpoint | Retrieval diagnostics without LLM | Phase 1 introduces this | Phase 3 calibration depends on this |

**Already implemented (don't redo):**
- MLflow service in docker-compose.yml with MinIO artifacts, health checks
- `setup_mlflow()` and `trace_node()` in tracing.py
- `register_all_prompts()` for prompt versioning
- `retrieval_metrics` dict in search, verify, generate, analyze agents
- `safe_parse_json()` with `_parse_failed` flag

## Key Technical Findings

### 1. MLflow Integration Status (HIGH confidence)
**Already done:**
- `tracing.py`: `setup_mlflow()`, `trace_node()`, `register_all_prompts()` -- all implemented
- `config.py`: `mlflow_enabled`, `mlflow_tracking_uri`, `mlflow_experiment_name` settings exist
- `docker-compose.yml`: Full MLflow service with MinIO artifact store, health checks
- `main.py`: `setup_mlflow(s)` and `register_all_prompts(s)` called in lifespan startup
- `workflow.py`: `trace_node` imported and used: `builder.add_node("search", trace_node(search_node))`

**Missing:**
- `MLFLOW_ENABLED` defaults to `false` in both config.py (line 195) and docker-compose.yml (line 59)
- `.env.example` does not mention `MLFLOW_ENABLED` at all
- Need to flip default to `true` and add to `.env.example`

### 2. Existing Log Fields vs Required (HIGH confidence)
**Already logged per agent:**
- `search_agent`: 15 metrics including `best_rerank_score`, `elapsed_s`, `has_context`, all hit counts
- `verify_agent`: `node`, `doc_content_len`, `risk_score`, `elapsed_s` + text log of `compliant_label`
- `generate_agent`: `node`, `elapsed_s` + plan details
- `analyze_agent`: `node`, `task`, `elapsed_s`, `hit_count`, `best_rerank_score`
- `supervisor`: text log of tier (compound/keyword/llm) and intent

**Missing fields (need adding):**
- `intent` -- not in retrieval_metrics of any agent (only in state, not in metrics dict)
- `tier` -- supervisor logs it as text but doesn't add to state; downstream agents can't include it
- `retrieved_docs` -- search has it; verify/generate/analyze don't consistently log it
- `json_parse_success` -- safe_parse_json sets `_parse_failed` flag but no agent puts this in metrics
- JSON log format -- all logging uses text format via `logging.basicConfig`

### 3. Eval Dataset Format (HIGH confidence)
**Recommendation: JSON file**, because:
- Native Python stdlib (no extra dependency)
- Easy to validate with Pydantic
- Git-friendly (diff-able)
- Can be loaded by any tool (not pytest-specific)

**Categories needed (from REQUIREMENTS.md):**
- Routing tests (FR-3): 10+ queries with expected_intent
- Retrieval tests (FR-2): 10+ queries with expected_docs in top-5
- JSON validity tests (FR-1): 10+ queries to verify/generate/analyze agents
- Answer quality tests (FR-4): queries with expected key facts

### 4. Debug Retrieval Endpoint Design (HIGH confidence)
**Stages to expose (from search_agent.py pipeline):**
1. Query preparation (original + expanded query)
2. Exact search detection
3. Vector search hits (count + top scores)
4. BM25 hits (count + top scores)
5. Graph enrichment (sections, articles, obligations, entities, laws -- counts)
6. Merged hits (total count after dedup)
7. Relevance-filtered hits (count + threshold used)
8. Reranked hits (count + all rerank_scores + rerank_logits)
9. Has_context flag + best_score vs search_min_confidence threshold

**Implementation approach:** Call the same tools directly (qdrant_search, bm25_search, reranker) rather than importing search_agent private functions. This keeps the debug endpoint decoupled.

### 5. Test Infrastructure (HIGH confidence)
- Framework: **pytest** (already in dev deps, existing test uses it)
- Existing test: `tests/test_analyze_classify.py` (82 lines, parametrized unit tests)
- Run command: `python -m pytest tests/ -v` (in Makefile)
- No conftest.py, no fixtures, no test config file (pytest.ini / pyproject.toml [tool.pytest])
- Eval script is standalone (`python tests/eval/run_eval.py`), not a pytest test

### 6. json_parse_success Tracking (HIGH confidence)
**Where safe_parse_json is called:**
- `verify_agent.py` line 257: `result = safe_parse_json(raw, fallback)` -- fallback has `_parse_failed: True`
- `generate_agent.py` line 151: `safe_parse_json(raw, {...})` -- for plan parsing
- `analyze_agent.py` line 376: `safe_parse_json(raw, fallback)` -- for compare/extract tasks

**How to track:** After `safe_parse_json` call, check `not result.get("_parse_failed", False)`. Add `"json_parse_success": True/False` to the retrieval_metrics dict. For agents that don't produce JSON (search, qa mode of analyze), set to `null` or omit.

### 7. Supervisor Tier State Gap (MEDIUM confidence)
**Issue:** Supervisor logs tier (compound/keyword/llm) in text logs but does NOT put it in state. Downstream agents and retrieval_metrics cannot include `tier` without it being in state.

**Fix:** Add `"tier"` field to the return dict of `classify_intent()`:
```python
return {
    **state,
    "intent": kw_intent,
    "intents": [kw_intent],
    "tier": "keyword",  # NEW
    ...
}
```
And add `tier: str` to `AgentState` TypedDict.

## Open Questions

1. **What documents are in the Qdrant collection for eval?**
   - What we know: Collection is `bank_knowledge`, contains banking/legal documents in Russian
   - What's unclear: Which specific documents are indexed -- needed to write realistic `expected_docs` in eval dataset
   - Recommendation: During implementation, query `GET /api/v1/documents` or Qdrant directly to list indexed documents. Write eval queries that reference known documents.

2. **Should eval script use REST API or direct graph.invoke()?**
   - What we know: ROADMAP says "запускает все запросы и выводит baseline metrics"
   - What's unclear: Whether it should test through the API (more realistic, includes serialization/routing) or call graph directly (faster, no server needed)
   - Recommendation: Use REST API via httpx. This tests the full stack including API layer, which is closer to production behavior. Requires running server.

3. **Eval dataset content for retrieval tests**
   - What we know: Need 30+ queries with expected outcomes
   - What's unclear: Without knowing exact indexed documents, retrieval tests can only be "soft" (expect certain document types, not specific filenames)
   - Recommendation: Start with routing + JSON validity tests (don't need specific docs). Add retrieval tests after checking what's actually indexed. Eval dataset is versioned in git and can be updated.

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis of all files in `langgraph-agent/` (tracing.py, config.py, docker-compose.yml, supervisor.py, search_agent.py, verify_agent.py, generate_agent.py, analyze_agent.py, main.py, chat.py, llm.py, reranker.py, state.py, utils.py, prompts/__init__.py, pyproject.toml, requirements.txt, Makefile, .env.example)
- Existing test file: tests/test_analyze_classify.py

### Secondary (MEDIUM confidence)
- python-json-logger library: well-known stdlib-compatible JSON log formatter

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - all libraries already in requirements.txt except python-json-logger
- Architecture: HIGH - patterns derived directly from reading existing codebase structure
- Pitfalls: HIGH - identified from actual code analysis (not theoretical)
- Eval design: MEDIUM - eval dataset content depends on what's indexed (unknown until runtime)

**Research date:** 2026-03-17
**Valid until:** 2026-04-17 (stable -- no fast-moving dependencies)
