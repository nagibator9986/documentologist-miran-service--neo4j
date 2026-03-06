"""Search Agent — hybrid RAG: Qdrant + BM25 + Neo4j + reranking + citations.

Improvements:
- Query expansion via LLM rewrite before embedding (better recall)
- Uses s.history_turns from config (not hardcoded 2)
- Confidence calibration: explicit "not found" when context is weak
- Structured retrieval metrics logged for observability
- Multi-intent: prepends combined_responses from prior agents
"""
from __future__ import annotations

import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_llm, invoke_with_retry
from ..core.utils import build_history_messages
from ..graph.state import AgentState
from ..tools.bm25_search import bm25_search
from ..tools.neo4j_query import (
    graph_obligation_search,
    graph_section_search,
    neo4j_query,
)
from ..tools.qdrant_search import qdrant_search, qdrant_text_search
from ..tools.reranker import reranker

_ARTICLE_NUM_RE = re.compile(r"[Сс]тать[яей]\s+(\d+)", re.UNICODE)

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
    "найди строку ",
    "найди фрагмент ",
    "найди текст ",
    "найди цитату ",
    "найди точный ",
    "найди дословно ",
    "есть ли строка ",
    "содержит строку ",
    "в каком документе ",
    "в каком файле ",
)

logger = logging.getLogger(__name__)

_DOC_REF_RE = re.compile(
    r"\b(этот\s+документ|этого\s+документа|в\s+нём|в\s+нем|в\s+ней|об\s+этом|данный\s+документ|"
    r"этот\s+файл|в\s+этом\s+документе|из\s+этого\s+документа|этот\s+текст)\b",
    re.IGNORECASE | re.UNICODE,
)
_FILENAME_RE = re.compile(r"[\w\-]+\.(?:pdf|docx|doc|txt|json)\b", re.IGNORECASE)

_RU_STOPWORDS = frozenset([
    "и", "в", "на", "по", "с", "к", "о", "об", "из", "от", "до", "для",
    "что", "как", "это", "все", "при", "или", "но", "не", "да", "же",
    "а", "то", "так", "где", "когда", "если", "чтобы", "кто", "который",
    "мне", "мы", "вы", "он", "она", "они", "его", "её", "их", "этот",
    "эта", "эти", "того", "тот", "за", "со", "во", "без", "под", "над",
])

_SYSTEM = """Ты — эксперт по банковскому праву и финансовым продуктам Казахстана.
Используй предоставленный контекст для точного, структурированного ответа.
Указывай источники в конце ответа в формате [Источник N].
Если информации нет в контексте — скажи об этом прямо, не придумывай.
ВАЖНО: Отвечай ТОЛЬКО на русском языке, независимо от языка запроса и контекста.
"""

# Minimum sigmoid-normalised rerank_score to consider context reliable
_MIN_CONFIDENCE = 0.25


def _recent_filename(state: AgentState) -> str | None:
    for msg in reversed((state.get("messages") or [])[:-1]):
        content = getattr(msg, "content", "") or ""
        matches = _FILENAME_RE.findall(content)
        if matches:
            return matches[0]
    return None


def _extract_graph_keywords(query: str, max_words: int) -> str:
    words = re.findall(r"[а-яёА-ЯЁa-zA-Z0-9]+", query)
    significant = [w for w in words if w.lower() not in _RU_STOPWORDS and len(w) >= 3]
    return " ".join(significant[:max_words])


def _extract_filename(hit: dict) -> str:
    import json as _json
    meta = hit.get("metadata", {})
    meta_json_raw = meta.get("meta_json")
    if meta_json_raw:
        try:
            inner = _json.loads(meta_json_raw) if isinstance(meta_json_raw, str) else meta_json_raw
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


def _expand_query(query: str) -> str:
    """Rewrite the query using LLM for better semantic retrieval.

    Adds synonyms and clarifies legal terms. Returns original query on any error
    so retrieval is never blocked.
    """
    if len(query) < 15:
        return query

    llm = get_llm(temperature=0.0, num_predict=120)
    prompt = (
        "Ты — помощник по поиску в банковских и юридических документах Казахстана.\n"
        "Перепиши запрос так, чтобы улучшить семантический поиск: добавь синонимы, "
        "раскрой сокращения, уточни юридические термины.\n"
        "Верни ТОЛЬКО улучшенный запрос без объяснений.\n\n"
        f"Исходный запрос: {query}\n"
        "Улучшенный запрос:"
    )
    try:
        expanded = invoke_with_retry(llm, [HumanMessage(content=prompt)], max_retries=1).strip()
        if expanded and expanded != query and len(expanded) < 600:
            logger.debug("query_expansion: '%s' -> '%s'", query[:60], expanded[:60])
            return expanded
    except Exception as exc:
        logger.debug("query_expansion failed (non-critical): %s", exc)
    return query


def _page_num(d: dict) -> str | None:
    import json as _json
    meta = d.get("metadata", {})
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


def search_node(state: AgentState) -> AgentState:
    """Execute hybrid retrieval: vector + BM25 + graph, then rerank and answer."""
    t_start = time.perf_counter()
    s = get_settings()
    query = state["user_query"]
    query_lower = query.lower()

    # ── 0a. Referential query enrichment ("этот документ" etc.) ─────────────
    _ref_filename: str | None = None
    if _DOC_REF_RE.search(query):
        _ref_filename = _recent_filename(state)
        if _ref_filename:
            query = f"{_ref_filename} {query}"
            query_lower = query.lower()
            logger.info("search_node: referential query enriched with filename=%s", _ref_filename)

    # ── 0b. Query expansion via LLM rewrite ──────────────────────────────────
    query_expanded = _expand_query(query)
    # Use expanded query for embeddings; original for BM25/graph (lexical match)
    embed_query = query_expanded

    # ── 0c. Exact-string search detection ────────────────────────────────────
    exact_hits: list[dict] = []
    exact_text: str = ""
    quoted_match = _QUOTED_TEXT_RE.search(query)
    is_exact_search = bool(quoted_match or any(kw in query_lower for kw in _EXACT_SEARCH_KW))

    if is_exact_search:
        if quoted_match:
            exact_text = quoted_match.group(1).strip()
        else:
            search_text = query
            for prefix in _EXACT_SEARCH_PREFIXES:
                if query_lower.startswith(prefix):
                    search_text = query[len(prefix):].strip()
                    break
            exact_text = search_text
        if exact_text:
            try:
                exact_hits = qdrant_text_search.invoke({
                    "text": exact_text,
                    "collection": s.qdrant_collection,
                    "limit": 8,
                })
                logger.info("qdrant_text_search '%s' -> %d exact matches", exact_text[:60], len(exact_hits))
            except Exception as exc:
                logger.warning("qdrant_text_search failed: %s", exc)

    exact_hit_ids: set[str] = {str(h.get("id")) for h in exact_hits if h.get("id")}

    # ── 1. Vector search (expanded query for better recall) ──────────────────
    vector_hits = qdrant_search.invoke({
        "query": embed_query,
        "collection": s.qdrant_collection,
        "limit": s.qdrant_top_k,
    })

    # ── 2. BM25 lexical re-ranking over vector candidates ────────────────────
    bm25_hits = bm25_search.invoke({
        "query": query,  # original query for BM25
        "documents": vector_hits,
        "top_k": s.bm25_top_k,
    })

    # ── 3. Merge & deduplicate ────────────────────────────────────────────────
    seen_ids: set[str] = set()
    merged = []
    for idx, hit in enumerate(exact_hits + vector_hits + bm25_hits):
        raw_id = hit.get("id")
        hit_id = str(raw_id) if raw_id else f"idx:{idx}:{hash(hit.get('content', ''))}"
        if hit_id not in seen_ids:
            seen_ids.add(hit_id)
            merged.append(hit)

    # ── 4. Graph enrichment ───────────────────────────────────────────────────
    graph_hits: list[dict] = []
    try:
        kw = _extract_graph_keywords(query, s.graph_kw_max_words)
        raw_sections = graph_section_search.invoke({"keywords": kw, "limit": 4})
        for sec in raw_sections:
            text = sec.get("text") or ""
            if text:
                graph_hits.append({
                    "id": sec.get("section_id", ""),
                    "content": text[:s.content_snippet_max_len],
                    "score": float(sec.get("score", 0.0)),
                    "section": f"стр. {sec.get('page', '?')} — {sec.get('source', '')}",
                    "metadata": {"articles": sec.get("articles", [])},
                })
    except Exception as exc:
        logger.warning("Graph section search failed: %s", exc)

    # ── 5. Filter by minimum cosine score ────────────────────────────────────
    relevant_qdrant = [h for h in merged if h.get("score", 0) >= s.min_relevance_score]
    if not relevant_qdrant and merged:
        logger.warning(
            "search_node: all %d hits below min_relevance_score=%.2f — using all",
            len(merged), s.min_relevance_score,
        )
        relevant_qdrant = merged

    # ── 5b. Merge graph hits (bypass cosine filter) ───────────────────────────
    seen_ids_2: set[str] = set()
    for idx, h in enumerate(relevant_qdrant):
        raw_id = h.get("id")
        seen_ids_2.add(str(raw_id) if raw_id else f"idx:{idx}:{hash(h.get('content', ''))}")
    for gh in graph_hits:
        raw_id = gh.get("id")
        gh_id = str(raw_id) if raw_id else f"graph:{hash(gh.get('content', ''))}"
        if gh_id not in seen_ids_2:
            seen_ids_2.add(gh_id)
            relevant_qdrant.append(gh)

    # ── 5c. Article number auto-lookup ───────────────────────────────────────
    if art_match := _ARTICLE_NUM_RE.search(query):
        try:
            art_num = int(art_match.group(1))
            art_records = neo4j_query.invoke({
                "cypher": (
                    "MATCH (a:Article {number: $num})<-[:HAS_ARTICLE]-(s:Section)"
                    "<-[:CONTAINS]-(d:Document) "
                    "RETURN a.number AS number, a.title AS title, "
                    "s.text_preview AS text, d.filename AS source LIMIT 3"
                ),
                "params": {"num": art_num},
            })
            for rec in art_records:
                if rec.get("text"):
                    art_id = f"article:{art_num}:{rec.get('source', '')}"
                    if art_id not in seen_ids_2:
                        seen_ids_2.add(art_id)
                        title_part = f": {rec['title']}" if rec.get("title") else ""
                        relevant_qdrant.append({
                            "id": art_id,
                            "content": (
                                f"Статья {art_num}{title_part}\n"
                                f"{rec.get('text', '')[:s.content_snippet_max_len]}"
                            ),
                            "score": 1.0,
                            "section": rec.get("source", ""),
                            "metadata": {
                                "article_number": art_num,
                                "article_title": rec.get("title", ""),
                            },
                        })
        except Exception as exc:
            logger.warning("Article auto-lookup failed: %s", exc)

    # ── 5d. Obligation search ─────────────────────────────────────────────────
    if any(kw in query.lower() for kw in _OBL_KEYWORDS):
        try:
            obl_records = graph_obligation_search.invoke({
                "keywords": _extract_graph_keywords(query, s.graph_kw_max_words),
                "limit": 3,
            })
            for rec in obl_records:
                content = " — ".join(
                    p for p in [rec.get("subject"), rec.get("action"), rec.get("object")] if p
                )
                if rec.get("evidence"):
                    content += f"\n{rec['evidence']}"
                obl_id = f"obl:{rec.get('doc_id', '')}:{hash(content)}"
                if obl_id not in seen_ids_2:
                    seen_ids_2.add(obl_id)
                    relevant_qdrant.append({
                        "id": obl_id,
                        "content": content[:s.content_snippet_max_len],
                        "score": float(rec.get("confidence", 0.7)),
                        "section": rec.get("document", ""),
                        "metadata": {"type": "obligation", "deadline": rec.get("deadline", "")},
                    })
        except Exception as exc:
            logger.warning("Obligation search failed: %s", exc)

    relevant = relevant_qdrant

    # ── 6. Cross-encoder reranking ────────────────────────────────────────────
    rerank_input = sorted(
        relevant, key=lambda d: float(d.get("score", 0)), reverse=True
    )[:s.rerank_top_k * 2]
    reranked = (
        reranker.invoke({"query": query, "documents": rerank_input, "top_k": s.rerank_top_k})
        if rerank_input else []
    )

    # ── 6b. Confidence calibration ────────────────────────────────────────────
    best_score = reranked[0].get("rerank_score", 0.0) if reranked else 0.0
    has_context = bool(reranked) and best_score >= _MIN_CONFIDENCE

    # ── 7. Build LLM prompt ───────────────────────────────────────────────────
    def _ctx_content(d: dict) -> str:
        if str(d.get("id", "")) in exact_hit_ids:
            return d.get("content", "")[:1200]
        return d.get("content", "")[:s.content_snippet_max_len]

    if is_exact_search and exact_hits:
        exact_blocks = "\n\n".join(
            "─" * 60 + f"\n[Совпадение {i + 1}]\n"
            f"Документ: {_extract_filename(h) or '—'}\n"
            f"Страница: {_page_num(h) or '?'}\n"
            f"Секция: {h.get('section', '—')}\n"
            f"Найденный фрагмент:\n{h.get('content', '')[:1000]}"
            for i, h in enumerate(exact_hits[:5])
        )
        user_msg = (
            f"Пользователь ищет точный фрагмент текста в базе документов.\n\n"
            f"ИСКОМЫЙ ФРАГМЕНТ:\n{exact_text[:600]}\n\n"
            f"НАЙДЕННЫЕ СОВПАДЕНИЯ:\n{exact_blocks}\n\n"
            f"Укажи точно: в каком документе найден фрагмент, на какой странице, в какой секции. "
            f"Процитируй первые 2-3 строки найденного фрагмента из источника."
        )
    elif has_context:
        context = "\n\n".join(
            f"[{i + 1}] {_ctx_content(d)}" for i, d in enumerate(reranked)
        )
        user_msg = f"Контекст:\n{context}\n\nВопрос: {query}"
    else:
        # Confidence calibration: be honest — no hallucinations
        logger.warning(
            "search_node: low confidence (best_score=%.3f, docs=%d) for query=%r",
            best_score, len(reranked), query,
        )
        user_msg = (
            f"Вопрос: {query}\n\n"
            "ВАЖНО: Релевантные документы в базе знаний не найдены (или уровень уверенности "
            "слишком низкий). Сообщи об этом пользователю прямо и честно. "
            "Не придумывай информацию. Предложи уточнить запрос или загрузить нужный документ."
        )

    # ── 8. Generate answer ────────────────────────────────────────────────────
    llm = get_llm()
    history = build_history_messages(state, max_turns=s.history_turns)
    answer = invoke_with_retry(llm, [
        SystemMessage(content=_SYSTEM),
        *history,
        HumanMessage(content=user_msg),
    ])

    # ── 9. Citations ──────────────────────────────────────────────────────────
    citations = [
        {
            "index": i + 1,
            "content_preview": d.get("content", "")[:s.citation_preview_max_len],
            "match_content": d.get("content", "") if str(d.get("id", "")) in exact_hit_ids else "",
            "is_exact_match": str(d.get("id", "")) in exact_hit_ids,
            "page_number": _page_num(d),
            "section": d.get("section", ""),
            # rerank_score is sigmoid-normalised [0,1] — no /10 hack needed
            "score": d.get("rerank_score", d.get("score", 0)),
            "filename": _extract_filename(d),
            "metadata": d.get("metadata", {}),
        }
        for i, d in enumerate(reranked)
    ]

    # ── 10. Multi-intent: prepend previous agents' responses ─────────────────
    combined_prev = state.get("combined_responses") or []
    if combined_prev:
        final_response = "\n\n---\n\n".join(combined_prev) + "\n\n---\n\n" + answer
    else:
        final_response = answer

    # ── 11. Structured retrieval metrics ─────────────────────────────────────
    elapsed = time.perf_counter() - t_start
    metrics: dict = {
        "node": "search",
        "query_len": len(query),
        "query_expanded": query_expanded != query,
        "vector_hits": len(vector_hits),
        "bm25_hits": len(bm25_hits),
        "graph_hits": len(graph_hits),
        "merged_hits": len(merged),
        "relevant_hits": len(relevant),
        "reranked_hits": len(reranked),
        "best_rerank_score": round(best_score, 4),
        "has_context": has_context,
        "is_exact_search": is_exact_search,
        "elapsed_s": round(elapsed, 2),
    }
    logger.info("search_node metrics: %s", metrics)

    return {
        **state,
        "query_expanded": query_expanded,
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
