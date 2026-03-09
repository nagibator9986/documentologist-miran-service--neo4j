"""LangGraph StateGraph — wires all agents into the multi-agent workflow.

Multi-intent support
--------------------
The supervisor may detect two intents (e.g. ["search", "verify"]).
After the first agent completes its work, the `advance_intent` node shifts to
the next intent and the graph loops back to the appropriate agent.  When all
intents are processed the flow continues to `memory_save`.

Flow:
    START -> memory_load -> supervisor
          -> route_intent -> {search | verify | generate | analyze}
          -> route_after_agent -> {advance_intent | memory_save}
          -> (advance_intent) -> route_next_agent -> next_agent -> ...
          -> memory_save -> END
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from ..agents.analyze_agent import analyze_node
from ..agents.generate_agent import generate_node
from ..agents.ingest_agent import ingest_node
from ..agents.memory_agent import memory_load_node, memory_save_node
from ..agents.search_agent import search_node
from ..agents.supervisor import classify_intent, route_intent
from ..agents.verify_agent import verify_node
from .state import AgentState

logger = logging.getLogger(__name__)

_AGENT_NODES = ("ingest", "search", "verify", "generate", "analyze")


# ──────────────────────────────────────────────────────────────────────────────
# advance_intent node
# ──────────────────────────────────────────────────────────────────────────────

def advance_intent_node(state: AgentState) -> AgentState:
    """Shift to the next intent in the intents list.

    Saves the current agent's response into `combined_responses` so that
    all partial answers are preserved. The `intent` field is updated to
    the next item in `intents`.
    """
    intents: list[str] = state.get("intents") or []
    current: str = state.get("intent", "")
    combined: list[str] = list(state.get("combined_responses") or [])

    # Archive the current agent's response before moving on
    partial = state.get("final_response", "")
    if partial:
        combined.append(partial)

    try:
        idx = intents.index(current)
        if idx + 1 < len(intents):
            next_intent = intents[idx + 1]
            logger.info("advance_intent: %s -> %s", current, next_intent)
            return {**state, "intent": next_intent, "combined_responses": combined}
    except ValueError:
        pass

    logger.warning(
        "advance_intent: could not find next intent after %r in %s", current, intents
    )
    return {**state, "combined_responses": combined}


# ──────────────────────────────────────────────────────────────────────────────
# Routing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _route_after_agent(state: AgentState) -> str:
    """After any agent: check whether there is a next intent to process."""
    intents: list[str] = state.get("intents") or []
    current: str = state.get("intent", "")
    try:
        idx = intents.index(current)
        if idx + 1 < len(intents):
            return "advance_intent"
    except ValueError:
        pass
    return "memory_save"


def _route_next_agent(state: AgentState) -> str:
    """After advance_intent: route to the (now current) agent node."""
    intent = state.get("intent", "search")
    if intent in _AGENT_NODES:
        return intent
    logger.warning("_route_next_agent: unknown intent %r, defaulting to search", intent)
    return "search"


# ──────────────────────────────────────────────────────────────────────────────
# Graph definition
# ──────────────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Construct and compile the LangGraph multi-agent StateGraph."""
    builder = StateGraph(AgentState)

    # ── Nodes ─────────────────────────────────────────────────────────────────
    builder.add_node("memory_load",    memory_load_node)
    builder.add_node("supervisor",     classify_intent)
    builder.add_node("ingest",         ingest_node)
    builder.add_node("search",         search_node)
    builder.add_node("verify",         verify_node)
    builder.add_node("generate",       generate_node)
    builder.add_node("analyze",        analyze_node)
    builder.add_node("advance_intent", advance_intent_node)
    builder.add_node("memory_save",    memory_save_node)

    # ── Entry: memory load -> supervisor ──────────────────────────────────────
    builder.add_edge(START, "memory_load")
    builder.add_edge("memory_load", "supervisor")

    # ── Supervisor -> first agent (conditional) ────────────────────────────────
    builder.add_conditional_edges(
        "supervisor",
        route_intent,
        {k: k for k in _AGENT_NODES},
    )

    # ── After each agent: either advance to next intent or finish ─────────────
    _after_map = {"advance_intent": "advance_intent", "memory_save": "memory_save"}
    for agent_node in _AGENT_NODES:
        builder.add_conditional_edges(agent_node, _route_after_agent, _after_map)

    # ── advance_intent -> next agent ──────────────────────────────────────────
    builder.add_conditional_edges(
        "advance_intent",
        _route_next_agent,
        {k: k for k in _AGENT_NODES},
    )

    # ── memory_save -> END ────────────────────────────────────────────────────
    builder.add_edge("memory_save", END)

    return builder.compile()


# Singleton compiled graph — import this in the API layer
graph = build_graph()
