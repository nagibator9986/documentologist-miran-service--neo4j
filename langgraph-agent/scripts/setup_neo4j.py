"""
Neo4j Level 1+2 setup script.

Level 1: Create fulltext index on Section.text_preview
Level 2: Parse 'Статья N. Title' patterns from Section text → create Article nodes
         - Article gets stable article_id = "{doc_id}:art:{number}"
         - Links: Section -[HAS_ARTICLE]-> Article <-[HAS_ARTICLE]- Law

Usage (from langgraph-agent/ root):
    python scripts/setup_neo4j.py
or with custom connection:
    NEO4J_URI=bolt://host:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=secret python scripts/setup_neo4j.py
"""
from __future__ import annotations

import os
import re
import sys

from neo4j import GraphDatabase

# ── Connection settings (from env or defaults) ────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# Fulltext index name (must match _FULLTEXT_INDEX in neo4j_query.py)
INDEX_NAME = "sectionText"

# Regex to find article headers: "Статья N" or "Статья N. Title until newline"
_ARTICLE_RE = re.compile(
    r"Стать[яей]\s+(\d+)(?:[.\s]+([^\n]{0,120}))?",
    re.UNICODE,
)


def _parse_articles(text: str) -> list[dict]:
    """Extract article numbers and optional titles from section text."""
    results = []
    seen: set[int] = set()
    for m in _ARTICLE_RE.finditer(text):
        number = int(m.group(1))
        if number in seen:
            continue
        seen.add(number)
        raw_title = (m.group(2) or "").strip().rstrip(".,;:")
        title = re.split(r"[–—\-]\s", raw_title)[0].strip() if raw_title else ""
        results.append({"number": number, "title": title or None})
    return results


def create_fulltext_index(session) -> None:
    """Create fulltext index on Section.text_preview if it doesn't exist."""
    existing = {r["name"] for r in session.run("SHOW INDEXES YIELD name")}
    if INDEX_NAME in existing:
        print(f"[✓] Fulltext index '{INDEX_NAME}' already exists — skipping.")
        return

    session.run(
        f"CREATE FULLTEXT INDEX {INDEX_NAME} FOR (n:Section) ON EACH [n.text_preview]"
    )
    print(f"[✓] Created fulltext index '{INDEX_NAME}' on Section.text_preview")


def extract_and_link_articles(session) -> None:
    """Parse Article nodes from all Section.text_preview and create HAS_ARTICLE links.

    Article.article_id = "{doc_id}:art:{number}" — stable and unique per document.
    Each Article is also linked to its parent Law node: Law -[HAS_ARTICLE]-> Article.
    """
    # Get all sections with their parent doc_id (via CONTAINS relationship)
    sections = session.run("""
        MATCH (d:Document)-[:CONTAINS]->(s:Section)
        RETURN
            s.section_id AS sid,
            s.text_preview AS text,
            d.doc_id AS doc_id
    """).data()

    if not sections:
        print("[!] No Section nodes found — nothing to extract.")
        return

    total_articles = 0
    for row in sections:
        sid = row["sid"]
        text = row["text"] or ""
        doc_id = row["doc_id"] or "unknown"
        articles = _parse_articles(text)

        for art in articles:
            # Stable article_id scoped to the document
            article_id = f"{doc_id}:art:{art['number']}"

            # Merge Article with stable article_id, set number + title
            session.run(
                """
                MERGE (a:Article {article_id: $article_id})
                ON CREATE SET
                    a.number = $number,
                    a.title  = $title
                ON MATCH SET
                    a.number = $number,
                    a.title  = CASE
                        WHEN $title IS NOT NULL THEN $title
                        ELSE a.title
                    END
                WITH a
                MATCH (s:Section {section_id: $sid})
                MERGE (s)-[:HAS_ARTICLE]->(a)
                """,
                article_id=article_id,
                number=art["number"],
                title=art["title"],
                sid=sid,
            )

            # Also link Law -[HAS_ARTICLE]-> Article (via Document's BASED_ON)
            session.run(
                """
                MATCH (d:Document {doc_id: $doc_id})-[:BASED_ON]->(l:Law)
                MATCH (a:Article {article_id: $article_id})
                MERGE (l)-[:HAS_ARTICLE]->(a)
                """,
                doc_id=doc_id,
                article_id=article_id,
            )

            total_articles += 1

        if articles:
            nums = ", ".join(str(a["number"]) for a in articles)
            print(f"  {sid}: статьи [{nums}]")

    print(f"[✓] Created/merged {total_articles} Article nodes total.")


def cleanup_orphan_articles(session) -> None:
    """Remove old Article nodes that have no article_id (created by previous version)."""
    result = session.run(
        "MATCH (a:Article) WHERE a.article_id IS NULL "
        "DETACH DELETE a RETURN count(a) AS deleted"
    ).single()
    deleted = result["deleted"] if result else 0
    if deleted:
        print(f"[✓] Removed {deleted} orphan Article nodes (no article_id).")


def show_summary(session) -> None:
    """Print current Neo4j node and relationship counts."""
    counts = session.run("""
        MATCH (n)
        RETURN labels(n)[0] AS label, count(n) AS cnt
        ORDER BY cnt DESC
    """).data()
    print("\n── Nodes ───────────────────────────────────────")
    for row in counts:
        print(f"  {row['label']:<15} {row['cnt']}")

    rels = session.run("""
        MATCH ()-[r]->()
        RETURN type(r) AS rel, count(r) AS cnt
        ORDER BY cnt DESC
    """).data()
    print("── Relationships ───────────────────────────────")
    for row in rels:
        print(f"  {row['rel']:<20} {row['cnt']}")
    print("────────────────────────────────────────────────")

    # Verify law_lookup will work
    law_arts = session.run("""
        MATCH (l:Law)-[:HAS_ARTICLE]->(a:Article)
        RETURN l.title AS law, count(a) AS articles
    """).data()
    if law_arts:
        print("\n── Law → Article coverage ──────────────────────")
        for row in law_arts:
            print(f"  {row['law']}: {row['articles']} статей")
        print("────────────────────────────────────────────────")


def main() -> None:
    print(f"Connecting to Neo4j: {NEO4J_URI} as {NEO4J_USER}")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session() as session:
            print("\n── Cleanup: orphan Articles (no article_id) ────")
            cleanup_orphan_articles(session)

            print("\n── Level 1: Fulltext index ──────────────────────")
            create_fulltext_index(session)

            print("\n── Level 2: Article extraction + Law links ──────")
            extract_and_link_articles(session)

            show_summary(session)

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        driver.close()

    print("\n[✓] Setup complete.")


if __name__ == "__main__":
    main()
