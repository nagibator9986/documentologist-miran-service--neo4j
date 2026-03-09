"""FastAPI application entry point for LangGraph Multi-Agent Service."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .api.v1.chat import router as chat_router
from .api.v1.documents import router as documents_router
from .api.v1.ingest import router as ingest_router
from .api.v1.sessions import router as sessions_router
from .core.config import get_settings
from .core.rate_limit import limiter
from .core.utils import close_all_clients, ensure_neo4j_fulltext_index
from .tools.session_memory import pg_ensure_schema_sync, pg_shutdown

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Lifespan: startup + graceful shutdown
# ──────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ── Startup ───────────────────────────────────────────────────────
    s = get_settings()
    logger.info("LangGraph Agent starting — model=%s env=%s", s.ollama_model, s.app_env)

    # Ensure export directory exists
    os.makedirs(s.export_dir, exist_ok=True)

    # Ensure PostgreSQL schema exists (non-fatal: agent still works without PG)
    pg_ensure_schema_sync()

    # Ensure Neo4j fulltext index exists (non-fatal: graph search falls back to CONTAINS)
    ensure_neo4j_fulltext_index()

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    logger.info("LangGraph Agent shutting down — closing DB connections…")
    close_all_clients()
    await pg_shutdown()
    logger.info("Shutdown complete.")


# ──────────────────────────────────────────────────────────────────────────────
# App factory
# ──────────────────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    s = get_settings()

    app = FastAPI(
        title="Miran LangGraph Agent",
        description="Multi-agent RAG system for Kazakhstani banking & legal documents",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── Rate limiting ─────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # ── CORS ─────────────────────────────────────────────────────────
    # Configured via CORS_ORIGINS env var. Never use ["*"] in production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # ── Routers ──────────────────────────────────────────────────────
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(ingest_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")

    # ── System endpoints ─────────────────────────────────────────────
    @app.get("/health", tags=["system"], summary="Basic liveness probe")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "langgraph-agent"})

    @app.get("/health/detailed", tags=["system"], summary="Readiness probe — checks all dependencies")
    async def health_detailed() -> JSONResponse:
        from .core.utils import get_neo4j_driver, get_qdrant_client

        checks: dict[str, str] = {}

        # Qdrant
        try:
            get_qdrant_client().get_collections()
            checks["qdrant"] = "ok"
        except Exception as exc:
            checks["qdrant"] = f"error: {exc}"

        # Neo4j
        try:
            drv = get_neo4j_driver()
            drv.verify_connectivity()
            checks["neo4j"] = "ok"
        except Exception as exc:
            checks["neo4j"] = f"error: {exc}"

        # Redis
        try:
            import redis as redis_lib
            r = redis_lib.from_url(s.redis_url, socket_connect_timeout=2)
            r.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"

        all_ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if all_ok else "degraded", **checks},
            status_code=200,
        )

    @app.get("/", tags=["system"])
    async def root() -> JSONResponse:
        return JSONResponse({
            "service": "Miran LangGraph Agent",
            "version": "1.0.0",
            "docs": "/docs",
        })

    return app


app = create_app()

# ──────────────────────────────────────────────────────────────────────────────
# Dev entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8001")),
        reload=os.getenv("APP_ENV", "prod") == "dev",
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
