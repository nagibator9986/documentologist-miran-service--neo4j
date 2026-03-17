# Directory Structure — Miran Service (Neo4j Edition)

## Root Layout

```
documentologist-miran-service--neo4j/
├── docker-compose.yml          # Unified compose for the full stack (GPU Edition)
├── .gitmodules                 # Git submodule config (bank_knowledge is a submodule)
├── .github/workflows/
│   └── python-ci.yml           # CI pipeline
├── scripts/
│   └── init-db.sql             # PostgreSQL multi-database init script
│
├── ocr-service/                # Step 1: Document ingestion + OCR
├── bank_knowledge/             # Step 2: Chunking + embedding + indexing (submodule)
├── bank_knowledge_temp/        # Temporary local copy of bank_knowledge (pre-submodule)
└── langgraph-agent/            # Step 3: Multi-agent RAG query service
```

---

## `ocr-service/`

FastAPI service that accepts document uploads and runs Surya OCR via Prefect.

```
ocr-service/
├── Dockerfile.api              # API container (CPU only)
├── Dockerfile.frontend         # Streamlit UI container
├── Dockerfile.worker           # OCR worker container (GPU-accelerated)
├── alembic.ini                 # Alembic migration config
├── .env.example
│
├── app/
│   ├── main.py                 # FastAPI app factory and startup
│   ├── api/
│   │   └── routes.py           # All API route definitions
│   ├── core/
│   │   ├── config.py           # Pydantic Settings (env-based config)
│   │   └── database.py         # SQLAlchemy async + sync session factories
│   ├── models/
│   │   └── document.py         # Document ORM model + DocumentStatus enum
│   ├── schemas/
│   │   └── document.py         # Pydantic request/response schemas
│   └── services/
│       ├── ocr.py              # Surya OCR wrapper
│       ├── storage.py          # MinIO client (upload/download)
│       ├── hasher.py           # File hash computation (dedup guard)
│       ├── indexer_webhook.py  # HTTP POST to bank-knowledge indexer
│       ├── documents.py        # Document CRUD service
│       └── metrics.py          # Prometheus-style metrics helpers
│
├── workers/
│   ├── __init__.py
│   ├── pipeline.py             # Prefect 3.0 flow: 5-task OCR pipeline
│   └── main.py                 # Worker entry point (starts Prefect worker)
│
├── frontend/
│   └── app.py                  # Streamlit upload/browse UI (frontend container)
├── frontend_app.py             # Alternate Streamlit entry point
├── backend/
│   └── main.py                 # Standalone backend variant
│
├── migrations/
│   ├── env.py                  # Alembic env config
│   └── versions/
│       ├── 001_initial.py
│       ├── 002_file_hash_unique.py
│       ├── 003_add_is_latest_index.py
│       └── 004_status_index.py
│
├── tests/
│   ├── test_api_upload.py
│   └── test_core.py
│
└── sctructure.py               # (typo in filename) structure exploration script
```

### Key OCR files

- `ocr-service/workers/pipeline.py` — the Prefect flow that defines all 5 processing tasks and the main `document_processing_pipeline` flow
- `ocr-service/app/services/indexer_webhook.py` — sends completion notification to `bank-knowledge:8002/api/v1/index`
- `ocr-service/app/models/document.py` — `DocumentStatus` enum: `pending | processing | completed | failed`

---

## `bank_knowledge/`

Git submodule. The indexer service: receives webhook events, chunks OCR output, embeds with bge-m3, upserts to Qdrant. Also exposed as a standalone CLI and Prefect flow library.

```
bank_knowledge/
├── Dockerfile.indexer          # Webhook server container (GPU)
├── Dockerfile                  # Streamlit UI container
├── indexer_api.py              # FastAPI webhook server (POST /api/v1/index)
│
└── bank_knowledge/             # Python package (same name as parent dir)
    ├── __init__.py
    ├── cli.py                  # Click CLI for manual ingestion runs
    ├── common.py               # Shared utilities (build_point_id, etc.)
    ├── constants.py            # BASE_DIR, DEFAULT_FILE paths
    ├── label_studio.py         # Label Studio export → ingest CSV transform
    ├── models.py               # Pydantic config models: IngestConfig, OcrTransformConfig, LabelStudioTransformConfig
    ├── ocr_surya.py            # Surya OCR JSON → chunked ingest CSV transform
    ├── pipeline_service.py     # PipelineService + PipelineTasks dataclasses (pure, testable)
    ├── prefect_flows.py        # All Prefect @flow definitions (8 flows)
    ├── qdrant_ops.py           # Qdrant collection management + embedding point builders
    ├── records.py              # CSV/JSON record loader
    ├── streamlit_app.py        # Streamlit collection browser UI
    └── upload_detection.py     # MinIO polling for new uploads (batch ingestion)
```

### Key indexer files

- `bank_knowledge/indexer_api.py` — the FastAPI app that is the `bank-knowledge` container's entry point. In-memory job registry with 24h TTL.
- `bank_knowledge/bank_knowledge/prefect_flows.py` — defines `ocr_surya_to_qdrant_flow` (the primary webhook-triggered flow) plus 7 other Prefect flows for different ingestion scenarios
- `bank_knowledge/bank_knowledge/qdrant_ops.py` — `OllamaPointBuilder` and `HuggingFacePointBuilder` for dual-vector embedding; `ensure_collection` creates the named-vector Qdrant collection

---

## `langgraph-agent/`

The main RAG query service. FastAPI + LangGraph StateGraph with 7 agents.

```
langgraph-agent/
├── Dockerfile
├── pyproject.toml
├── requirements.txt
├── Makefile
├── .env.example
├── chainlit_app.py             # Chainlit chat UI entry point
├── streamlit_app.py            # Streamlit chat UI (alternative)
│
├── .chainlit/
│   ├── config.toml             # Chainlit UI configuration
│   └── translations/           # 20+ locale JSON files
│
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app factory, lifespan, routers, health endpoints
│   │
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── state.py            # AgentState TypedDict + RetrievalMetrics TypedDict
│   │   └── workflow.py         # StateGraph definition + build_graph() + singleton graph
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── supervisor.py       # Intent classification (3-tier: compound / keyword / LLM)
│   │   ├── search_agent.py     # 13-stage hybrid RAG pipeline
│   │   ├── analyze_agent.py    # QA / compare / extract / summary
│   │   ├── verify_agent.py     # Compliance check + risk scoring
│   │   ├── generate_agent.py   # Legal document generation + DOCX/PDF export
│   │   ├── ingest_agent.py     # Upload status tracking + user guidance
│   │   └── memory_agent.py     # Redis session load/save + PostgreSQL audit log
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── qdrant_search.py    # qdrant_search, qdrant_text_search, qdrant_scroll_by_doc_ids
│   │   ├── bm25_search.py      # In-process BM25 over Qdrant hits
│   │   ├── neo4j_query.py      # Cypher tools: sections, articles, obligations, entities, laws
│   │   ├── reranker.py         # Cross-encoder reranker (mmarco, CUDA)
│   │   ├── doc_generate.py     # Document template expansion + DOCX/PDF export
│   │   ├── ocr_client.py       # HTTP client for ocr-api (status check, list documents)
│   │   └── session_memory.py   # Redis session tools + asyncpg PostgreSQL memory tools
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── chat.py         # POST /api/v1/chat, GET /api/v1/chat/stream (SSE)
│   │       ├── completions.py  # OpenAI-compatible completions endpoint
│   │       ├── documents.py    # GET /api/v1/documents (list + download)
│   │       ├── ingest.py       # POST /api/v1/ingest (file upload proxy to OCR)
│   │       └── sessions.py     # GET/DELETE /api/v1/sessions/{session_id}
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py           # Settings (pydantic-settings, env-based, lru_cache singleton)
│   │   ├── llm.py              # get_llm(), get_draft_llm(), get_json_llm(), invoke_with_retry()
│   │   ├── utils.py            # Shared helpers: strip_prefix, build_history, extract_filename, safe_parse_json
│   │   ├── rate_limit.py       # SlowAPI limiter configuration
│   │   └── tracing.py          # MLflow setup, trace_node wrapper, prompt registration
│   │
│   └── prompts/
│       └── __init__.py         # All system prompt strings as module-level constants
│
├── scripts/
│   └── setup_neo4j.py          # One-time Neo4j schema setup script
│
└── tests/
    └── test_analyze_classify.py
```

---

## Naming Conventions

### Python modules

- Agent nodes are named `*_node` (e.g., `search_node`, `verify_node`) — the LangGraph node entry point function
- Private pipeline stages within agents are `_stage_name` (underscore prefix)
- Tool functions exposed as LangChain `@tool` or `StructuredTool` objects named as nouns: `qdrant_search`, `bm25_search`, `reranker`, `neo4j_query`
- Config settings use `snake_case`; Docker env vars use `UPPER_SNAKE_CASE`
- Pydantic config models: `IngestConfig`, `OcrTransformConfig`, `LabelStudioTransformConfig`

### Docker services and containers

- Container name pattern: `miran-{service}` (e.g., `miran-ocr-api`, `miran-neo4j`)
- Service name pattern: `{service}` with hyphens (e.g., `ocr-api`, `bank-knowledge`)
- Build contexts match directory names: `./ocr-service`, `./bank_knowledge`, `./langgraph-agent`

### File patterns

- `Dockerfile.{variant}` — service-specific Dockerfiles within a subdirectory (`Dockerfile.api`, `Dockerfile.worker`, `Dockerfile.frontend`)
- `*.example` — template files that must be copied to the real name (`.env.example` → `.env`)
- `migrations/versions/NNN_description.py` — Alembic migration files with numeric prefix

### API versioning

All agent API routes are prefixed `/api/v1/`. The OCR service also uses `/api/v1/`. The bank-knowledge indexer webhook is `/api/v1/index`.

---

## Configuration Entry Points

| Service | Config file | Key env vars |
|---|---|---|
| langgraph-agent | `langgraph-agent/app/core/config.py` (pydantic-settings) | `OLLAMA_MODEL`, `NEO4J_*`, `QDRANT_*`, `REDIS_URL`, `POSTGRES_DSN`, `MINIO_*` |
| ocr-service | `ocr-service/app/core/config.py` | `DATABASE_URL`, `MINIO_*`, `PREFECT_API_URL`, `INDEXER_WEBHOOK_URL` |
| bank-knowledge | Environment only (no config.py, read directly in `indexer_api.py`) | `QDRANT_URL`, `OLLAMA_URL`, `MINIO_*`, `NEO4J_*`, `CHUNK_*` |
| All services | `docker-compose.yml` `.env` section | `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` |

---

## Port Map

| Port (host) | Service | Container port |
|---|---|---|
| 8000 | ocr-api | 8000 |
| 8001 | langgraph-agent | 8001 |
| 8002 | bank-knowledge | 8002 |
| 8501 | chainlit-ui | 8501 |
| 8504 | ocr-frontend | 8501 |
| 8505 | bank-knowledge-ui | 8080 |
| 4201 | prefect-server | 4200 |
| 5434 | postgres | 5432 |
| 6335 | qdrant (HTTP) | 6333 |
| 6336 | qdrant (gRPC) | 6334 |
| 7475 | neo4j (HTTP) | 7474 |
| 7688 | neo4j (Bolt) | 7687 |
| 9002 | minio (API) | 9000 |
| 9003 | minio (Console) | 9001 |
| 6380 | redis | 6379 |

All host bindings are `127.0.0.1` (localhost only), except `langgraph-agent` which binds `0.0.0.0:8001`.

---

## Notable File Locations

| Purpose | Path |
|---|---|
| Full service topology | `docker-compose.yml` |
| LangGraph state definition | `langgraph-agent/app/graph/state.py` |
| Graph wiring + routing | `langgraph-agent/app/graph/workflow.py` |
| Supervisor (intent classification) | `langgraph-agent/app/agents/supervisor.py` |
| Search agent (hybrid RAG) | `langgraph-agent/app/agents/search_agent.py` |
| All system prompts | `langgraph-agent/app/prompts/__init__.py` |
| LLM client factory | `langgraph-agent/app/core/llm.py` |
| All agent settings | `langgraph-agent/app/core/config.py` |
| OCR Prefect pipeline | `ocr-service/workers/pipeline.py` |
| Indexer webhook server | `bank_knowledge/indexer_api.py` |
| Prefect flow definitions | `bank_knowledge/bank_knowledge/prefect_flows.py` |
| Embedding + Qdrant ops | `bank_knowledge/bank_knowledge/qdrant_ops.py` |
| Surya OCR transform | `bank_knowledge/bank_knowledge/ocr_surya.py` |
| Neo4j setup script | `langgraph-agent/scripts/setup_neo4j.py` |
| PostgreSQL DB init | `scripts/init-db.sql` |
