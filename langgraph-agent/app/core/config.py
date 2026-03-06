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
    ollama_model: str = "qwen2.5:7b"
    ollama_embedding_model: str = "bge-m3:latest"
    # Seconds before an Ollama request times out
    ollama_timeout: int = 120
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

    # ── Document export ───────────────────────────────────────────────
    # Directory where DOCX/PDF exports are written. Must be writable.
    export_dir: str = "/tmp/agent_exports"
    # Directory where bank_knowledge uploads (Surya JSON) are stored.
    # Used by documents.py /download endpoint — override in .env if needed.
    bank_knowledge_upload_dir: str = ""

    # ── Agent behaviour ───────────────────────────────────────────────
    rerank_top_k: int = 5
    bm25_top_k: int = 40
    max_tokens_response: int = 2048
    temperature: float = 0.1
    # Minimum cosine similarity score to consider a hit relevant (0–1).
    # Hits below this threshold are discarded before LLM generation.
    # Lowered to 0.35 for better recall; cross-encoder reranker handles precision
    min_relevance_score: float = 0.35
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
    content_snippet_max_len: int = 800
    # Max characters of content shown in citation preview
    citation_preview_max_len: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
