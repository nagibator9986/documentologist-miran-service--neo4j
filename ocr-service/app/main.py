"""
Документолог: Core — FastAPI Application.
"""

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import limiter, router as doc_router
from app.core.config import get_settings
from app.core.database import engine
from app.schemas.document import HealthResponse
from app.services.metrics import http_request_duration_seconds, http_requests_total
from app.services.storage import get_minio_service


settings = get_settings()

# ── Prometheus middleware ─────────────────────────────────────────────────

_SKIP_METRICS_PATHS = {"/metrics", "/health"}


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Track request count and latency for all API routes."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in _SKIP_METRICS_PATHS:
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        http_requests_total.labels(
            method=request.method,
            path=path,
            status_code=str(response.status_code),
        ).inc()
        http_request_duration_seconds.labels(
            method=request.method,
            path=path,
        ).observe(duration)

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("🚀 Starting Документолог API …")
    logger.info("📦 Schema management: Alembic migrations are required before startup.")

    yield

    logger.info("👋 Shutting down …")
    await engine.dispose()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    description="Система автоматизированной обработки документов",
    lifespan=lifespan,
)

# Rate limiting — attach limiter state and register 429 handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS (loosen in dev, tighten in prod)
cors_allow_credentials = settings.cors_allow_credentials
if "*" in settings.cors_origins and cors_allow_credentials:
    logger.warning("CORS misconfiguration detected: disabling credentials for wildcard origins.")
    cors_allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(PrometheusMiddleware)

# ── Routes ───────────────────────────────────────────────────

app.include_router(doc_router, prefix="/api/v1")


# ── Health check timeout (seconds) ────────────────────────────────────────
_HEALTH_TIMEOUT = 5.0


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    minio_ok = "unknown"
    db_ok = "unknown"

    try:
        async with asyncio.timeout(_HEALTH_TIMEOUT):
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(None, get_minio_service().health_check)
            minio_ok = "ok" if ok else "error"
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("Проверка MinIO превысила тайм-аут (%ss)", _HEALTH_TIMEOUT)
        minio_ok = "timeout"
    except Exception:
        minio_ok = "error"

    try:
        from sqlalchemy import text
        from app.core.database import async_session_factory

        async with asyncio.timeout(_HEALTH_TIMEOUT):
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
                db_ok = "ok"
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("Проверка БД превысила тайм-аут (%ss)", _HEALTH_TIMEOUT)
        db_ok = "timeout"
    except Exception:
        db_ok = "error"

    return HealthResponse(
        status="ok" if db_ok == "ok" and minio_ok == "ok" else "degraded",
        version=settings.app_version,
        database=db_ok,
        minio=minio_ok,
    )


# ── Metrics gauge TTL cache ────────────────────────────────────────────────
# Refresh the documents_by_status gauge at most once every 30 seconds.
# Prevents a DB query on every Prometheus scrape (which can be <10s intervals).
_METRICS_CACHE_TTL = 30.0
_metrics_last_refresh: float = 0.0


@app.get("/metrics", include_in_schema=False, tags=["System"])
async def prometheus_metrics():
    """Expose Prometheus metrics for scraping.

    The `documentolog_documents_by_status` gauge is refreshed at most once
    every 30 seconds via a TTL cache — avoids a DB query on every scrape.
    """
    global _metrics_last_refresh

    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.document import Document
    from app.services.metrics import documents_by_status
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    now = time.monotonic()
    if now - _metrics_last_refresh >= _METRICS_CACHE_TTL:
        try:
            async with async_session_factory() as session:
                rows = await session.execute(
                    select(Document.status, func.count()).group_by(Document.status)
                )
                for status_val, count in rows:
                    documents_by_status.labels(status=status_val.value).set(count)
            _metrics_last_refresh = now
        except Exception as exc:
            logger.warning("Не удалось обновить метрики по статусам: %s", exc)

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
