"""
Centralized configuration loaded from environment variables.
"""

import json
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Database ────────────────────────────
    database_url: str = "postgresql+asyncpg://docolog:docolog_secret@localhost:5433/documentolog"
    database_url_sync: str = "postgresql://docolog:docolog_secret@localhost:5433/documentolog"

    # ── MinIO ───────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin123"
    minio_secure: bool = False

    # ── Buckets ─────────────────────────────
    bucket_source: str = "source-files"
    bucket_results: str = "analysis-results"

    # ── Prefect ─────────────────────────────
    prefect_api_url: str = "http://localhost:4200/api"

    # ── OCR ──────────────────────────────────
    ocr_concurrency_limit: int = 1
    ocr_concurrency_slot: str = "ocr-gpu-slots"
    ocr_timeout_seconds: int = 300
    ocr_retry_delay_seconds: int = 60
    max_upload_size_mb: int = 50

    # ── Pipeline task timeouts ────────────────
    ocr_init_timeout_seconds: int = 30
    ocr_structure_timeout_seconds: int = 30
    ocr_save_timeout_seconds: int = 60

    # ── App ──────────────────────────────────
    log_level: str = "INFO"
    app_title: str = "Документолог: Core"
    app_version: str = "0.1.0"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    cors_allow_credentials: bool = True

    # ── Integration: bank_knowledge indexer ──────────────────────────────────
    # URL банковского индексатора. После завершения OCR worker делает POST сюда,
    # чтобы запустить цепочку: chunk → embed → Qdrant + Neo4j.
    # Пример: http://bank-knowledge:8002/api/v1/index
    # Оставьте пустым чтобы отключить автоиндексацию.
    indexer_webhook_url: str = ""
    # Base URL for indexing status checks. When configured, the status endpoint
    # will be called as: {indexer_status_url.rstrip('/')}/{doc_id}/status
    # Example (matching the webhook service above):
    #   INDEXER_STATUS_URL=http://bank-knowledge:8002/api/v1/index
    indexer_status_url: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
