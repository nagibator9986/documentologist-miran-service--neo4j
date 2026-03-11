"""LangGraph StateGraph — wires all agents into the multi-agent workflow.

Multi-intent support
--------------------
The supervisor may detect two intents (e.g. ["search", "verify"]).
After the first agent completes its work, the `advance_intent` node shifts to
the next intent and the graph loops back to the appropriate agent.  When all
intents are processed the flow continues to `memory_save`.

Flow:
    START -> memory_load -> supervisor
          -> [conditional edge: state["intent"]] -> {search | verify | generate | analyze | ingest}
          -> [_route_after_agent] -> {advance_intent | memory_save}
          -> (advance_intent) -> [_route_next_agent] -> next_agent -> ...
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
from ..agents.supervisor import classify_intent
from ..agents.verify_agent import verify_node
from ..core.tracing import trace_node
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
    # Agent nodes are wrapped with trace_node so each execution becomes an
    # MLflow child span under the top-level autolog trace for graph.invoke().
    # Utility nodes (memory_load/save, advance_intent) are left unwrapped —
    # they contain no LLM calls and their I/O latency is measured by autolog.
    builder.add_node("memory_load",    memory_load_node)
    builder.add_node("supervisor",     trace_node(classify_intent))
    builder.add_node("ingest",         trace_node(ingest_node))
    builder.add_node("search",         trace_node(search_node))
    builder.add_node("verify",         trace_node(verify_node))
    builder.add_node("generate",       trace_node(generate_node))
    builder.add_node("analyze",        trace_node(analyze_node))
    builder.add_node("advance_intent", advance_intent_node)
    builder.add_node("memory_save",    memory_save_node)

    # ── Entry: memory load -> supervisor ──────────────────────────────────────
    builder.add_edge(START, "memory_load")
    builder.add_edge("memory_load", "supervisor")

    # ── Supervisor -> first agent (conditional) ────────────────────────────────
    # The graph reads state["intent"] set by classify_intent and routes to the
    # matching agent node.  Routing is the graph's responsibility, not an agent's.
    builder.add_conditional_edges(
        "supervisor",
        lambda state: state.get("intent", "search"),
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
