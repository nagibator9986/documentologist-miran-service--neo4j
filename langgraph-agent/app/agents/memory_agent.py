"""Memory Agent — saves and loads conversation history from Redis + PostgreSQL.

memory_save_node also finalises the multi-intent combined_responses:
if there are combined_responses from prior agents AND a final_response from the
last agent, they are merged into a single unified final_response.
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, HumanMessage

from ..graph.state import AgentState
from ..tools.session_memory import pg_save_message_sync, session_load, session_save

logger = logging.getLogger(__name__)


def memory_save_node(state: AgentState) -> AgentState:
    """Persist the latest turn to session memory. Non-fatal if Redis/PG unavailable.

    Tracks Redis and PG success independently. Logs ERROR when a backend
    fails so operators can distinguish ephemeral blips from outages:
      - WARNING: individual operation failed, retry may succeed.
      - ERROR: both user + assistant messages failed for this turn.
    """
    session_id = state.get("session_id", "default")
    user_id = state.get("user_id", "anonymous")
    query = state.get("user_query", "")

    # Finalise multi-intent: already done by each agent (they prepend combined_responses).
    # The final_response from the last agent is the complete merged response.
    response = state.get("final_response", "")

    # ── Redis (short-term) ─────────────────────────────────────────────────────
    redis_failures = 0
    for role, content in (("user", query), ("assistant", response)):
        try:
            session_save.invoke({"session_id": session_id, "role": role, "content": content})
        except Exception as exc:
            redis_failures += 1
            logger.warning("Redis save (%s) failed for session=%s: %s", role, session_id, exc)

    if redis_failures == 0:
        logger.info("Memory saved to Redis for session=%s", session_id)
    elif redis_failures == 2:
        logger.error(
            "Redis unavailable — both messages lost for session=%s. "
            "Conversation history will not persist across restarts.",
            session_id,
        )

    # ── PostgreSQL (long-term) ─────────────────────────────────────────────────
    pg_user_ok = pg_save_message_sync(session_id, user_id, "user", query)
    pg_asst_ok = pg_save_message_sync(session_id, user_id, "assistant", response)

    if not pg_user_ok or not pg_asst_ok:
        logger.error(
            "PostgreSQL save incomplete for session=%s user=%s "
            "(user_ok=%s, assistant_ok=%s) — long-term history may be incomplete.",
            session_id, user_id, pg_user_ok, pg_asst_ok,
        )

    return state


def memory_load_node(state: AgentState) -> AgentState:
    """Load conversation history and reset per-turn transient fields.

    Clears retrieval fields (vector_hits, bm25_hits, etc.) so that if
    LangGraph checkpointing is ever enabled, stale retrieval data from a
    prior turn will not bleed into the current one.

    Non-fatal if Redis unavailable.
    """
    session_id = state.get("session_id", "default")

    # Reset per-turn retrieval fields — ensures clean state on every invocation
    state = {
        **state,
        "vector_hits": [],
        "bm25_hits": [],
        "graph_hits": [],
        "reranked_docs": [],
        "combined_responses": [],
        "citations": [],
        "final_response": "",
        "query_expanded": "",
    }

    try:
        raw = session_load.invoke({"session_id": session_id})
    except Exception as exc:
        logger.warning("Redis load failed (non-critical): %s", exc)
        return state

    if not raw:
        return state

    try:
        history: list[dict] = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("memory_load_node: failed to parse session history JSON (%s) — starting fresh", exc)
        return state

    loaded_messages = []
    for turn in history:
        role = turn.get("role", "")
        content = turn.get("content", "")
        if not content:
            continue
        if role == "user":
            loaded_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            loaded_messages.append(AIMessage(content=content))

    current = state.get("messages", [])
    logger.info("Memory loaded for session=%s messages=%d", session_id, len(loaded_messages))
    return {**state, "messages": loaded_messages + current}
