"""
Application settings via pydantic-settings.
All values can be overridden through environment variables or .env file.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── App ───────────────────────────────────────────────
    app_name: str = "rag-engine"
    app_env: str = "dev"
    log_level: str = "INFO"
    port: int = 8001

    # ── Mistral ───────────────────────────────────────────
    mistral_api_key: str = ""
    embedding_model: str = "mistral-embed"
    # mistral-embed produces 1024-dim vectors
    vector_size: int = 1024

    # ── Qdrant ────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    default_collection: str = "rag_documents"

    # ── Neo4j ─────────────────────────────────────────────
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    # ── Redis ─────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"
    redis_stream_key: str = "rag:doc_ready"
    redis_consumer_group: str = "rag-engine"
    redis_consumer_name: str = "rag-engine-1"

    # ── Chunking ──────────────────────────────────────────
    chunk_max_tokens: int = 512
    chunk_overlap_ratio: float = 0.20
    chunk_min_tokens: int = 50
    chunk_encoding: str = "cl100k_base"

    # ── Pipeline ──────────────────────────────────────────
    embed_batch_size: int = 32

    # ── Prefect ───────────────────────────────────────────
    prefect_api_url: str = "http://localhost:4200/api"


@lru_cache
def get_settings() -> Settings:
    return Settings()
