"""LangGraph shared state definition."""
from __future__ import annotations

from typing import Annotated, Any
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class RetrievalMetrics(TypedDict, total=False):
    """Structured observability payload set by retrieval nodes."""
    node: str                  # which agent produced these metrics
    elapsed_s: float           # wall-clock seconds for the node
    merged_hits: int           # total chunks after vector+BM25 merge
    graph_hits: int            # chunks added from Neo4j
    best_rerank_score: float   # top cross-encoder sigmoid score
    is_exact_search: bool      # True when phrase-match query was used
    doc_content_len: int       # characters of document content verified
    risk_score: int            # verify_node compliance risk (0-10)


class AgentState(TypedDict):
    """Shared state passed between all nodes in the graph."""

    # ── Conversation ──────────────────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: str
    session_id: str
    user_id: str

    # ── Routing ───────────────────────────────────────────────────────
    # Primary intent: search | verify | generate | analyze
    intent: str
    # All detected intents — enables multi-intent execution.
    # e.g. ["search", "verify"] runs search first, then verify in sequence.
    intents: list[str]
    # Routing tier: "compound", "keyword", or "llm" — set by supervisor
    tier: str
    # document IDs the user mentioned / uploaded
    document_ids: list[str]

    # ── Retrieved context ─────────────────────────────────────────────
    vector_hits: list[dict[str, Any]]    # from Qdrant
    bm25_hits: list[dict[str, Any]]      # from BM25
    graph_hits: list[dict[str, Any]]     # from Neo4j
    reranked_docs: list[dict[str, Any]]  # after cross-encoder reranking

    # ── Agent outputs ─────────────────────────────────────────────────
    ingest_result: dict[str, Any]    # {doc_id, status, filename, page_count}
    search_result: str
    verify_result: dict[str, Any]    # {compliant, risk_score, issues, hints}
    generate_result: dict[str, Any]  # {text, export_path}
    analyze_result: dict[str, Any]   # {summary, qa_pairs, comparison}

    # ── Multi-intent accumulation ──────────────────────────────────────
    # Partial responses from already-completed intents in multi-intent mode
    combined_responses: list[str]

    # ── Final response ────────────────────────────────────────────────
    final_response: str
    citations: list[dict[str, Any]]
    export_path: str | None

    # ── Observability ─────────────────────────────────────────────────
    # Structured retrieval metrics for debugging and monitoring
    retrieval_metrics: RetrievalMetrics
