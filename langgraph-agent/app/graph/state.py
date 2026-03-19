"""LangGraph shared state definition."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


# ── Retrieval scope literals ─────────────────────────────────────────────────
# point   — search across the entire collection (current default behaviour)
# scoped  — search within a single document identified by doc_id
# document — process the entire document via Map-Reduce
Scope = Literal["point", "scoped", "document"]


# ── Unified retrieval result ─────────────────────────────────────────────────

@dataclass
class RetrievalResult:
    """Return type for the unified retrieval module.

    Every retrieval strategy (point / scoped / document) returns this,
    so consuming agents don't care *how* chunks were fetched.
    """

    chunks: list[dict[str, Any]] = field(default_factory=list)
    best_score: float = 0.0
    has_context: bool = False
    scope: Scope = "point"
    total_doc_chunks: int = 0  # how many chunks the document has (scoped/document)
    metrics: dict[str, Any] = field(default_factory=dict)


class RetrievalMetrics(TypedDict, total=False):
    """Structured observability payload set by retrieval nodes."""
    node: str                  # which agent produced these metrics
    elapsed_s: float           # wall-clock seconds for the node
    scope: str                 # point | scoped | document
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
    # Retrieval scope determined by supervisor: point | scoped | document
    scope: Scope

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
