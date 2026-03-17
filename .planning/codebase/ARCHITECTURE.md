# Architecture — Miran Service (Neo4j Edition)

## Overview

Miran is a multi-agent RAG (Retrieval-Augmented Generation) system specialised for Kazakhstani banking and legal documents. It processes Russian-language documents through a three-stage pipeline: OCR ingestion, knowledge indexing, and multi-intent agent query resolution.

The system is fully containerised and defined in the root `docker-compose.yml`. It is designed for GPU-accelerated inference but gracefully degrades to CPU.

---

## Service Topology

Defined in `docker-compose.yml`. All services share the `miran-net` bridge network.

```
User / Client
    |
    ├── [8501] chainlit-ui       (Chainlit chat interface)
    ├── [8001] langgraph-agent   (FastAPI multi-agent RAG API)
    ├── [8000] ocr-api           (FastAPI document upload API)
    ├── [8504] ocr-frontend      (Streamlit upload/browsing UI)
    └── [8505] bank-knowledge-ui (Streamlit collection UI)

Internal pipeline services:
    ocr-worker        (Surya OCR inference worker, GPU)
    bank-knowledge    (Indexer webhook server, port 8002, GPU)
    prefect-server    (Flow orchestration UI, port 4201)

Shared infrastructure:
    postgres          (port 5434) — OCR DB, agent memory, Prefect state
    minio             (port 9002) — object storage (3 buckets)
    redis             (port 6380) — session short-term memory
    qdrant            (port 6335) — vector store
    neo4j             (port 7475/7688) — knowledge graph
```

### MinIO Buckets
- `source-files` — raw uploaded PDFs/images
- `analysis-results` — Surya OCR JSON output
- `agent-exports` — generated DOCX/PDF documents

### PostgreSQL Databases (multi-DB, single instance)
- `ocr_service` — document records and status tracking
- `prefect` — Prefect flow run state
- `agent_memory` — LangGraph agent conversation audit log

---

## Full Document Processing Pipeline

```
Step 1 — Upload
  POST /api/v1/ingest (ocr-api:8000)
    → store file in MinIO (source-files)
    → create DB record (status: pending)
    → trigger Prefect flow via prefect-server

Step 1.5 — OCR (ocr-worker, Surya on CUDA)
  Prefect flow: document_processing_pipeline
    task_initialize      → mark status: processing, verify MinIO
    task_ocr_analysis    → download file from MinIO, run Surya OCR
    task_data_structuring → build structured JSON with metadata
    task_save_and_update  → upload JSON to MinIO (analysis-results), mark completed
    task_notify_indexer   → POST to bank-knowledge:8002/api/v1/index (non-fatal)

Step 2 — Indexing (bank-knowledge:8002)
  Indexer webhook receives doc_id + result_path
    → download Surya JSON from MinIO
    → run Prefect flow: ocr_surya_to_qdrant_flow
        transform_surya_ocr_task   → chunk text (max 2000 chars, 200 overlap)
        build_points_task          → generate dual embeddings via Ollama (bge-m3)
        upsert_points_task         → upsert to Qdrant (q_vec + qa_vec, 1024 dims)
    → (Neo4j graph population is handled separately via graph-building flows)

Step 3 — Query (langgraph-agent:8001)
  POST /api/v1/chat or SSE chat
    → LangGraph StateGraph executes the multi-agent pipeline
```

---

## LangGraph Agent Pipeline

### StateGraph

Defined in `langgraph-agent/app/graph/workflow.py`. Compiled as a singleton at module import.

```
START
  → memory_load        (load Redis session history)
  → supervisor         (classify intent)
  → [conditional edge on state["intent"]]
      → ingest | search | verify | generate | analyze
  → [conditional: more intents?]
      → advance_intent → next agent  (multi-intent loop)
      → memory_save                  (single intent or last intent)
  → END
```

All agent nodes are wrapped by `trace_node` (MLflow child spans). Memory and advance_intent nodes are left unwrapped.

### Shared State

`AgentState` (TypedDict) in `langgraph-agent/app/graph/state.py` is the single data structure passed between all nodes:

- Conversation: `messages`, `user_query`, `session_id`, `user_id`
- Routing: `intent` (primary), `intents` (list for multi-intent), `document_ids`
- Query processing: `query_expanded` (LLM-rewritten for vector retrieval)
- Retrieved context: `vector_hits`, `bm25_hits`, `graph_hits`, `reranked_docs`
- Agent outputs: `ingest_result`, `search_result`, `verify_result`, `generate_result`, `analyze_result`
- Multi-intent accumulation: `combined_responses`
- Final: `final_response`, `citations`, `export_path`
- Observability: `retrieval_metrics` (RetrievalMetrics TypedDict)

---

## Supervisor Agent

`langgraph-agent/app/agents/supervisor.py`

Three-tier intent classification, cheapest-first:

1. **Compound regex patterns** — detects two co-occurring Russian-language intents ("найди и проверь" → `["search", "verify"]`). Checked first to prevent collapsing multi-intent queries.
2. **Keyword regex override** — high-confidence single-intent patterns (ingest, analyze, generate, verify, search) — zero LLM cost. Ordering is intentional: ingest > analyze > generate > verify > search to prevent overly broad patterns from winning.
3. **Draft LLM fallback** — stateless call to the 7b model (qwen2.5:7b) for genuinely ambiguous queries. Stateless by design: conversation history caused context contamination in classification.

Valid intents: `ingest | search | verify | generate | analyze`

---

## Individual Agents

### Search Agent (`search_agent.py`)

13-stage hybrid RAG pipeline:

1. `_prepare_query` — strip conversational prefix, enrich referential queries with last-mentioned filename, LLM query expansion (7b draft model)
2. `_detect_exact_search` — detect quoted/prefixed exact-match requests
3. `_retrieve_exact` — Qdrant `MatchText` server-side search for literal strings
4. `_retrieve_vector_bm25` — Qdrant vector search (top 40) + BM25 lexical reranking over results
5. `_retrieve_graph` — 5 parallel Neo4j queries (sections, articles by number, obligations, named entities, law references) via a module-level `ThreadPoolExecutor` with 8s timeout
6. `_normalize_graph_hits` — convert Neo4j records to unified hit dict format
7. `_merge_all_hits` — deduplicate all sources by ID
8. `_filter_by_relevance` — drop hits below `min_relevance_score` (0.35), fallback to all if empty
9. `_rerank_and_calibrate` — cross-encoder reranking (mmarco-mMiniLMv2-L12-H384-v1, CUDA) over top 30 candidates, sigmoid confidence check
10. `_build_llm_prompt` — assemble numbered context blocks with source headers
11. `_generate_answer` — call 14b model with system prompt + capped conversation history (3 turns)
12. `_build_citations` — structured citation list per reranked doc
13. Returns updated `AgentState` with metrics

### Analyze Agent (`analyze_agent.py`)

Classifies into sub-tasks before retrieval:
- `qa` — default, standard vector→BM25→graph→rerank pipeline
- `compare` — extracts two subjects ("сравни X и Y"), fetches two independent Qdrant searches, reranks each side separately, builds two-sided context block
- `extract` — same as qa but uses JSON LLM (`get_json_llm`) for structured entity extraction
- `summary` — broader hit limit (15) for coverage

Output format: compare/extract → JSON parse + markdown rendering; qa/summary → free text.

### Verify Agent (`verify_agent.py`)

Compliance checking pipeline:
1. Document resolution: explicit `document_ids` → Qdrant scroll; pasted document detection (length threshold + structural signals regex: ДОГОВОР, Статья N, п. N, etc.); fallback to search
2. Legal context: vector(40) → BM25 → cross-encoder(top 7) × 1000 chars + Neo4j sections + Neo4j obligations
3. Stateless LLM JSON call (no history — history caused contamination between verify queries)
4. JSON parse + consistency guard: overrides `compliant=True` if violation signals found in issues text or risk_score ≥ 7
5. Markdown rendering with emoji compliance indicator

### Generate Agent (`generate_agent.py`)

Legal document generation:
1. Qdrant search (10 hits × 1000 chars) + Neo4j sections (4 × 600 chars) + Neo4j obligations
2. JSON LLM: document plan (template_type, title, sections, export_format)
3. `doc_generate` tool: expand plan to full draft text
4. Draft LLM validation pass: add missing sections, fix legal wording, add NPA references
5. DOCX or PDF export → MinIO upload (presigned URL, 7-day TTL)
6. Response shows full document text in chat (up to 4000 chars) + download link

### Ingest Agent (`ingest_agent.py`)

Status tracking and user guidance:
- Extracts UUID doc_id from query or `document_ids` state
- Calls `ocr_check_status` tool (proxies to ocr-api)
- When OCR is `completed`, probes Qdrant directly for chunks (the only reliable signal that indexing completed)
- Lists recent uploads when no doc_id is present

### Memory Agent (`memory_agent.py`)

Dual-store session persistence:
- `memory_load_node`: loads up to 20 messages from Redis at request start. Window is intentionally larger than the 6 messages (3 turns × 2) passed to LLMs, to support `extract_recent_filename()` referential resolution
- `memory_save_node`: saves user query + response to Redis (primary, TTL-based) and PostgreSQL (audit log, append-only). The two stores are not atomically consistent; Redis is authoritative

---

## Retrieval Architecture

### Qdrant Vector Store

- Named dual-vector collection: `q_vec` (query) and `qa_vec` (QA pair), both 1024-dimensional cosine
- Embedding model: `bge-m3:latest` via Ollama (multilingual, 1024 dims)
- Indexed payload fields: `audience`, `product`, `product_group`, `category`, `document_title`
- Key tool: `langgraph-agent/app/tools/qdrant_search.py` — implements `qdrant_search`, `qdrant_text_search` (MatchText), `qdrant_scroll_by_doc_ids`

### BM25 Lexical Reranking

`langgraph-agent/app/tools/bm25_search.py` — runs BM25 over the Qdrant vector hits in-process. Not a separate index; uses the returned content strings.

### Neo4j Knowledge Graph

`langgraph-agent/app/tools/neo4j_query.py` — Cypher query tools:
- `graph_section_search` — fulltext search on `Section.text_preview` index (`sectionText`)
- `graph_article_lookup` — Article nodes by number, linked to parent Section and Document
- `graph_obligation_search` — Obligation nodes (subject/action/object/deadline/evidence)
- `graph_entity_lookup` — named entity nodes
- `neo4j_query` — raw parameterised Cypher

Node labels used: `Document`, `Section`, `Article`, `Obligation`, `Law`, `Entity`

### Cross-Encoder Reranker

`langgraph-agent/app/tools/reranker.py` — loads `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual mMARCO, covers Russian). Falls back to `cross-encoder/ms-marco-MiniLM-L6-v2`. Runs on CUDA when available. Applied as the final precision filter before LLM generation.

---

## bank_knowledge Indexer

`bank_knowledge/indexer_api.py` — FastAPI webhook server (port 8002):
- `POST /api/v1/index` — enqueue indexing job, runs as `BackgroundTask`
- `GET /api/v1/index/{doc_id}/status` — check job status
- In-memory job registry with 24h TTL and hourly cleanup thread

Indexing flow (`bank_knowledge/bank_knowledge/prefect_flows.py`):
- `ocr_surya_to_qdrant_flow` — the primary webhook-triggered flow: transform Surya JSON → chunk → embed → upsert
- `ingest_qdrant_flow` — direct ingestion from any pre-processed file
- `labelstudio_to_qdrant_flow` — Label Studio annotation export → Qdrant
- `daily_ops_flow` — scheduled ingestion + optional model retraining
- `reindex_qdrant_flow` — full collection recreation

`PipelineService` (`bank_knowledge/bank_knowledge/pipeline_service.py`) — pure dataclass that wires Prefect task callables to pipeline steps. Tasks are injected via `PipelineTasks` dataclass, making the service independently testable.

Embedding backends (`bank_knowledge/bank_knowledge/qdrant_ops.py`):
- `OllamaPointBuilder` — async Ollama API calls (default, bge-m3)
- `HuggingFacePointBuilder` — local sentence-transformers

---

## OCR Service

`ocr-service/workers/pipeline.py` — Prefect 3.0 flow orchestrator:
- `document_processing_pipeline` — 5-task sequential pipeline
- Uses `threading.Semaphore` (not Prefect concurrency) to limit concurrent OCR jobs, removing the dependency on a running Prefect API server
- `task_notify_indexer` runs outside the main try/except block — indexer notification failure is explicitly non-fatal

`ocr-service/app/services/ocr.py` — Surya OCR wrapper
`ocr-service/app/services/storage.py` — MinIO client
`ocr-service/app/services/indexer_webhook.py` — HTTP call to bank-knowledge
`ocr-service/migrations/` — Alembic migrations for the `ocr_service` DB (4 versions)

---

## Key Design Patterns

### Three-Tier Classification (Supervisor)
Compound regex → keyword regex → draft LLM. Each tier is tried in order, short-circuiting on a match. Minimises LLM calls for common patterns.

### Stateless LLM calls in Verify
The verify agent deliberately does not pass conversation history to the compliance LLM. Previous verify answers contaminated subsequent checks. Only the search and analyze agents use session history.

### Non-fatal degradation everywhere
- Memory save failures (Redis, PostgreSQL) are logged at WARNING and do not fail the request
- Neo4j fulltext index creation at startup is non-fatal
- Indexer webhook notification is non-fatal (OCR already marked completed)
- MinIO export failures are non-fatal (document text is still returned in chat)
- MLflow tracing is no-op when disabled

### Singleton clients with double-checked locking
Neo4j driver, Qdrant client, MinIO clients, and the Neo4j thread pool are all module-level singletons initialised lazily with `threading.Lock()` double-checked locking.

### Multi-intent accumulation
`combined_responses: list[str]` in `AgentState` accumulates partial responses as the `advance_intent` node cycles through intents. `build_final_response()` prepends prior responses to the current one before returning.

### Dual-vector Qdrant schema
Each point carries two vectors: `q_vec` (question embedding) and `qa_vec` (QA-pair embedding). The search agent queries using `q_vec` by default. The named vector is configured via `qdrant_named_vector` setting.

### GPU deployment pattern
Three services use the shared `gpu-deploy` anchor in `docker-compose.yml`: `ocr-worker`, `bank-knowledge`, and `langgraph-agent`. Each gracefully degrades to CPU if no GPU is found. LLM inference (Ollama) uses the host GPU via `host.docker.internal`.

---

## Observability

- **MLflow**: optional tracing (`mlflow_enabled=false` by default). `trace_node` wraps agent nodes as MLflow child spans. Prompt versions are registered at startup via `register_all_prompts`.
- **Rate limiting**: SlowAPI middleware on the FastAPI agent app.
- **Health endpoints**: `/health` (liveness), `/health/detailed` (readiness — checks Qdrant, Neo4j, Redis).
- **RetrievalMetrics**: structured dict attached to `AgentState` after each agent run, logged at INFO level.
- **Structured logging**: JSON-file driver, 10MB max / 3 file rotation (docker-compose logging config).

---

## LLM Model Roles

| Model | Role | Config key |
|---|---|---|
| `qwen2.5:14b` | Primary: search answer, analyze text, verify | `OLLAMA_MODEL` |
| `qwen2.5:7b` | Draft: supervisor classification, query expansion, generate validation | `OLLAMA_GENERATE_DRAFT_MODEL` |
| `bge-m3:latest` | Embeddings (1024-dim, multilingual) | `OLLAMA_EMBEDDING_MODEL` |
| `mmarco-mMiniLMv2-L12-H384-v1` | Cross-encoder reranker (13 languages, CUDA) | `reranker_model` |

The draft model is used for any task requiring speed over reasoning depth: classification needs only 1-2 words output, query expansion needs ~150 tokens, generate validation needs ~2000 tokens but is not the critical reasoning step.
