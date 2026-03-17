# Technology Stack

## Runtime & Language

- **Python 3.11** — all services (slim Docker images: `python:3.11-slim`)
- **Docker / Docker Compose** — full containerized deployment with GPU support (NVIDIA Container Toolkit, CUDA 12.1)

---

## Services Overview

The system is a four-stage document intelligence pipeline:

| Service | Container | Port |
|---|---|---|
| OCR API | `miran-ocr-api` | 8000 |
| OCR Worker | `miran-ocr-worker` | — |
| OCR Frontend (Streamlit) | `miran-ocr-frontend` | 8504 |
| bank-knowledge Indexer | `miran-bank-knowledge` | 8002 |
| bank-knowledge UI (Streamlit) | `miran-bank-knowledge-ui` | 8505 |
| LangGraph Agent API | `miran-langgraph-agent` | 8001 |
| Chainlit UI | `miran-chainlit-ui` | 8501 |

---

## Web Frameworks

- **FastAPI `>=0.110, <1`** — REST API for `ocr-service` (API) and `bank-knowledge` (indexer)
- **FastAPI `>=0.110.0`** — REST + SSE streaming for `langgraph-agent`
- **Uvicorn `[standard] >=0.27, <1`** — ASGI server for all FastAPI services
- **Chainlit `>=1.1.0`** — LLM-native chat UI with streaming and file upload (served from `langgraph-agent` image on port 8501)
- **Streamlit `>=1.32.0`** — browser UI for OCR file upload/browse and knowledge-base exploration

---

## AI / ML Frameworks

### LangGraph / LangChain
- **`langgraph>=0.1.0`** — multi-agent state machine orchestration
- **`langchain-core>=0.2.0`** — core primitives
- **`langchain-ollama>=0.2.0`** — Ollama LLM + embeddings integration (used in `langgraph-agent` and `bank-knowledge`)
- **`langchain-huggingface`** — HuggingFace model integration (used in `bank-knowledge`)

### Local LLM / Embeddings (via Ollama)
- **Ollama** — inference server running on the host (accessed via `http://host.docker.internal:11434` from containers)
- **Default chat model:** `qwen2.5:14b` (langgraph-agent), `qwen2.5:7b` (bank-knowledge)
- **Draft/fast model:** `qwen2.5:7b`
- **Embedding model:** `bge-m3:latest` (1024-dimensional vectors)

### OCR
- **`surya-ocr==0.16.0`** — primary OCR engine (GPU-accelerated via CUDA 12.1)
- **`pdf2image`** — PDF page rendering (requires `poppler-utils` system package)
- **`Pillow`** — image processing
- **`pymupdf`** — PDF parsing

### Retrieval & Reranking
- **`sentence-transformers>=2.7.0`** — cross-encoder reranking
  - Primary model: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual, 13 languages incl. Russian)
  - Fallback model: `cross-encoder/ms-marco-MiniLM-L6-v2`
  - Pre-downloaded into the Docker image at build time
- **`rank-bm25>=0.2.2`** — BM25 lexical search (hybrid retrieval alongside vector search)
- **`qdrant-client>=1.9.0`** — Qdrant vector database client

### Deep Learning Runtime
- **`torch>=2.3.0`** — PyTorch (CUDA 12.1 wheels via `https://download.pytorch.org/whl/cu121`)
- **`torchvision>=0.18.0`** — torchvision (OCR worker)
- GPU is auto-detected at runtime; CPU fallback is transparent

---

## Data & Storage Libraries

- **`sqlalchemy[asyncio]>=2.0, <3`** — async ORM (OCR service)
- **`asyncpg>=0.29, <1`** — async PostgreSQL driver
- **`psycopg2-binary>=2.9, <3`** — sync PostgreSQL driver
- **`alembic>=1.13, <2`** — database migrations (OCR service)
- **`minio>=7.2, <8`** — MinIO / S3-compatible object storage client
- **`redis>=5.0.0`** — Redis client for session memory and rate limiting
- **`neo4j>=5.0.0`** — Neo4j graph database driver (Bolt protocol)

---

## Workflow Orchestration

- **`prefect>=3.0, <4`** — pipeline orchestration for OCR tasks (`prefect>=2.14` in legacy `bank_knowledge` UI)
- Prefect server uses PostgreSQL (`prefect` database) as its backing store

---

## Configuration & Utilities

- **`pydantic>=2.5, <3`** — data validation
- **`pydantic-settings>=2.2.0`** — settings from env vars / `.env` files
- **`python-dotenv>=1.0, <2`** — `.env` file loading
- **`loguru>=0.7, <1`** — structured logging
- **`tenacity>=8.2.0`** — retry logic with exponential backoff (Ollama calls)
- **`python-multipart>=0.0.9`** — file upload parsing

---

## API / HTTP

- **`httpx>=0.27`** — async HTTP client (inter-service calls, webhook delivery)
- **`requests>=2.31.0`** — sync HTTP client

---

## Rate Limiting & Observability

- **`slowapi>=0.1.9`** — FastAPI rate limiting
- **`limits[redis]>=3.6, <4`** — Redis backend for distributed rate limiting
- **`prometheus-client>=0.20, <1`** — Prometheus metrics endpoint (OCR API)
- **`mlflow>=2.14.0`** — ML experiment tracking, prompt registry, tracing (optional; disabled by default via `MLFLOW_ENABLED=false`)
- **`boto3>=1.34.0`** — AWS SDK (used by MLflow for MinIO/S3 artifact storage)

---

## Document Export

- **`python-docx>=1.1.0`** — DOCX generation
- **`fpdf2>=2.7.9`** — PDF generation

---

## Development & Testing

- **`pytest>=8.0, <9`** — test runner
- **`httpx>=0.27`** — async test client (pytest)

---

## Infrastructure Images (Docker Compose)

| Component | Image | Version |
|---|---|---|
| PostgreSQL | `postgres` | `16-alpine` |
| Redis | `redis` | `7-alpine` |
| MinIO | `minio/minio` | `latest` |
| MinIO CLI | `minio/mc` | `latest` |
| Qdrant | `qdrant/qdrant` | `latest` |
| Neo4j | `neo4j` | `5.20` with APOC plugin |
| Prefect Server | `prefecthq/prefect` | `3-latest` |

---

## GPU Requirements

- **NVIDIA driver `>=525`** on the host
- **NVIDIA Container Toolkit** for GPU passthrough into containers
- CUDA 12.1 runtime is bundled inside PyTorch wheels — no host CUDA installation required
- Services using GPU: `ocr-worker`, `bank-knowledge`, `langgraph-agent`
