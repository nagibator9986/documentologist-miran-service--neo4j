"""Shared utilities: safe JSON parsing, singleton DB clients, conversation helpers."""
from __future__ import annotations

import logging
import math
import re
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared regex patterns (used across multiple agents)
# ---------------------------------------------------------------------------

# Detects referential phrases like "этот документ", "в нём", "данный файл"
DOC_REF_RE = re.compile(
    r"\b(этот\s+документ|этого\s+документа|в\s+нём|в\s+нем|в\s+ней|об\s+этом|данный\s+документ|"
    r"этот\s+файл|в\s+этом\s+документе|из\s+этого\s+документа|этот\s+текст)\b",
    re.IGNORECASE | re.UNICODE,
)

# Matches common document filenames (pdf, docx, doc, txt, json)
FILENAME_RE = re.compile(r"[\w\-]+\.(?:pdf|docx|doc|txt|json)\b", re.IGNORECASE)

# Common conversational prefixes to strip before classification / retrieval.
_CONVERSATIONAL_PREFIXES_RE = re.compile(
    r"^(?:(?:скажите?\s+)?пожалуйста\s*,?\s*"
    r"|(?:привет|здравствуйте?|добрый\s+(?:день|вечер|утро))\s*[!,.]?\s*"
    r"|подскажи(?:те)?\s*,?\s*"
    r"|не\s+мог(?:ли|бы)\s+(?:бы\s+)?(?:вы\s+)?(?:мне\s+)?"
    r"|будьте?\s+добры?\s*,?\s*"
    r"|можно\s+(?:ли\s+)?(?:узнать|спросить)\s*,?\s*"
    r")+",
    re.IGNORECASE | re.UNICODE,
)


def strip_conversational_prefix(query: str) -> str:
    """Remove polite/greeting prefixes so downstream classifiers see only the core query."""
    stripped = _CONVERSATIONAL_PREFIXES_RE.sub("", query).strip()
    return stripped if stripped else query


# ---------------------------------------------------------------------------
# Hit metadata helpers
# ---------------------------------------------------------------------------


def extract_hit_filename(hit: dict[str, Any]) -> str | None:
    """Extract the source filename from a retrieval hit dict.

    Looks in several locations: top-level ``filename``, ``section`` field,
    and nested ``metadata.source`` / ``metadata.filename``.
    """
    # Direct field
    fn = hit.get("filename")
    if fn:
        return fn
    # metadata.source or metadata.filename
    meta = hit.get("metadata") or {}
    fn = meta.get("source") or meta.get("filename")
    if fn:
        return fn
    # Fallback: extract from section string like "стр. 5 — doc.pdf"
    section = hit.get("section", "")
    if section:
        m = FILENAME_RE.search(section)
        if m:
            return m.group(0)
    return None


def extract_hit_page(hit: dict[str, Any]) -> int | str | None:
    """Extract the page number from a retrieval hit dict."""
    meta = hit.get("metadata") or {}
    page = meta.get("page_number") or meta.get("page")
    if page is not None:
        return page
    # Try parsing from section string "стр. 12"
    section = hit.get("section", "")
    m = re.search(r"стр\.?\s*(\d+)", section)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Conversation history helper
# ---------------------------------------------------------------------------


def extract_recent_filename(state: Any) -> str | None:
    """Return the most recently mentioned filename from conversation history.

    Scans messages in reverse (newest first), skipping the current user message.
    Used by search and analyze agents to enrich referential queries like
    "в этом документе" → "document.pdf в этом документе".
    """
    messages: list = state.get("messages", [])
    for msg in reversed(messages[:-1]):
        content = getattr(msg, "content", "") or ""
        matches = FILENAME_RE.findall(content)
        if matches:
            return matches[0]
    return None


def build_final_response(answer: str, combined_responses: list[str]) -> str:
    """Merge multi-intent responses into a single final_response string.

    When the supervisor detects multiple intents (e.g. search + verify), each
    agent calls this to prepend the previous agent's output before its own
    answer.  The separator line makes the boundary between results visible.

    Args:
        answer: The current agent's answer.
        combined_responses: Answers accumulated from prior agents in this turn.

    Returns:
        Combined string, or just `answer` if there are no prior responses.
    """
    if combined_responses:
        return "\n\n---\n\n".join(combined_responses) + "\n\n---\n\n" + answer
    return answer


def build_history_messages(state: Any, max_turns: int = 3) -> list["BaseMessage"]:
    """Return the last `max_turns` conversation turns from state["messages"].

    Excludes the current (last) HumanMessage which is already handled by the
    calling agent.  Use this to give agents multi-turn awareness.

    Args:
        state: AgentState dict that contains a "messages" list.
        max_turns: Number of prior user+assistant pairs to include (default 3).

    Returns:
        Slice of BaseMessage list, oldest first, safe to prepend to the LLM call.
    """
    messages: list = state.get("messages", [])
    # The last element is the current HumanMessage — skip it
    history = messages[:-1] if len(messages) > 1 else []
    # Keep only the last max_turns * 2 messages (user + assistant per turn)
    return history[-(max_turns * 2):]


def sigmoid_score(raw: float) -> float:
    """Map an unbounded cross-encoder logit score to [0, 1] via sigmoid.

    Cross-encoder/ms-marco models return raw logits that can be negative.
    This is used to normalise scores for UI progress bars.
    """
    return 1.0 / (1.0 + math.exp(-raw))


# ---------------------------------------------------------------------------
# Safe JSON parser
# ---------------------------------------------------------------------------


def safe_parse_json(raw: str | None, fallback: dict[str, Any]) -> dict[str, Any]:
    """Extract the first valid JSON object from LLM output.

    Uses brace-counting (not rfind) so nested objects are handled correctly.
    Falls back to `fallback` on any parse error or if `raw` is None / non-string.
    """
    import json

    if not isinstance(raw, str) or not raw:
        logger.warning(
            "safe_parse_json: received %s instead of str — using fallback",
            type(raw).__name__,
        )
        return dict(fallback)

    text = raw.strip()

    # 1. Direct parse (clean output)
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 2. Brace-counting: find the FIRST complete JSON object
    start = text.find("{")
    if start == -1:
        logger.warning("safe_parse_json: no '{' in LLM output — using fallback")
        return dict(fallback)

    depth = 0
    end = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        logger.warning("safe_parse_json: unmatched braces in LLM output — using fallback")
        return dict(fallback)

    try:
        result = json.loads(text[start:end])
        if isinstance(result, dict):
            return result
        logger.warning("safe_parse_json: extracted JSON is not a dict — using fallback")
        return dict(fallback)
    except json.JSONDecodeError as exc:
        logger.warning("safe_parse_json: extracted substring is invalid JSON (%s) — using fallback", exc)
        return dict(fallback)


# ---------------------------------------------------------------------------
# Singleton Qdrant client
# ---------------------------------------------------------------------------

_qdrant_lock = threading.Lock()
_qdrant_client = None


def get_qdrant_client():
    """Return a module-level singleton QdrantClient (thread-safe, with timeout)."""
    global _qdrant_client
    if _qdrant_client is None:
        with _qdrant_lock:
            if _qdrant_client is None:
                from qdrant_client import QdrantClient

                from .config import get_settings

                s = get_settings()
                _qdrant_client = QdrantClient(
                    url=s.qdrant_url,
                    api_key=s.qdrant_api_key,
                    timeout=s.qdrant_timeout,
                )
                logger.info("QdrantClient singleton created: %s (timeout=%ds)", s.qdrant_url, s.qdrant_timeout)
    return _qdrant_client


# ---------------------------------------------------------------------------
# Singleton Neo4j driver
# ---------------------------------------------------------------------------

_neo4j_lock = threading.Lock()
_neo4j_driver = None


def get_neo4j_driver():
    """Return a module-level singleton Neo4j driver (thread-safe, with connection pool)."""
    global _neo4j_driver
    if _neo4j_driver is None:
        with _neo4j_lock:
            if _neo4j_driver is None:
                from neo4j import GraphDatabase

                from .config import get_settings

                s = get_settings()
                _neo4j_driver = GraphDatabase.driver(
                    s.neo4j_uri,
                    auth=(s.neo4j_user, s.neo4j_password),
                    max_connection_pool_size=s.neo4j_max_connection_pool_size,
                )
                logger.info(
                    "Neo4j driver singleton created: %s (pool_size=%d)",
                    s.neo4j_uri,
                    s.neo4j_max_connection_pool_size,
                )
    return _neo4j_driver


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


def close_all_clients() -> None:
    """Close all singleton DB clients and thread pools. Called on application shutdown."""
    global _qdrant_client, _neo4j_driver

    with _qdrant_lock:
        if _qdrant_client is not None:
            try:
                _qdrant_client.close()
                logger.info("QdrantClient closed.")
            except Exception as exc:
                logger.warning("Error closing QdrantClient: %s", exc)
            finally:
                _qdrant_client = None

    with _neo4j_lock:
        if _neo4j_driver is not None:
            try:
                _neo4j_driver.close()
                logger.info("Neo4j driver closed.")
            except Exception as exc:
                logger.warning("Error closing Neo4j driver: %s", exc)
            finally:
                _neo4j_driver = None

    # Shutdown the graph query thread pool from retrieval module
    try:
        from ..tools.retrieval import _graph_pool, _graph_pool_lock
        with _graph_pool_lock:
            if _graph_pool is not None:
                _graph_pool.shutdown(wait=False)
                logger.info("Graph query thread pool shut down.")
    except Exception as exc:
        logger.warning("Error shutting down graph pool: %s", exc)


# ---------------------------------------------------------------------------
# Neo4j index bootstrap
# ---------------------------------------------------------------------------

def ensure_neo4j_fulltext_index() -> None:
    """Create Neo4j fulltext index + B-tree property indexes on startup.

    Indexes created (all IF NOT EXISTS — safe to call repeatedly):
    - Fulltext: Section.text_preview  (for graph_section_search)
    - B-tree:   Document(doc_id), Section(section_id) — primary keys for MERGE
    - B-tree:   Entity(text), Entity(label)            — for graph_entity_lookup
    - B-tree:   Obligation(subject)                    — for graph_obligation_search
    - B-tree:   Law(law_id), Law(title)                — for law_lookup / law queries

    Non-fatal: if Neo4j is unreachable at startup the agent still works;
    CONTAINS fallback is used for fulltext, B-tree lookups degrade to full scans.
    """
    from .config import get_settings
    fulltext_index = get_settings().neo4j_fulltext_index

    _BTREE_INDEXES = [
        ("doc_doc_id",        "FOR (d:Document)   ON (d.doc_id)"),
        ("sec_section_id",    "FOR (s:Section)    ON (s.section_id)"),
        ("ent_text",          "FOR (e:Entity)     ON (e.text)"),
        ("ent_label",         "FOR (e:Entity)     ON (e.label)"),
        ("obl_subject",       "FOR (o:Obligation) ON (o.subject)"),
        ("law_law_id",        "FOR (l:Law)        ON (l.law_id)"),
        ("law_title",         "FOR (l:Law)        ON (l.title)"),
    ]
    try:
        driver = get_neo4j_driver()
        with driver.session() as session:
            # ── Fulltext index ────────────────────────────────────────────────
            result = session.run(
                "SHOW INDEXES WHERE name = $name",
                name=fulltext_index,
            )
            if result.single():
                logger.debug("Neo4j fulltext index '%s' already exists.", fulltext_index)
            else:
                session.run(
                    f"CREATE FULLTEXT INDEX {fulltext_index} IF NOT EXISTS "
                    f"FOR (s:Section) ON EACH [s.text_preview]"
                )
                logger.info("Neo4j fulltext index '%s' created.", fulltext_index)

            # ── B-tree property indexes ───────────────────────────────────────
            existing_idx = {r["name"] for r in session.run("SHOW INDEXES YIELD name")}
            for idx_name, idx_clause in _BTREE_INDEXES:
                if idx_name in existing_idx:
                    logger.debug("Neo4j index '%s' already exists.", idx_name)
                    continue
                session.run(f"CREATE INDEX {idx_name} IF NOT EXISTS {idx_clause}")
                logger.info("Neo4j index '%s' created.", idx_name)

    except Exception as exc:
        logger.warning(
            "ensure_neo4j_fulltext_index failed (non-critical — CONTAINS fallback active): %s", exc
        )
