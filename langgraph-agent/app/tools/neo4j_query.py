"""Tool: query Neo4j knowledge graph."""
from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.tools import tool

from ..core.utils import get_neo4j_driver

logger = logging.getLogger(__name__)

# Lucene fulltext index name (created by scripts/setup_neo4j.py)
_FULLTEXT_INDEX = "sectionText"

# Traversal depth limits to prevent runaway queries
_MIN_DEPTH = 1
_MAX_DEPTH = 5

# Allowed characters in relationship type names (UPPER_SNAKE_CASE)
_ALLOWED_REL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ_")

# Lucene special characters that break fulltext queries when unescaped
_LUCENE_SPECIAL_RE = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


def _escape_lucene(text: str) -> str:
    """Escape Lucene special characters in fulltext search keywords.

    Prevents query parse errors when user input contains characters like
    +, -, !, (, ), {, }, [, ], ^, ", ~, *, ?, :, \\, /, &&, ||
    """
    return _LUCENE_SPECIAL_RE.sub(r"\\\1", text)


@tool
def neo4j_query(cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Execute a Cypher query on the Neo4j knowledge graph.

    Args:
        cypher: Cypher query string.
        params: Optional query parameters.

    Returns:
        List of result records as dicts.
    """
    try:
        driver = get_neo4j_driver()
        with driver.session() as session:
            result = session.run(cypher, parameters=params or {})
            records = [dict(r) for r in result]
        logger.debug("neo4j_query → %d records", len(records))
        return records
    except Exception as exc:
        logger.error("neo4j_query failed: %s", exc)
        return []


@tool
def law_lookup(law_id: str) -> dict[str, Any]:
    """Fetch a Law node and its related Articles from Neo4j.

    Args:
        law_id: Unique identifier of the law.

    Returns:
        Dict with law properties and list of article summaries.
    """
    cypher = """
        MATCH (l:Law {law_id: $law_id})
        OPTIONAL MATCH (l)-[:HAS_ARTICLE]->(a:Article)
        RETURN l, collect(a) AS articles
    """
    driver = get_neo4j_driver()
    with driver.session() as session:
        result = session.run(cypher, law_id=law_id)
        record = result.single()
        if not record:
            return {"error": f"Law {law_id!r} not found"}
        law_node = dict(record["l"])
        articles = [dict(a) for a in record["articles"] if a]
        return {"law": law_node, "articles": articles}


@tool
def graph_section_search(keywords: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search Section nodes in Neo4j by keyword using fulltext or CONTAINS fallback.

    Args:
        keywords: Keywords to search for (Russian words work fine).
        limit: Max number of sections to return.

    Returns:
        List of matching sections with source filename and linked Article numbers.
    """
    driver = get_neo4j_driver()
    with driver.session() as session:
        # Try fulltext index first (created by setup_neo4j.py)
        try:
            # Escape Lucene special chars to prevent query parse errors
            safe_kw = _escape_lucene(keywords)
            cypher = f"""
                CALL db.index.fulltext.queryNodes('{_FULLTEXT_INDEX}', $kw)
                YIELD node AS s, score
                OPTIONAL MATCH (d:Document)-[:CONTAINS]->(s)
                OPTIONAL MATCH (s)-[:HAS_ARTICLE]->(a:Article)
                RETURN
                    s.section_id AS section_id,
                    s.page_number AS page,
                    s.text_preview AS text,
                    d.filename AS source,
                    collect(DISTINCT {{number: a.number, title: a.title}}) AS articles,
                    score
                ORDER BY score DESC
                LIMIT $limit
            """
            result = session.run(cypher, kw=safe_kw, limit=limit)
            records = [dict(r) for r in result]
            if records:
                logger.debug("graph_section_search (fulltext) → %d records", len(records))
                return records
        except Exception as exc:
            logger.debug("Fulltext index not available (%s), falling back to CONTAINS", exc)

        # Fallback: CONTAINS on text_preview — truncate keyword for safety
        kw_trunc = keywords[:60]
        cypher = """
            MATCH (s:Section)
            WHERE toLower(s.text_preview) CONTAINS toLower($kw)
            OPTIONAL MATCH (d:Document)-[:CONTAINS]->(s)
            OPTIONAL MATCH (s)-[:HAS_ARTICLE]->(a:Article)
            RETURN
                s.section_id AS section_id,
                s.page_number AS page,
                s.text_preview AS text,
                d.filename AS source,
                collect(DISTINCT {number: a.number, title: a.title}) AS articles
            LIMIT $limit
        """
        result = session.run(cypher, kw=kw_trunc, limit=limit)
        records = [dict(r) for r in result]
        logger.debug("graph_section_search (CONTAINS) → %d records", len(records))
        return records


@tool
def graph_entity_lookup(text: str, label: str = "", limit: int = 5) -> list[dict[str, Any]]:
    """Find Entity nodes whose text contains the given string.

    Args:
        text: Partial or full entity text to match (case-insensitive).
        label: Optional entity type filter — ORG, PERSON, PARTY, LAW, ARTICLE, etc.
        limit: Max number of results.

    Returns:
        List of matching entities with label, role, confidence, evidence and
        the filenames of documents that mention them.
    """
    driver = get_neo4j_driver()
    with driver.session() as session:
        if label:
            cypher = """
                MATCH (e:Entity)
                WHERE toLower(e.text) CONTAINS toLower($text) AND e.label = $label
                OPTIONAL MATCH (d:Document)-[:HAS_ENTITY]->(e)
                RETURN
                    e.text AS text, e.label AS label, e.role AS role,
                    e.confidence AS confidence, e.evidence AS evidence,
                    collect(DISTINCT d.filename) AS documents
                ORDER BY e.confidence DESC
                LIMIT $limit
            """
            result = session.run(cypher, text=text, label=label.upper(), limit=limit)
        else:
            cypher = """
                MATCH (e:Entity)
                WHERE toLower(e.text) CONTAINS toLower($text)
                OPTIONAL MATCH (d:Document)-[:HAS_ENTITY]->(e)
                RETURN
                    e.text AS text, e.label AS label, e.role AS role,
                    e.confidence AS confidence, e.evidence AS evidence,
                    collect(DISTINCT d.filename) AS documents
                ORDER BY e.confidence DESC
                LIMIT $limit
            """
            result = session.run(cypher, text=text, limit=limit)
        records = [dict(r) for r in result]
    logger.debug("graph_entity_lookup(%r, label=%r) → %d records", text, label, len(records))
    return records


@tool
def graph_obligation_search(keywords: str, limit: int = 5) -> list[dict[str, Any]]:
    """Find Obligation nodes whose subject/action/object/evidence contain the keywords.

    Args:
        keywords: Words to match (case-insensitive, partial).
        limit: Max number of results.

    Returns:
        List of obligations with subject, action, object, deadline, confidence,
        evidence and the source document filename/doc_id.
    """
    driver = get_neo4j_driver()
    with driver.session() as session:
        cypher = """
            MATCH (d:Document)-[:HAS_OBLIGATION]->(o:Obligation)
            WHERE toLower(
                coalesce(o.subject, '') + ' ' + coalesce(o.action, '') + ' ' +
                coalesce(o.object, '') + ' ' + coalesce(o.evidence, '')
            ) CONTAINS toLower($kw)
            RETURN
                o.subject AS subject, o.action AS action, o.object AS object,
                o.deadline AS deadline, o.confidence AS confidence,
                o.evidence AS evidence,
                d.filename AS document, d.doc_id AS doc_id
            ORDER BY o.confidence DESC
            LIMIT $limit
        """
        records = [dict(r) for r in session.run(cypher, kw=keywords[:100], limit=limit)]
    logger.debug("graph_obligation_search(%r) → %d records", keywords[:40], len(records))
    return records


@tool
def graph_traverse(start_node_id: str, rel_types: list[str] | None = None, depth: int = 2) -> list[dict[str, Any]]:
    """Traverse the knowledge graph from a starting node.

    Args:
        start_node_id: doc_id or law_id of the starting node.
        rel_types: Relationship types to follow (e.g. ['REFERENCES', 'BASED_ON']).
                   Only UPPER_SNAKE_CASE names are accepted — others are silently dropped.
        depth: Maximum traversal depth (clamped to 1–5 for safety).

    Returns:
        List of connected nodes with relationship info.
    """
    # Validate rel_types to prevent Cypher injection (allow only UPPER_SNAKE_CASE)
    if rel_types:
        rel_types = [r for r in rel_types if r and all(c in _ALLOWED_REL_CHARS for c in r)]

    rel_filter = ""
    if rel_types:
        rel_filter = ":" + "|".join(rel_types)

    # Clamp depth to prevent runaway traversals
    safe_depth = max(_MIN_DEPTH, min(_MAX_DEPTH, int(depth)))
    if safe_depth != depth:
        logger.warning("graph_traverse: depth=%r clamped to %d", depth, safe_depth)

    cypher = f"""
        MATCH path = (n {{node_id: $nid}})-[r{rel_filter}*1..{safe_depth}]-(m)
        RETURN
            labels(m) AS node_labels,
            properties(m) AS node_props,
            [rel IN relationships(path) | type(rel)] AS rel_chain
        LIMIT 50
    """
    driver = get_neo4j_driver()
    with driver.session() as session:
        result = session.run(cypher, nid=start_node_id)
        return [dict(r) for r in result]
