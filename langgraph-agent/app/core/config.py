"""Application settings — Ollama-first configuration."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # .env may have vars for other services (docker-compose etc.)
    )

    # ── App ───────────────────────────────────────────────────────────
    app_name: str = "langgraph-agent"
    app_env: str = "dev"
    log_level: str = "INFO"
    port: int = 8001

    # Allowed CORS origins. In .env: CORS_ORIGINS=["http://localhost:3000"]
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
    )

    # ── Ollama ────────────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"
    # qwen2.5:7b handles Russian + structured JSON far better than mistral:latest.
    # Alternatives: llama3.1:8b, mistral-nemo (prod API), mistral:latest (weakest).
    ollama_model: str = "qwen2.5:14b"
    # Faster model for bulk text generation (e.g. document draft expansion).
    # When empty, falls back to ollama_model.
    ollama_generate_draft_model: str = ""
    ollama_embedding_model: str = "bge-m3:latest"
    # Seconds before an Ollama request times out
    ollama_timeout: int = 300
    # Max retries for Ollama LLM calls (tenacity, exponential backoff)
    ollama_max_retries: int = 3

    # ── Qdrant ────────────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "bank_knowledge"
    # Increased to 20: gives BM25 more candidates to rerank lexically
    # 40 candidates gives BM25 more material to work with (real hybrid effect)
    qdrant_top_k: int = 40
    # Seconds before a Qdrant request times out
    qdrant_timeout: int = 30

    # ── Neo4j ─────────────────────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    # Maximum number of connections in the Neo4j connection pool
    neo4j_max_connection_pool_size: int = 50

    # ── PostgreSQL (long-term memory) ─────────────────────────────────
    postgres_dsn: str = "postgresql://postgres:postgres@localhost:5432/agent_memory"
    # Seconds for asyncpg connection/command timeout
    postgres_timeout: int = 10

    # ── Redis (short-term memory) ─────────────────────────────────────
    redis_url: str = "redis://localhost:6379"
    session_ttl_seconds: int = 3600

    # ── MinIO ─────────────────────────────────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "agent-exports"
    minio_secure: bool = False
    # Public-facing MinIO address for presigned URLs.
    # Inside Docker the internal endpoint is "minio:9000" (not reachable by browsers).
    # Set MINIO_PUBLIC_ENDPOINT=localhost:9000 (or your public domain) in .env
    # so generated presigned URLs are accessible from outside the Docker network.
    # Falls back to minio_endpoint if not set.
    minio_public_endpoint: str = ""

    # ── Document export ───────────────────────────────────────────────
    # Directory where DOCX/PDF exports are written. Must be writable.
    export_dir: str = "/tmp/agent_exports"
    # Directory where bank_knowledge uploads (Surya JSON) are stored.
    # Used by documents.py /download endpoint — override in .env if needed.
    bank_knowledge_upload_dir: str = ""

    # ── Agent behaviour ───────────────────────────────────────────────
    rerank_top_k: int = 7
    bm25_top_k: int = 40
    max_tokens_response: int = 2048
    temperature: float = 0.1
    # Minimum cosine similarity score to consider a hit relevant (0-1).
    # Lowered from 0.35: gives cross-encoder more candidates in the normal path
    # (not just the fallback path) for borderline-relevant chunks (cosine ~0.28-0.34).
    # Calibrated 2026-03-17.
    min_relevance_score: float = 0.25
    # Seconds before the entire LangGraph pipeline times out.
    # Prevents hanging requests when Ollama is unavailable.
    # Set to 240 to give generate agent (3 LLM passes) enough time.
    graph_timeout_seconds: int = 240
    # How many prior conversation turns to pass to each agent LLM call.
    history_turns: int = 3

    # ── Text truncation constants ─────────────────────────────────────
    # Max significant words extracted from long queries for Neo4j graph search
    graph_kw_max_words: int = 10
    # Max characters of content included in context window per chunk
    content_snippet_max_len: int = 1200
    # Max characters of content shown in citation preview
    citation_preview_max_len: int = 200

    # ── Reranker ──────────────────────────────────────────────────────
    # Primary multilingual cross-encoder (mMARCO, 13 languages incl. Russian)
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    # Fallback cross-encoder if primary fails to load
    reranker_fallback_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
    # Max characters of content fed to the cross-encoder per chunk
    # 1024 chars ≈ 200-250 tokens; legal paragraphs often need full context
    reranker_max_content: int = 1024

    # ── Search pipeline tuning ────────────────────────────────────────
    # Minimum sigmoid-normalised rerank score to consider context reliable.
    # Lowered from 0.25: cross-encoder sigmoid scores on Russian legal text often
    # land 0.15-0.30 for genuinely relevant docs; original threshold was cutting
    # too many borderline-relevant results. Calibrated 2026-03-17.
    search_min_confidence: float = 0.15
    # Seconds before parallel graph-enrichment step times out
    graph_enrichment_timeout: float = 8.0
    # num_predict for supervisor intent LLM call (only need 1-2 words out)
    supervisor_num_predict: int = 32
    # Char length above which the user query is treated as pasted document text
    verify_pasted_doc_threshold: int = 300

    # ── Qdrant vector schema ──────────────────────────────────────────
    # Named vector used in dual-vector Qdrant collections (set by the indexer)
    qdrant_named_vector: str = "q_vec"

    # ── Neo4j index names ─────────────────────────────────────────────
    # Fulltext index on Section.text_preview (created at startup)
    neo4j_fulltext_index: str = "sectionText"

    # ── PostgreSQL connection pool ────────────────────────────────────
    pg_pool_min_size: int = 1
    pg_pool_max_size: int = 5
    # Thread pool workers for running async PG calls from sync context
    pg_thread_workers: int = 2

    # ── Integration: OCR Service ──────────────────────────────────────
    # Base URL of the Surya OCR service (first step in the pipeline).
    # Example: http://ocr-api:8000
    ocr_service_url: str = "http://localhost:8000"

    # ── File upload limits ────────────────────────────────────────────
    # Maximum file size accepted by POST /api/v1/ingest (in megabytes)
    max_upload_size_mb: int = 50

    # ── Qdrant scan limits ────────────────────────────────────────────
    # Maximum number of 200-record scroll batches for full-collection scan
    # (used by qdrant_text_search fallback and list_documents endpoint)
    qdrant_max_scan_batches: int = 50

    # ── SSE streaming ─────────────────────────────────────────────────
    # Number of words per SSE token event when streaming the final response
    sse_word_chunk_size: int = 6

    # ── Reranking ─────────────────────────────────────────────────────
    # Hard candidate pool fed to the cross-encoder before top_k selection.
    # Replaces the old rerank_top_k * 2 magic: 30 covers all merged hits
    # (vector 40 + BM25 40 + graph ~10, deduplicated to ~45) without waste.
    rerank_candidate_pool: int = 30
    # Kept for any external callers that still reference this setting.
    rerank_candidate_multiplier: int = 2

    # ── Analyze agent ─────────────────────────────────────────────────
    # Number of Qdrant hits for summary tasks (needs broad coverage).
    analyze_summary_limit: int = 15
    # Number of Qdrant hits per document side in compare tasks.
    analyze_compare_limit: int = 8

    # ── Memory agent ───────────────────────────────────────────────────
    # How many messages to load from Redis into state["messages"] at the
    # start of each request.  Intentionally larger than history_turns*2:
    # the full loaded set is also scanned by extract_recent_filename()
    # (needs to find filenames mentioned N turns ago) and other referential
    # resolution logic.  build_history_messages() still caps what reaches
    # the LLM at history_turns turns.
    memory_max_history: int = 20

    # ── Graph query thread pool ───────────────────────────────────────
    # Worker threads for parallel Neo4j queries inside _retrieve_graph.
    # Shared pool — created once, reused across all requests.
    graph_pool_workers: int = 5

    # ── MLflow Observability ──────────────────────────────────────────
    # MLflow tracing is enabled by default.
    # Set MLFLOW_ENABLED=false in .env to disable (zero overhead when off).
    mlflow_enabled: bool = True
    # MLflow tracking server URL (docker-compose: http://mlflow:5000)
    mlflow_tracking_uri: str = "http://localhost:5000"
    # Experiment groups all runs for this service together in the UI
    mlflow_experiment_name: str = "miran-agent"

    # Log format: "text" for human-readable, "json" for structured production logs
    log_format: str = "text"


@lru_cache
def get_settings() -> Settings:
    return Settings()
