"""Search Agent — hybrid RAG pipeline orchestrator.

Pipeline stages (each is a focused private function):
  1. _prepare_query       — referential enrichment + LLM query expansion
  2. _detect_exact_search — quoted / prefix-based exact match detection
  3. _retrieve_exact      — Qdrant MatchText search for literal strings
  4. _retrieve_vector_bm25 — vector search + BM25 lexical reranking + merge
  5. _retrieve_graph      — parallel Neo4j enrichment (sections, articles,
                             obligations, entities, laws)
  6. _normalize_graph_hits — convert raw graph records to unified hit dicts
  7. _merge_all_hits      — combine all hit sources, deduplicating by ID
  8. _filter_by_relevance — drop low-cosine hits, fall back to all if empty
  9. _rerank_and_calibrate — cross-encoder reranking + confidence threshold
 10. _build_llm_prompt    — assemble context string for the LLM call
 11. _generate_answer     — LLM generation with history
 12. _build_citations     — format structured citation list
 13. search_node          — orchestrator: wires the pipeline, returns state
"""
from __future__ import annotations

import json as _json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import Settings, get_settings
from ..core.llm import get_llm, invoke_with_retry
from ..core.utils import (
    DOC_REF_RE,
    build_final_response,
    build_history_messages,
    extract_recent_filename,
)
from ..graph.state import AgentState
from ..prompts import SEARCH_EXPERT, SEARCH_EXPAND_QUERY
from ..tools.bm25_search import bm25_search
from ..tools.neo4j_query import (
    graph_entity_lookup,
    graph_obligation_search,
    graph_section_search,
    neo4j_query,
)
from ..tools.qdrant_search import qdrant_search, qdrant_text_search
from ..tools.reranker import reranker

logger = logging.getLogger(__name__)

# ── Module-level thread pool for parallel Neo4j queries ──────────────────────
# Lazy singleton with double-checked locking — created once, reused per request.
# Prevents unbounded thread creation when many requests arrive simultaneously.

_graph_pool: ThreadPoolExecutor | None = None
_graph_pool_lock = threading.Lock()


def _get_graph_pool() -> ThreadPoolExecutor:
    global _graph_pool
    if _graph_pool is None:
        with _graph_pool_lock:
            if _graph_pool is None:
                _graph_pool = ThreadPoolExecutor(
                    max_workers=get_settings().graph_pool_workers,
                    thread_name_prefix="graph-query",
                )
    return _graph_pool


# ── Domain-specific regex patterns ───────────────────────────────────────────

_ARTICLE_NUM_RE = re.compile(r"[Сс]тать[яей]\s+(\d+)", re.UNICODE)

_ENTITY_ORG_RE = re.compile(
    r'\b(ТОО|АО|НАО|ОАО|ЗАО|ГП|ЧП|МФО|БВУ|Kaspi|Halyk|БТА|Цесна|Jusan|Нурбанк|Евразийский|Bereke)\b',
    re.UNICODE,
)
_CAPS_SEQUENCE_RE = re.compile(
    r'\b[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{2,}){1,}\b',
    re.UNICODE,
)
_LAW_QUERY_RE = re.compile(
    r'(?:Закон\s+(?:РК|Республики\s+Казахстан)\s+о[б]?\s+[а-яёА-ЯЁ][а-яё\s,]{4,50}'
    r'|(?:Гражданский|Налоговый|Трудовой|Уголовный)\s+кодекс'
    r'|(?:ГК|НК|ТК|КоАП|УК)\s+(?:РК|Республики\s+Казахстан))',
    re.IGNORECASE | re.UNICODE,
)
_OBL_KEYWORDS = frozenset([
    "обяза", "должен", "обязан", "ответствен", "вправе", "право ",
    "запрещ", "недопустим", "обязательств",
])
_QUOTED_TEXT_RE = re.compile(
    r'["\u00ab\u201c\u2018](.{8,}?)["\u00bb\u201d\u2019]', re.UNICODE | re.DOTALL
)
_EXACT_SEARCH_KW = frozenset([
    "найди строку", "найди фрагмент", "найди текст", "найди цитату",
    "найди точный", "найди дословно", "есть ли строка", "содержит строку",
    "в каком документе", "в каком файле", "какой документ содержит",
])
_EXACT_SEARCH_PREFIXES: tuple[str, ...] = (
    "в каком документе находится этот абзац ",
    "в каком документе находится этот текст ",
    "в каком документе находится ",
    "в каком документе есть ",
    "в каком файле находится ",
    "какой документ содержит ",
    "найди строку ", "найди фрагмент ", "найди текст ", "найди цитату ",
    "найди точный ", "найди дословно ", "есть ли строка ", "содержит строку ",
    "в каком документе ", "в каком файле ",
)
_RU_STOPWORDS = frozenset([
    "и", "в", "на", "по", "с", "к", "о", "об", "из", "от", "до", "для",
    "что", "как", "это", "все", "при", "или", "но", "не", "да", "же",
    "а", "то", "так", "где", "когда", "если", "чтобы", "кто", "который",
    "мне", "мы", "вы", "он", "она", "они", "его", "её", "их", "этот",
    "эта", "эти", "того", "тот", "за", "со", "во", "без", "под", "над",
])


# ── Small data helpers ────────────────────────────────────────────────────────

def _extract_graph_keywords(query: str, max_words: int) -> str:
    """Return significant words from query for Neo4j keyword search."""
    words = re.findall(r"[а-яёА-ЯЁa-zA-Z0-9]+", query)
    significant = [w for w in words if w.lower() not in _RU_STOPWORDS and len(w) >= 3]
    return " ".join(significant[:max_words])


def _extract_filename_from_hit(hit: dict) -> str:
    """Extract the source filename from a Qdrant hit's payload metadata."""
    meta = hit.get("metadata", {})
    raw = meta.get("meta_json")
    if raw:
        try:
            inner = _json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(inner, dict) and inner.get("filename"):
                return str(inner["filename"])
        except Exception:
            pass
    for field in ("title", "filename", "source"):
        val = meta.get(field, "")
        if val:
            return str(val)
    question = hit.get("question") or meta.get("question", "")
    if "|" in question:
        return question.split("|")[0].strip()
    return ""


def _extract_page_num(hit: dict) -> str | None:
    """Extract page number from a Qdrant hit's payload metadata."""
    meta = hit.get("metadata", {})
    pn = meta.get("page_number") or meta.get("page")
    if pn is not None:
        return str(pn)
    raw = meta.get("meta_json")
    if raw:
        try:
            inner = _json.loads(raw) if isinstance(raw, str) else raw
            pn = inner.get("page_number") or inner.get("page")
            if pn is not None:
                return str(pn)
        except Exception:
            pass
    return None


# ── Stage 1: Query preparation ────────────────────────────────────────────────

def _prepare_query(query: str, state: AgentState) -> tuple[str, str]:
    """Enrich referential queries and produce an expanded embedding query.

    Returns:
        (lexical_query, embed_query) — lexical keeps original wording for BM25;
        embed_query is LLM-rewritten for better semantic recall.
    """
    if DOC_REF_RE.search(query):
        filename = extract_recent_filename(state)
        if filename:
            query = f"{filename} {query}"
            logger.info("search: referential query enriched with filename=%s", filename)

    embed_query = _expand_query(query)
    return query, embed_query


def _expand_query(query: str) -> str:
    """LLM rewrite for better semantic retrieval. Non-fatal — returns original on error."""
    if len(query) < 15:
        return query

    llm = get_llm(temperature=0.0, num_predict=120)
    prompt = SEARCH_EXPAND_QUERY.format(query=query)
    try:
        expanded = invoke_with_retry(
            llm, [HumanMessage(content=prompt)], max_retries=1
        ).strip()
        if expanded and expanded != query and len(expanded) < 600:
            logger.debug("query_expansion: '%s' -> '%s'", query[:60], expanded[:60])
            return expanded
    except Exception as exc:
        logger.debug("query_expansion failed (non-critical): %s", exc)
    return query


# ── Stage 2: Exact-string search detection ────────────────────────────────────

def _detect_exact_search(query: str) -> tuple[bool, str]:
    """Determine whether the query requests a literal substring match.

    Returns:
        (is_exact, exact_text) — exact_text is the string to search for.
    """
    query_lower = query.lower()
    quoted = _QUOTED_TEXT_RE.search(query)
    is_exact = bool(quoted or any(kw in query_lower for kw in _EXACT_SEARCH_KW))

    if not is_exact:
        return False, ""

    if quoted:
        return True, quoted.group(1).strip()

    search_text = query
    for prefix in _EXACT_SEARCH_PREFIXES:
        if query_lower.startswith(prefix):
            search_text = query[len(prefix):].strip()
            break
    return True, search_text


# ── Stage 3: Exact Qdrant text search ────────────────────────────────────────

def _retrieve_exact(exact_text: str, s: Settings) -> list[dict]:
    """Run Qdrant server-side MatchText search. Non-fatal."""
    if not exact_text:
        return []
    try:
        hits = qdrant_text_search.invoke({
            "text": exact_text,
            "collection": s.qdrant_collection,
            "limit": 8,
        })
        logger.info("exact_search '%s' -> %d hits", exact_text[:60], len(hits))
        return hits
    except Exception as exc:
        logger.warning("exact_search failed: %s", exc)
        return []


# ── Stage 4: Vector + BM25 retrieval ─────────────────────────────────────────

def _retrieve_vector_bm25(
    embed_query: str, lexical_query: str, s: Settings
) -> tuple[list[dict], list[dict]]:
    """Run Qdrant vector search, then BM25 over its results.

    Returns:
        (vector_hits, bm25_hits)
    """
    vector_hits = qdrant_search.invoke({
        "query": embed_query,
        "collection": s.qdrant_collection,
        "limit": s.qdrant_top_k,
    })
    bm25_hits = bm25_search.invoke({
        "query": lexical_query,
        "documents": vector_hits,
        "top_k": s.bm25_top_k,
    })
    return vector_hits, bm25_hits


# ── Stage 5: Parallel Neo4j graph enrichment ─────────────────────────────────

def _retrieve_graph(query: str, s: Settings) -> dict[str, list[dict]]:
    """Run 5 graph queries in parallel with a timeout.

    Returns dict with keys: sections, articles, obligations, entities, laws.
    Any individual query failure is non-fatal (returns empty list for that key).
    """
    kw = _extract_graph_keywords(query, s.graph_kw_max_words)
    art_match = _ARTICLE_NUM_RE.search(query)
    needs_obligations = any(ob_kw in query.lower() for ob_kw in _OBL_KEYWORDS)
    needs_entities = bool(_ENTITY_ORG_RE.search(query) or _CAPS_SEQUENCE_RE.search(query))
    law_match = _LAW_QUERY_RE.search(query)

    def _sections() -> list[dict]:
        return graph_section_search.invoke({"keywords": kw, "limit": 4})

    def _articles() -> list[dict]:
        if not art_match:
            return []
        return neo4j_query.invoke({
            "cypher": (
                "MATCH (a:Article {number: $num})<-[:HAS_ARTICLE]-(s:Section)"
                "<-[:CONTAINS]-(d:Document) "
                "RETURN a.number AS number, a.title AS title, "
                "s.text_preview AS text, d.filename AS source LIMIT 3"
            ),
            "params": {"num": int(art_match.group(1))},
        })

    def _obligations() -> list[dict]:
        if not needs_obligations:
            return []
        return graph_obligation_search.invoke({"keywords": kw, "limit": 3})

    def _entities() -> list[dict]:
        if not needs_entities:
            return []
        return graph_entity_lookup.invoke({"text": kw[:60], "limit": 4})

    def _laws() -> list[dict]:
        if not law_match:
            return []
        return neo4j_query.invoke({
            "cypher": (
                "MATCH (l:Law) "
                "WHERE toLower(l.title) CONTAINS toLower($kw) "
                "OPTIONAL MATCH (l)-[:HAS_ARTICLE]->(a:Article) "
                "RETURN l.law_id AS law_id, l.title AS title, "
                "collect({number: a.number, title: a.title}) AS articles LIMIT 3"
            ),
            "params": {"kw": law_match.group(0)[:80]},
        })

    results: dict[str, list[dict]] = {
        k: [] for k in ("sections", "articles", "obligations", "entities", "laws")
    }
    task_map = {
        "sections": _sections,
        "articles": _articles,
        "obligations": _obligations,
        "entities": _entities,
        "laws": _laws,
    }

    pool = _get_graph_pool()
    futures = {pool.submit(fn): name for name, fn in task_map.items()}
    try:
        for future in as_completed(futures, timeout=s.graph_enrichment_timeout):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                logger.warning("graph_%s lookup failed: %s", name, exc)
    except TimeoutError:
        logger.warning(
            "search: graph enrichment timeout (%.1fs) — using partial results",
            s.graph_enrichment_timeout,
        )

    return results


# ── Stage 6: Normalize graph records to unified hit format ───────────────────

def _normalize_graph_hits(graph_results: dict[str, list[dict]], s: Settings) -> list[dict]:
    """Convert raw Neo4j records to the same dict shape as Qdrant hits."""
    hits: list[dict] = []

    for sec in graph_results.get("sections", []):
        text = sec.get("text") or ""
        if text:
            hits.append({
                "id": sec.get("section_id", ""),
                "content": text[:s.content_snippet_max_len],
                "score": float(sec.get("score", 0.0)),
                "section": f"стр. {sec.get('page', '?')} — {sec.get('source', '')}",
                "metadata": {"articles": sec.get("articles", [])},
            })

    for rec in graph_results.get("articles", []):
        if rec.get("text"):
            art_num = rec.get("number", "?")
            title_part = f": {rec['title']}" if rec.get("title") else ""
            hits.append({
                "id": f"article:{art_num}:{rec.get('source', '')}",
                "content": f"Статья {art_num}{title_part}\n{rec['text'][:s.content_snippet_max_len]}",
                "score": 1.0,
                "section": rec.get("source", ""),
                "metadata": {"article_number": art_num, "article_title": rec.get("title", "")},
            })

    for rec in graph_results.get("obligations", []):
        content = " — ".join(
            p for p in [rec.get("subject"), rec.get("action"), rec.get("object")] if p
        )
        if rec.get("evidence"):
            content += f"\n{rec['evidence']}"
        hits.append({
            "id": f"obl:{rec.get('doc_id', '')}:{hash(content)}",
            "content": content[:s.content_snippet_max_len],
            "score": float(rec.get("confidence", 0.7)),
            "section": rec.get("document", ""),
            "metadata": {"type": "obligation", "deadline": rec.get("deadline", "")},
        })

    for rec in graph_results.get("entities", []):
        ent_text = rec.get("text", "")
        if not ent_text:
            continue
        ent_label = rec.get("label", "")
        content_parts = [f"{ent_label}: {ent_text}"]
        if rec.get("role"):
            content_parts.append(f"Роль: {rec['role']}")
        if rec.get("evidence"):
            content_parts.append(rec["evidence"])
        docs = rec.get("documents") or []
        hits.append({
            "id": f"ent:{ent_label}:{ent_text}",
            "content": "\n".join(content_parts)[:s.content_snippet_max_len],
            "score": float(rec.get("confidence", 0.6)),
            "section": ", ".join(docs[:2]) if docs else "",
            "metadata": {"type": "entity", "entity_label": ent_label, "entity_text": ent_text},
        })

    for rec in graph_results.get("laws", []):
        law_title = rec.get("title", "")
        if not law_title:
            continue
        articles = rec.get("articles") or []
        art_list = ", ".join(
            f"ст.{a.get('number', '')}{': ' + a['title'] if a.get('title') else ''}"
            for a in articles[:10] if a.get("number")
        )
        content = f"Закон: {law_title}"
        if art_list:
            content += f"\nСтатьи: {art_list}"
        hits.append({
            "id": f"law:{rec.get('law_id', law_title)}",
            "content": content[:s.content_snippet_max_len],
            "score": 1.0,
            "section": law_title,
            "metadata": {"type": "law", "law_id": rec.get("law_id", "")},
        })

    return hits


# ── Stage 7: Merge all hit sources, deduplicating by ID ──────────────────────

def _merge_all_hits(
    exact_hits: list[dict],
    vector_hits: list[dict],
    bm25_hits: list[dict],
    graph_hits: list[dict],
) -> tuple[list[dict], set[str]]:
    """Combine all hit sources into a single deduplicated list.

    Graph hits bypass the cosine-score filter in stage 8 and are appended after
    it, so they are returned separately in the second position.

    Returns:
        (qdrant_merged, seen_ids) — qdrant_merged contains exact+vector+bm25 hits.
        seen_ids is passed to stage 8 to avoid double-adding graph hits.
    """
    seen_ids: set[str] = set()
    merged: list[dict] = []

    for idx, hit in enumerate(exact_hits + vector_hits + bm25_hits):
        raw_id = hit.get("id")
        hit_id = str(raw_id) if raw_id else f"idx:{idx}:{hash(hit.get('content', ''))}"
        if hit_id not in seen_ids:
            seen_ids.add(hit_id)
            merged.append(hit)

    # Graph hits added after deduplication pass so seen_ids stays accurate
    for hit in graph_hits:
        raw_id = hit.get("id")
        hit_id = str(raw_id) if raw_id else f"graph:{hash(hit.get('content', ''))}"
        if hit_id not in seen_ids:
            seen_ids.add(hit_id)
            merged.append(hit)

    return merged, seen_ids


# ── Stage 8: Relevance filtering ─────────────────────────────────────────────

def _filter_by_relevance(hits: list[dict], s: Settings) -> list[dict]:
    """Drop hits below min_relevance_score. Falls back to all if everything is filtered."""
    filtered = [h for h in hits if h.get("score", 0) >= s.min_relevance_score]
    if not filtered and hits:
        logger.warning(
            "search: all %d hits below min_relevance_score=%.2f — using all",
            len(hits), s.min_relevance_score,
        )
        return hits
    return filtered


# ── Stage 9: Cross-encoder reranking + confidence calibration ────────────────

def _rerank_and_calibrate(
    query: str, candidates: list[dict], s: Settings
) -> tuple[list[dict], float, bool]:
    """Rerank with cross-encoder and decide whether context is usable.

    Returns:
        (reranked, best_score, has_context)
    """
    top_candidates = sorted(
        candidates, key=lambda d: float(d.get("score", 0)), reverse=True
    )[:s.rerank_top_k * 2]

    reranked = (
        reranker.invoke({"query": query, "documents": top_candidates, "top_k": s.rerank_top_k})
        if top_candidates else []
    )

    best_score = reranked[0].get("rerank_score", 0.0) if reranked else 0.0
    has_context = bool(reranked) and best_score >= s.search_min_confidence
    return reranked, best_score, has_context


# ── Stage 10: LLM prompt assembly ────────────────────────────────────────────

def _build_llm_prompt(
    query: str,
    is_exact: bool,
    exact_text: str,
    exact_hits: list[dict],
    exact_hit_ids: set[str],
    reranked: list[dict],
    has_context: bool,
    best_score: float,
    s: Settings,
) -> str:
    """Build the user message for the LLM based on retrieval results."""

    def _ctx_snippet(d: dict) -> str:
        """Return content at full length for exact hits, snippet length otherwise."""
        limit = 1200 if str(d.get("id", "")) in exact_hit_ids else s.content_snippet_max_len
        return d.get("content", "")[:limit]

    if is_exact and exact_hits:
        exact_blocks = "\n\n".join(
            "─" * 60 + f"\n[Совпадение {i + 1}]\n"
            f"Документ: {_extract_filename_from_hit(h) or '—'}\n"
            f"Страница: {_extract_page_num(h) or '?'}\n"
            f"Секция: {h.get('section', '—')}\n"
            f"Найденный фрагмент:\n{h.get('content', '')[:1000]}"
            for i, h in enumerate(exact_hits[:5])
        )
        return (
            f"Пользователь ищет точный фрагмент текста в базе документов.\n\n"
            f"ИСКОМЫЙ ФРАГМЕНТ:\n{exact_text[:600]}\n\n"
            f"НАЙДЕННЫЕ СОВПАДЕНИЯ:\n{exact_blocks}\n\n"
            "Укажи точно: в каком документе найден фрагмент, на какой странице, в какой секции. "
            "Процитируй первые 2-3 строки найденного фрагмента из источника."
        )

    if has_context:
        context = "\n\n".join(
            f"[{i + 1}] {_ctx_snippet(d)}" for i, d in enumerate(reranked)
        )
        return f"Контекст:\n{context}\n\nВопрос: {query}"

    # Low-confidence path — honest "not found" rather than hallucination
    logger.warning(
        "search: low confidence (best_score=%.3f, docs=%d) for query=%r",
        best_score, len(reranked), query,
    )
    return (
        f"Вопрос: {query}\n\n"
        "ВАЖНО: Релевантные документы в базе знаний не найдены (или уровень уверенности "
        "слишком низкий). Сообщи об этом пользователю прямо и честно. "
        "Не придумывай информацию. Предложи уточнить запрос или загрузить нужный документ."
    )


# ── Stage 11: LLM answer generation ──────────────────────────────────────────

def _generate_answer(user_msg: str, state: AgentState, s: Settings) -> str:
    """Call the LLM with system prompt + conversation history + retrieval context."""
    llm = get_llm()
    history = build_history_messages(state, max_turns=s.history_turns)
    return invoke_with_retry(llm, [
        SystemMessage(content=SEARCH_EXPERT),
        *history,
        HumanMessage(content=user_msg),
    ])


# ── Stage 12: Citation builder ────────────────────────────────────────────────

def _build_citations(
    reranked: list[dict], exact_hit_ids: set[str], s: Settings
) -> list[dict]:
    """Format the reranked documents into structured citation records."""
    return [
        {
            "index": i + 1,
            "content_preview": d.get("content", "")[:s.citation_preview_max_len],
            "match_content": d.get("content", "") if str(d.get("id", "")) in exact_hit_ids else "",
            "is_exact_match": str(d.get("id", "")) in exact_hit_ids,
            "page_number": _extract_page_num(d),
            "section": d.get("section", ""),
            "score": d.get("rerank_score", d.get("score", 0)),
            "filename": _extract_filename_from_hit(d),
            "metadata": d.get("metadata", {}),
        }
        for i, d in enumerate(reranked)
    ]


# ── Graph entry point ─────────────────────────────────────────────────────────

def search_node(state: AgentState) -> AgentState:
    """Hybrid RAG pipeline node: vector + BM25 + graph → rerank → generate → cite."""
    t_start = time.perf_counter()
    s = get_settings()
    query = state["user_query"]

    # 1. Prepare query
    lexical_query, embed_query = _prepare_query(query, state)

    # 2-3. Exact-string search (when requested)
    is_exact, exact_text = _detect_exact_search(lexical_query)
    exact_hits = _retrieve_exact(exact_text, s) if is_exact else []
    exact_hit_ids = {str(h.get("id")) for h in exact_hits if h.get("id")}

    # 4. Vector + BM25
    vector_hits, bm25_hits = _retrieve_vector_bm25(embed_query, lexical_query, s)

    # 5-6. Graph enrichment + normalization
    graph_results = _retrieve_graph(lexical_query, s)
    graph_hits = _normalize_graph_hits(graph_results, s)

    # 7. Merge all sources
    merged, _ = _merge_all_hits(exact_hits, vector_hits, bm25_hits, graph_hits)

    # 8. Relevance filter
    relevant = _filter_by_relevance(merged, s)

    # 9. Rerank + confidence
    reranked, best_score, has_context = _rerank_and_calibrate(lexical_query, relevant, s)

    # 10-11. Prompt + answer
    user_msg = _build_llm_prompt(
        lexical_query, is_exact, exact_text, exact_hits, exact_hit_ids,
        reranked, has_context, best_score, s,
    )
    answer = _generate_answer(user_msg, state, s)

    # 12. Citations
    citations = _build_citations(reranked, exact_hit_ids, s)

    # Multi-intent: prepend prior agents' responses
    final_response = build_final_response(answer, state.get("combined_responses") or [])

    elapsed = time.perf_counter() - t_start
    metrics = {
        "node": "search",
        "query_len": len(lexical_query),
        "query_expanded": embed_query != lexical_query,
        "vector_hits": len(vector_hits),
        "bm25_hits": len(bm25_hits),
        "graph_hits": len(graph_hits),
        "entity_hits": len(graph_results.get("entities", [])),
        "law_hits": len(graph_results.get("laws", [])),
        "merged_hits": len(merged),
        "relevant_hits": len(relevant),
        "reranked_hits": len(reranked),
        "best_rerank_score": round(best_score, 4),
        "has_context": has_context,
        "is_exact_search": is_exact,
        "elapsed_s": round(elapsed, 2),
    }
    logger.info("search_node metrics: %s", metrics)

    return {
        **state,
        "query_expanded": embed_query,
        "vector_hits": vector_hits,
        "bm25_hits": bm25_hits,
        "graph_hits": graph_hits,
        "reranked_docs": reranked,
        "search_result": answer,
        "citations": citations,
        "final_response": final_response,
        "retrieval_metrics": metrics,
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
