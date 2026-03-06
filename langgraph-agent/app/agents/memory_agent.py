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

    Also finalises the multi-intent combined_responses: if prior agent responses
    were accumulated in combined_responses, they are merged into final_response
    so the REST endpoint always returns a single unified answer.
    """
    session_id = state.get("session_id", "default")
    user_id = state.get("user_id", "anonymous")
    query = state.get("user_query", "")

    # Finalise multi-intent: already done by each agent (they prepend combined_responses).
    # The final_response from the last agent is the complete merged response.
    response = state.get("final_response", "")

    # Save user message to Redis
    try:
        session_save.invoke({"session_id": session_id, "role": "user", "content": query})
    except Exception as exc:
        logger.warning("Redis save (user) failed (non-critical): %s", exc)

    # Save assistant response to Redis
    try:
        session_save.invoke({"session_id": session_id, "role": "assistant", "content": response})
        logger.info("Memory saved for session=%s", session_id)
    except Exception as exc:
        logger.warning("Redis save (assistant) failed (non-critical): %s", exc)

    # Persist to PostgreSQL using sync wrapper
    pg_save_message_sync(session_id, user_id, "user", query)
    pg_save_message_sync(session_id, user_id, "assistant", response)

    return state


def memory_load_node(state: AgentState) -> AgentState:
    """Load conversation history. Non-fatal if Redis unavailable."""
    session_id = state.get("session_id", "default")

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
