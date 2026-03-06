"""Tool: session memory — Redis (short-term) + PostgreSQL (long-term)."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import time
from typing import Any

from langchain_core.tools import tool

from ..core.config import get_settings

logger = logging.getLogger(__name__)


# Module-level thread pool — created once, reused for every pg_save_message_sync call.
# Avoids the overhead of creating/destroying a ThreadPoolExecutor per invocation.
_sync_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="pg-sync"
)


def _run_coro_sync(coro: Any, *, timeout: int = 10) -> Any:
    """Run a coroutine from synchronous code, regardless of whether an event loop is running.

    Strategy:
    - If no event loop is running → use asyncio.run() directly.
    - If an event loop IS running (e.g. inside FastAPI) → execute in a reusable
      thread via _sync_executor to avoid nesting (no per-call pool creation).
    """
    try:
        asyncio.get_running_loop()
        # Running inside an event loop — delegate to the module-level worker pool
        future = _sync_executor.submit(asyncio.run, coro)
        return future.result(timeout=timeout)
    except RuntimeError:
        # No running event loop — safe to call asyncio.run() directly
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Redis helpers (short-term)
# ---------------------------------------------------------------------------


def _redis_client():
    import redis

    s = get_settings()
    return redis.from_url(s.redis_url, decode_responses=True, socket_connect_timeout=5)


def _session_key(session_id: str) -> str:
    return f"session:{session_id}:messages"


@tool
def session_save(session_id: str, role: str, content: str) -> bool:
    """Append a message to the Redis session (short-term memory).

    Args:
        session_id: Unique session identifier.
        role: 'user' or 'assistant'.
        content: Message text.

    Returns:
        True on success.
    """
    s = get_settings()
    r = _redis_client()
    key = _session_key(session_id)
    r.rpush(key, json.dumps({"role": role, "content": content, "ts": time.time()}))
    r.expire(key, s.session_ttl_seconds)
    return True


@tool
def session_load(session_id: str, last_n: int = 20) -> str:
    """Load recent messages from Redis session.

    Args:
        session_id: Unique session identifier.
        last_n: Number of most recent messages to retrieve.

    Returns:
        JSON string of message list (each with role, content, ts).
        Empty string if no history.
    """
    r = _redis_client()
    key = _session_key(session_id)
    raw = r.lrange(key, -last_n, -1)
    if not raw:
        return ""
    messages = [json.loads(m) for m in raw]
    return json.dumps(messages)


@tool
def session_clear(session_id: str) -> bool:
    """Clear the Redis session.

    Args:
        session_id: Session to clear.

    Returns:
        True on success.
    """
    r = _redis_client()
    r.delete(_session_key(session_id))
    return True


# ---------------------------------------------------------------------------
# PostgreSQL connection pool (long-term memory)
# ---------------------------------------------------------------------------
# A single asyncpg Pool is created lazily on first use and reused for all
# subsequent calls.  This eliminates the TCP handshake + auth on every save.
# ---------------------------------------------------------------------------

_pg_pool: Any = None  # asyncpg.Pool instance, created lazily
_pg_pool_lock = asyncio.Lock() if False else None  # placeholder; real lock created per-loop


async def _get_pg_pool() -> Any:
    """Return (or create) the module-level asyncpg connection pool."""
    import asyncpg

    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool

    s = get_settings()
    # min_size=1 keeps at least one connection warm; max_size=5 caps memory
    _pg_pool = await asyncpg.create_pool(
        dsn=s.postgres_dsn,
        min_size=1,
        max_size=5,
        command_timeout=s.postgres_timeout,
    )
    logger.info("asyncpg pool created (min=1, max=5)")
    return _pg_pool


async def pg_save_message(session_id: str, user_id: str, role: str, content: str) -> None:
    """Persist a message to PostgreSQL using the connection pool."""
    pool = await _get_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agent_sessions (session_id, user_id)
            VALUES ($1, $2)
            ON CONFLICT (session_id) DO UPDATE SET last_active = NOW()
            """,
            session_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO agent_messages (session_id, user_id, role, content, created_at)
            VALUES ($1, $2, $3, $4, NOW())
            """,
            session_id,
            user_id,
            role,
            content,
        )


def pg_save_message_sync(session_id: str, user_id: str, role: str, content: str) -> None:
    """Synchronous wrapper around pg_save_message. Non-fatal on error."""
    try:
        _run_coro_sync(
            pg_save_message(session_id, user_id, role, content),
            timeout=5,
        )
    except Exception as exc:
        logger.warning("pg_save_message_sync failed (non-critical): %s", exc)


async def pg_ensure_schema() -> None:
    """Create tables if they don't exist."""
    pool = await _get_pg_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id   TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL DEFAULT 'anonymous',
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                last_active  TIMESTAMPTZ DEFAULT NOW(),
                prefs        JSONB DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id           BIGSERIAL PRIMARY KEY,
                session_id   TEXT NOT NULL REFERENCES agent_sessions(session_id),
                user_id      TEXT NOT NULL DEFAULT 'anonymous',
                role         TEXT NOT NULL,
                content      TEXT NOT NULL,
                context_docs JSONB DEFAULT '[]',
                created_at   TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON agent_messages (session_id, created_at DESC);
        """)


def pg_ensure_schema_sync() -> None:
    """Synchronous wrapper for pg_ensure_schema. Called once at app startup."""
    try:
        _run_coro_sync(pg_ensure_schema(), timeout=15)
        logger.info("PostgreSQL schema ensured")
    except Exception as exc:
        logger.warning("pg_ensure_schema_sync failed (non-critical): %s", exc)
