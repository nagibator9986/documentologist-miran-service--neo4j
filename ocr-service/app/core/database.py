"""
Async and sync SQLAlchemy engines and session factories.

- Async engine  → FastAPI route handlers
- Sync engine   → Prefect worker tasks + FastAPI background tasks
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# ── Async (FastAPI) ───────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,    # validate connections on checkout (detects stale TCP)
    pool_recycle=3600,     # recycle connections after 1 hour (avoids idle timeout drops)
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async session (commit is explicit in handlers)."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── Sync (Prefect workers + background tasks) ─────────────────────
sync_engine = create_engine(
    settings.database_url_sync,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


@contextmanager
def get_sync_db() -> Generator[Session, None, None]:
    """
    Context manager for synchronous DB sessions.
    Used by Prefect worker tasks and FastAPI background tasks.
    Always closes the session and rolls back on unhandled exceptions.
    """
    session = SyncSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
