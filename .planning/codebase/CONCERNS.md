# Codebase Concerns — Tech Debt, Fragile Areas, Performance, Security

> Analyzed: March 2026
> Scope: `documentologist-miran-service--neo4j` — all services (langgraph-agent, ocr-service, bank_knowledge, docker-compose infra)

---

## 1. Double-Retrieval in the Compare Path (Performance / Correctness)

**Files:** `langgraph-agent/app/agents/analyze_agent.py` (lines 476–491)

The `compare` task branch in `analyze_node` executes **two separate Qdrant calls** via `_retrieve_compare_pair`, then immediately discards those raw results and makes **two more** full retrieval passes through `_retrieve_and_rerank` (which itself calls Qdrant again internally via `qdrant_search.invoke`):

```python
hits_a, hits_b, used_pair = _retrieve_compare_pair(query, s)     # 2 × Qdrant embed + search
reranked_a, score_a, ok_a = _retrieve_and_rerank(query_a, ...)   # 2 × Qdrant embed + search (again)
reranked_b, score_b, ok_b = _retrieve_and_rerank(query_b, ...)
```

`hits_a` and `hits_b` from `_retrieve_compare_pair` are computed and then never used — `all_hits` is built from `reranked_a + reranked_b` only. Each `_retrieve_and_rerank` call itself runs another vector search for the same subject. The net effect is **4 embedding + Qdrant round-trips** per compare query where 2 would suffice. The embedding calls alone each touch Ollama over HTTP.

**Impact:** A compare query typically takes 2× longer than it should on the Qdrant + embedding side, before the heavy LLM calls even begin.

---

## 2. Synchronous LangGraph Execution Blocks the Async Event Loop

**File:** `langgraph-agent/app/api/v1/chat.py` (lines 125–130)

```python
async def _run_graph_async(initial_state: dict) -> dict:
    loop = asyncio.get_event_loop()
    coro = loop.run_in_executor(None, graph.invoke, initial_state)
    return await asyncio.wait_for(coro, timeout=s.graph_timeout_seconds)
```

The entire graph — including Neo4j sessions, cross-encoder inference, Ollama HTTP calls, and MinIO uploads — runs on the default `ThreadPoolExecutor`. All of these I/O operations are synchronous and blocking. Under concurrent load, the shared executor pool fills up, starving the FastAPI event loop. There is no bounded thread pool specified; the default executor uses `min(32, cpu_count + 4)` threads, which may not be enough when `graph_timeout_seconds=240` and requests pile up.

**Impact:** Under moderate concurrent load (>4–5 simultaneous users), response latency degrades non-linearly. There is no backpressure or request queue — FastAPI will accept new connections while executor threads are saturated.

---

## 3. Rate Limiter Uses In-Memory Storage (Single Process Only)

**File:** `langgraph-agent/app/core/rate_limit.py` (lines 7–11)

```python
# Keyed by client IP; uses in-memory storage by default (suitable for single-process).
# For multi-process/multi-replica deployments, swap storage_uri to Redis:
limiter = Limiter(key_func=get_remote_address)
```

The comment acknowledges this directly but the fix is not applied. If the service is scaled to multiple processes or replicas (e.g. with gunicorn workers or Kubernetes pods), every replica gets its own counter and the 30/minute limit becomes 30 × N per minute effectively. Redis is already deployed in the stack (`miran-redis`) but is unused here.

**Impact:** Rate limiting provides no real protection in any multi-process deployment.

---

## 4. In-Memory Job Registry in bank_knowledge Indexer (State Lost on Restart)

**File:** `bank_knowledge/indexer_api.py` (lines 52–87)

The indexer maintains a `_jobs` dict entirely in process memory with a 24-hour TTL cleanup thread. If the `bank-knowledge` container restarts mid-indexing:
- All in-flight and queued job records vanish.
- The OCR service's `task_notify_indexer` has already fired its 2 retries and given up.
- The document appears as `completed` in PostgreSQL but is never indexed into Qdrant or Neo4j.
- The user can search and get no results, with no error shown.

There is no recovery mechanism — no way to detect that an `ocr_service` document is completed but has zero Qdrant vectors. Additionally, a new `Minio` client is instantiated on every `_run_indexing` call rather than being reused as a singleton.

**Impact:** Any container restart during indexing silently produces a document that appears processed but is unsearchable.

---

## 5. OCR Worker: Single-Process Polling Loop, No Parallelism

**File:** `ocr-service/workers/main.py` (lines 132–188)

The worker processes one document at a time sequentially. The `_OCR_SEMAPHORE` in `pipeline.py` has `ocr_concurrency_limit` (default 1). While this is intentional to avoid OOM on the GPU, there is no mechanism to scale horizontally — multiple workers would race on the same `PENDING` queue row without a row-level lock or `SELECT ... FOR UPDATE SKIP LOCKED`. Starting a second worker container would cause the same document to be double-processed.

The polling loop also has a silent failure mode: if the DB connection drops inside `get_sync_db()`, the loop catches the exception, doubles the sleep interval, and continues — but documents that arrived during the outage accumulate silently in `PENDING` state. No alerting or dead-letter queue exists.

**Impact:** The OCR pipeline cannot safely scale beyond one worker, and DB disruptions create invisible backlogs.

---

## 6. `neo4j_password` Default Is "password" in Settings

**File:** `langgraph-agent/app/core/config.py` (line 56)

```python
neo4j_password: str = "password"
```

The default is a well-known weak password. If the `.env` file is missing or `NEO4J_PASSWORD` is not set, the agent connects with `neo4j/password`. The `docker-compose.yml` correctly uses `${NEO4J_PASSWORD:?NEO4J_PASSWORD must be set}` to enforce the variable, but the `Settings` default is a fallback for any non-Docker deployment path or local dev that forgets to set `.env`. The same applies to `minio_access_key: str = "minioadmin"` and `minio_secret_key: str = "minioadmin"`.

**Impact:** A development instance accidentally exposed with default credentials has open Neo4j and MinIO access.

---

## 7. Graph Execution Timeout Is a Blunt Instrument

**File:** `langgraph-agent/app/core/config.py` (line 101), `langgraph-agent/app/api/v1/chat.py` (line 130)

`graph_timeout_seconds = 240` applies to the entire graph invocation, including memory load, supervisor LLM call, all agent LLM passes, and memory save. The generate agent alone does 3 LLM passes (`_plan_document` → `_generate_draft` → `_validate_draft`) each with `num_predict=2000` on a 14b model. When Ollama is under load, a single generate request can easily consume 180–200 seconds, leaving no headroom for multi-intent flows (e.g. `generate + verify` would exceed the timeout consistently).

There is no per-node timeout — `graph_enrichment_timeout=8s` exists for the Neo4j parallel step only. All other nodes can hang indefinitely until the outer 240-second wall hits.

**Impact:** Multi-intent requests involving `generate` will frequently timeout in production, returning a 504 with no partial result. The user sees no intermediate output.

---

## 8. Cross-Encoder Model Download Blocks First Request

**File:** `langgraph-agent/app/tools/reranker.py` (lines 32–66)

```python
@lru_cache(maxsize=1)
def _get_cross_encoder():
```

The `CrossEncoder` is loaded lazily on the first rerank call. If the model is not already cached by HuggingFace (`~/.cache`), this triggers a download from the Hub on the first live request, which can take 30–120 seconds depending on network. No pre-warming occurs at startup, unlike the OCR worker's `pre_warm_ocr()`. The first user after a fresh deployment faces a timeout-or-slow-response.

**Impact:** Cold-start degradation on fresh container deploys; the first 1–3 requests may timeout or stall.

---

## 9. CORS Origins Are Statically Listed and Include Localhost in Production

**File:** `langgraph-agent/app/core/config.py` (lines 25–27)

```python
cors_origins: list[str] = Field(
    default=["http://localhost:3000", "http://localhost:8080"],
)
```

The default CORS list allows `localhost:3000` and `localhost:8080`. In `docker-compose.yml`, `CORS_ORIGINS` is set to `${CORS_ORIGINS:-["http://localhost:3000"]}` — the fallback keeps localhost active. In a production deployment where the frontend is served from a different domain, this either allows all localhost origins (potential CSRF vector) or the developer forgets to set `CORS_ORIGINS` and the frontend can't connect at all.

**Impact:** Either overly permissive CORS in production or a broken deployment that requires environment variable debugging.

---

## 10. Qdrant Full-Scan Fallback Is O(n) and Logs at WARNING Level Always

**File:** `langgraph-agent/app/tools/qdrant_search.py` (lines 217–266)

When the Qdrant server-side `MatchText` filter returns no results, `qdrant_text_search` falls back to a Python-side full collection scan of up to `50 × 200 = 10,000` chunks. The scan is capped but still iterates every stored chunk in batches. This path is triggered by a `logger.warning` that fires on every exact-search query where the index has not been populated or is temporarily unavailable. The warning appears in normal operation and creates noise that masks real warnings.

Additionally, the fallback scan is O(n) in collection size — a 100,000-chunk collection would require 500 batches (capped at 50, so 10,000 chunks scanned). For exact-text search queries on large collections, results will be silently incomplete.

**Impact:** Exact-search queries degrade silently at scale; warning logs are polluted, making real issues harder to spot.

---

## 11. Supervisor LLM Fallback Has No Retry on Empty Response

**File:** `langgraph-agent/app/agents/supervisor.py` (lines 193–207)

```python
raw = invoke_with_retry(llm, [...]) or "search"
intents = _detect_intents_from_llm(query, raw)
```

`invoke_with_retry` falls back to `""` on repeated failure (see `llm.py` line 135). The `or "search"` default kicks in, routing every failed LLM classification to `search`. This is a sensible default but means classification failures are invisible — the user gets a search result when they may have asked for document generation or compliance verification. There is no metric or counter for how often the LLM fallback fires with empty output.

**Impact:** LLM degradation silently causes wrong agent selection; no alerting.

---

## 12. Presigned MinIO URLs Expire in 7 Days with No Warning

**File:** `langgraph-agent/app/agents/generate_agent.py` (lines 211–230)

```python
return url_client.presigned_get_object(
    s.minio_bucket, object_name, expires=timedelta(days=7)
)
```

Generated document download links expire after 7 days. Conversation history stored in Redis/PostgreSQL (`session_ttl_seconds=3600` for Redis, no expiry set for PG) may contain these URLs long after they stop working. There is no cleanup job for expired exports, no notification to users, and no mechanism to regenerate the link on demand.

**Impact:** Users who return to a conversation after a week find broken download links with no explanation.

---

## 13. LLM-Generated JSON Is Parsed with a Brace-Counting Fallback That Can Silently Corrupt Data

**File:** `langgraph-agent/app/core/utils.py` (lines 202–245)

`safe_parse_json` uses brace counting to find the first `{...}` block in LLM output. If the LLM produces two JSON objects (e.g. reasoning text + JSON), only the first is returned. If the first object is incomplete or is JSON inside a code fence with pre-preamble text, the brace counter may extract an inner nested object rather than the intended top-level one. The function returns the fallback dict on parse failure, which for `analyze_agent` includes `"confidence": 0.5` — a plausible-looking value that conceals the failure.

**Impact:** Structured JSON tasks (`compare`, `extract`) may silently return partial or fallback data that looks valid but contains no real content.

---

## 14. Neo4j CONTAINS Fallback Has No Score and Bypasses the Lucene Index

**File:** `langgraph-agent/app/tools/neo4j_query.py` (lines 130–147)

When the Neo4j fulltext index is unavailable, `graph_section_search` falls back to:

```cypher
WHERE toLower(s.text_preview) CONTAINS toLower($kw)
```

This is a full node-scan with no index support. On a graph with tens of thousands of Section nodes this is a table-scan equivalent. The fallback also returns no `score` field, so the downstream normalization in `_normalize_graph_hits` sets `score=0.0` for all fallback sections, and they are placed at the bottom of the merged candidate pool before reranking.

**Impact:** If the fulltext index is missing (new deployment, Neo4j restart, schema migration), graph enrichment degrades severely in both latency and quality without any alert.

---

## 15. No Authentication on Any Endpoint

**Files:** `langgraph-agent/app/api/v1/chat.py`, `langgraph-agent/app/api/v1/sessions.py`, `langgraph-agent/app/api/v1/documents.py`, `bank_knowledge/indexer_api.py`

None of the FastAPI routers implement authentication. The only protection is rate limiting (30/minute by IP) and input validation. Anyone on the Docker network or with host port access can:
- Submit arbitrary queries to the LLM agent
- Access session history for any `session_id` (sessions are identified by an opaque UUID but not scoped to a user)
- Trigger document indexing via the `bank-knowledge` webhook

The `user_id` field in `ChatRequest` is validated for format but never verified — any caller can claim any `user_id` string.

**Impact:** No confidentiality of sessions; the indexer webhook is unauthenticated and could be abused to trigger resource-expensive indexing jobs.

---

## 16. Prefect Server Is a Hard Dependency for OCR Pipeline But Bypassed in Practice

**Files:** `ocr-service/workers/pipeline.py`, `ocr-service/workers/main.py`

`document_processing_pipeline` is decorated as a `@flow` and tasks as `@task`, but the worker's comment explicitly explains why Prefect's `serve()` is not used (subprocess spawning causes model reload OOM). The polling loop calls the flow function directly in-process. The `PREFECT_API_URL` environment variable is set for all three OCR containers (`ocr-api`, `ocr-worker`, `ocr-frontend`), and `prefect-server` is a health-checked `depends_on`. However, the `_OCR_SEMAPHORE` comment says the semaphore was chosen specifically to have "zero dependency on a running Prefect API server."

This creates a split-brain: Prefect infra is running and consuming resources (PostgreSQL DB + server process), but the actual execution path does not use it. Task retry/retry_delay_seconds decorators on `@task` functions only apply when executed via Prefect runtime, not when called directly. In the current polling loop, `retries=2` on `task_ocr_analysis` has no effect — a single OCR failure terminates the pipeline immediately.

**Impact:** The retry logic on Prefect tasks is silently non-functional. Prefect server is consuming resources for no operational benefit.

---

## 17. generate_agent Hardcodes 3-Hour-Old File Path in Local Exports

**File:** `langgraph-agent/app/core/config.py` (line 84)

```python
export_dir: str = "/tmp/agent_exports"
```

DOCX/PDF files are written to `/tmp/agent_exports` before being uploaded to MinIO. `/tmp` is ephemeral — on container restart, all local exports are lost. The `agent_exports` Docker volume is mounted at `/exports` (not `/tmp`), which means the volume provides persistence but the default `export_dir` writes to the wrong location unless `EXPORT_DIR=/exports` is set in the environment. In `docker-compose.yml`, `EXPORT_DIR: /exports` is set correctly, but local/non-Docker development uses `/tmp` and loses files on each run.

**Impact:** Exports in non-Docker dev environments are always lost after process restart; if `EXPORT_DIR` env var is missing in Docker, exports hit `/tmp` and the volume mount provides no benefit.

---

## 18. graph_section_search Uses an F-String Index Name — Potential Injection Vector

**File:** `langgraph-agent/app/tools/neo4j_query.py` (lines 106–120)

```python
fulltext_index = get_settings().neo4j_fulltext_index
cypher = f"""
    CALL db.index.fulltext.queryNodes('{fulltext_index}', $kw)
```

The index name `fulltext_index` is interpolated directly into the Cypher string (not parameterized). Index names cannot be passed as query parameters in Cypher, so this is a known limitation of Neo4j's driver. However, the value comes from `Settings.neo4j_fulltext_index` which is read from the `.env` file. If someone injects into `NEO4J_FULLTEXT_INDEX`, the Cypher string is directly affected. While this is an admin-controlled setting, it violates the principle of no user-controlled strings in Cypher templates.

**Impact:** Low-severity — index name is config-controlled, not user-input. But the pattern is fragile and deserves a allowlist validation at startup.

---

## 19. Redis Session TTL vs. PostgreSQL Long-Term Memory Are Not Coordinated

**Files:** `langgraph-agent/app/core/config.py` (line 67), `langgraph-agent/app/agents/memory_agent.py`

`session_ttl_seconds = 3600` means Redis session data expires after 1 hour. Long-term memory is stored in PostgreSQL with no TTL (the config shows no `pg_session_ttl` setting). Over time, the PostgreSQL `agent_memory` database accumulates all historical sessions with no pruning. Sessions that exist in PG but have expired Redis keys will load partial history (PG data only) without any indication that the Redis portion is missing.

**Impact:** Unbounded growth of the `agent_memory` PostgreSQL database; no cleanup strategy for stale sessions.

---

## 20. Missing Features / Not Implemented

- **No document deletion from Qdrant/Neo4j:** Once a document is indexed, there is no API to remove its vectors or graph nodes. Re-indexing the same document creates duplicate vectors.
- **No embedding model version pinning:** `bge-m3:latest` in `OLLAMA_EMBEDDING_MODEL` — if the model is updated via `ollama pull`, embedding vectors from old documents become incompatible with new embeddings. No re-indexing pipeline exists.
- **No health check for Ollama:** The langgraph-agent startup does not verify Ollama is reachable or that the required models (`qwen2.5:14b`, `qwen2.5:7b`, `bge-m3`) are pulled. The first real request will hang or timeout if models are missing.
- **No backpressure on the indexing queue:** The `bank-knowledge` indexer accepts an unbounded number of `POST /api/v1/index` requests. Each spawns a background thread via FastAPI `BackgroundTasks`. A burst of document uploads will create as many concurrent embedding threads as there are uploads, saturating Ollama and causing all of them to timeout.
- **No structured logging / tracing by default:** `mlflow_enabled: bool = False` — MLflow is off by default. The only observability is `logger.info` with `retrieval_metrics` dicts. No request tracing, no distributed trace IDs linking OCR → indexer → agent queries for a given document.
- **No input size validation on OCR upload beyond MB limit:** The `max_upload_size_mb=50` limit prevents large files, but there is no page count pre-check. A 50 MB 200-page scanned PDF will run the Surya OCR pipeline to completion regardless of the 300-second timeout, potentially producing an incomplete OCR result.
- **Qdrant `qdrant_named_vector = "q_vec"` may not exist:** If the `bank_knowledge` indexer creates the collection with a different named vector key, the named-vector search silently falls back to the default vector on every query (the `except` in `qdrant_search` catches `UnexpectedResponse` silently at DEBUG level). There is no startup check to verify the collection schema matches the expected named vector.
