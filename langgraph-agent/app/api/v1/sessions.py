"""Sessions API — list recent sessions and load message history from PostgreSQL."""
from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, HTTPException

from ...core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


async def _connect() -> asyncpg.Connection:
    s = get_settings()
    return await asyncpg.connect(dsn=s.postgres_dsn, timeout=s.postgres_timeout)


@router.get("/", summary="List recent sessions")
async def list_sessions(limit: int = 20) -> dict:
    """Return the most recently active sessions from PostgreSQL."""
    try:
        conn = await _connect()
        try:
            rows = await conn.fetch(
                """
                SELECT s.session_id, s.user_id, s.last_active,
                       COUNT(m.id) AS message_count,
                       (SELECT content FROM agent_messages
                        WHERE session_id = s.session_id AND role = 'user'
                        ORDER BY created_at DESC LIMIT 1) AS last_query
                FROM agent_sessions s
                LEFT JOIN agent_messages m ON m.session_id = s.session_id
                GROUP BY s.session_id, s.user_id, s.last_active
                ORDER BY s.last_active DESC
                LIMIT $1
                """,
                limit,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("list_sessions failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    sessions = [
        {
            "session_id": r["session_id"],
            "user_id": r["user_id"],
            "last_active": str(r["last_active"]),
            "message_count": r["message_count"],
            "last_query": (r["last_query"] or "")[:80],
        }
        for r in rows
    ]
    return {"total": len(sessions), "sessions": sessions}


@router.get("/{session_id}/messages", summary="Load message history for a session")
async def get_session_messages(session_id: str, limit: int = 100) -> dict:
    """Return all messages for a session from PostgreSQL."""
    if not session_id or len(session_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid session_id")
    try:
        conn = await _connect()
        try:
            rows = await conn.fetch(
                """
                SELECT role, content, created_at
                FROM agent_messages
                WHERE session_id = $1
                ORDER BY created_at ASC
                LIMIT $2
                """,
                session_id,
                limit,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error("get_session_messages failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    messages = [
        {"role": r["role"], "content": r["content"], "ts": r["created_at"].isoformat()}
        for r in rows
    ]
    return {"session_id": session_id, "total": len(messages), "messages": messages}
