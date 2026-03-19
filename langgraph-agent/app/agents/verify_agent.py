"""Verify Agent — compliance check and risk scoring.

Refactored to use the unified retrieval module.

Pipeline:
  1. _fetch_document_content  — resolve what to verify (IDs / pasted text / search)
  2. _fetch_legal_context     — unified retrieval for applicable norms
  3. _run_compliance_llm      — LLM JSON generation (stateless, no history)
  4. _parse_verify_result     — validate and normalise the JSON payload
  5. _format_summary          — produce human-readable markdown summary
  6. verify_node              — orchestrator

Key changes:
  - Legal context uses ``retrieve()`` in POINT mode (full pipeline: vector + corpus
    BM25 + Neo4j graph → cross-encoder rerank).
  - Document scope: when doc_ids present + full-document keywords, uses Map-Reduce
    to verify the entire document instead of only 5 search hits.
  - Graph obligations fetched separately (specific to verify workflow).
"""
from __future__ import annotations

import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.json_output import VerifyResult, parse_with_retry
from ..core.utils import build_final_response, strip_conversational_prefix
from ..graph.state import AgentState
from ..prompts import VERIFY_COMPLIANCE
from ..tools.neo4j_query import graph_obligation_search, graph_section_search
from ..tools.qdrant_search import qdrant_scroll_by_doc_ids
from ..tools.retrieval import retrieve

logger = logging.getLogger(__name__)

# ── Compliance consistency guard ─────────────────────────────────────────────
_NON_COMPLIANT_SIGNALS = re.compile(
    r"\b(нарушени|незаконн|не\s+соответств|запрещ|противоречит|"
    r"отсутству|не\s+имеет\s+лицензи|превышает|превышение|"
    r"не\s+выполн|неисполнени|недопустим|нарушает)\b",
    re.IGNORECASE | re.UNICODE,
)

# ── Document content signals ─────────────────────────────────────────────────
_DOCUMENT_SIGNALS_RE = re.compile(
    r"(?:"
    r"ДОГОВОР|СОГЛАШЕНИЕ|АКТ\s+О|КОНТРАКТ|"
    r"Статья\s+\d|ст\.\s*\d+|п\.\s*\d+\.|Пункт\s+\d|"
    r"№\s*\d|ПРИЛОЖЕНИЕ|РАЗДЕЛ\s+[IVXАБВГД\d]|"
    r"г\.\s+[А-ЯЁ][а-яё]+|Дата\s+составления|"
    r"Подписи\s+сторон|РЕКВИЗИТЫ\s+СТОРОН|Исполнитель:|Заказчик:|Заёмщик:|Кредитор:"
    r")",
    re.UNICODE,
)


def _is_document_content(text: str, threshold: int) -> bool:
    if len(text) > threshold:
        return True
    if len(text) > 80 and _DOCUMENT_SIGNALS_RE.search(text):
        logger.debug("verify: short text with document signals — treating as document")
        return True
    return False


# ── Stage 1: Document content resolution ─────────────────────────────────────

def _fetch_document_content(state: AgentState, query: str) -> str:
    """Resolve what text to verify.

    Priority: doc_ids → pasted document → search hits.
    """
    s = get_settings()

    doc_ids = state.get("document_ids") or []
    if doc_ids:
        chunks = qdrant_scroll_by_doc_ids(doc_ids)
        if chunks:
            logger.info("verify: fetched %d chunks for document_ids=%s", len(chunks), doc_ids)
            return "\n\n".join(chunks)

    if _is_document_content(query, s.verify_pasted_doc_threshold):
        logger.info("verify: treating query (%d chars) as pasted document", len(query))
        return query

    # Fallback: use unified retrieval to find relevant chunks
    result = retrieve(query, scope="point", top_k=5)
    if result.chunks:
        return "\n\n".join(
            f"[{i + 1}] {h.get('content', '')[:s.content_snippet_max_len]}"
            for i, h in enumerate(result.chunks)
        )
    return query


# ── Stage 2: Legal context ───────────────────────────────────────────────────

def _fetch_legal_context(query: str) -> tuple[str, str, str]:
    """Gather applicable norms using unified retrieval + Neo4j graph.

    Uses POINT retrieval (full pipeline: vector + corpus BM25 + Neo4j graph
    → cross-encoder rerank) for legal norms.
    Graph obligations fetched separately.

    Returns:
        (legal_context, graph_context, obl_context) — all formatted strings.
    """
    s = get_settings()

    # 1. Unified retrieval for legal norms (replaces inline vector+BM25+rerank)
    result = retrieve(query, scope="point", top_k=7)

    legal_context = "\n".join(
        f"- {h.get('content', '')[:1000]}" for h in result.chunks
    ) or "нет данных"

    # 2. Neo4j sections (additional structured context)
    graph_parts: list[str] = []
    try:
        sections = graph_section_search.invoke({"keywords": query[:60], "limit": 3})
        for sec in sections:
            text = (sec.get("text") or "")[:500]
            articles = sec.get("articles") or []
            refs = ", ".join(
                f"Статья {a.get('number')}" + (f" «{a.get('title')}»" if a.get("title") else "")
                for a in articles if a.get("number")
            )
            entry = text + (f"\n[{refs}]" if refs else "")
            graph_parts.append(entry)
    except Exception as exc:
        logger.warning("verify: graph section search failed: %s", exc)

    # 3. Neo4j obligations
    obl_parts: list[str] = []
    try:
        obls = graph_obligation_search.invoke({"keywords": query[:60], "limit": 4})
        for obl in obls:
            line = " — ".join(
                p for p in [obl.get("subject"), obl.get("action"), obl.get("object")] if p
            )
            if obl.get("deadline"):
                line += f" (срок: {obl['deadline']})"
            if line:
                obl_parts.append(line)
    except Exception as exc:
        logger.warning("verify: obligation search failed: %s", exc)

    graph_context = "\n\n".join(graph_parts) or "нет данных"
    obl_context = "\n".join(f"- {o}" for o in obl_parts) or "нет данных"
    return legal_context, graph_context, obl_context


# ── Stage 3: LLM compliance analysis ─────────────────────────────────────────

def _run_compliance_llm(
    doc_content: str,
    legal_context: str,
    graph_context: str,
    obl_context: str,
) -> tuple[dict, bool]:
    s = get_settings()
    is_pasted_doc = _is_document_content(doc_content, s.verify_pasted_doc_threshold)
    doc_label = "Содержимое документа" if is_pasted_doc else "Описание ситуации / сценарий для проверки"

    if is_pasted_doc:
        task_instruction = "Проверь данный документ на соответствие нормам из контекста ниже."
    else:
        task_instruction = (
            "Пользователь описал ситуацию или сценарий. Оцени, нарушает ли описанная ситуация "
            "нормы из контекста ниже. Фрагменты законов в разделе 'Применимые нормы' — это "
            "КОНТЕКСТ для оценки, а НЕ объект проверки. Проверяй СИТУАЦИЮ, а не текст норм."
        )

    prompt = (
        f"{task_instruction}\n\n"
        f"{doc_label}:\n{doc_content}\n\n"
        f"Применимые нормы (векторный поиск + реранкинг):\n{legal_context}\n\n"
        f"Релевантные статьи из графа знаний:\n{graph_context}\n\n"
        f"Выявленные обязательства из графа:\n{obl_context}"
    )

    messages = [
        SystemMessage(content=VERIFY_COMPLIANCE),
        HumanMessage(content=prompt),
    ]

    result, success = parse_with_retry(messages, VerifyResult, num_predict=2048)

    if success and result is not None:
        return result.model_dump(), True

    return {
        "compliant": None,
        "risk_score": None,
        "issues": [_PARSE_FAILED_RESPONSE],
        "law_refs": [],
        "fix_hints": [],
        "_parse_failed": True,
    }, False


# ── Stage 4: Result normalisation ────────────────────────────────────────────

_PARSE_FAILED_RESPONSE = (
    "Анализ завершён, однако модель не смогла сформировать структурированный ответ. "
    "Попробуйте переформулировать запрос или уточнить описание ситуации."
)


def _parse_verify_result(result: dict) -> dict:
    if result.get("_parse_failed"):
        result["compliant_label"] = "Не определено"
        result["risk_score"] = None
        return result

    raw_score = result.get("risk_score", 5)
    try:
        score = max(0, min(10, int(raw_score)))
        if raw_score != score:
            logger.warning("verify: risk_score %r clamped to %d", raw_score, score)
        result["risk_score"] = score
    except (TypeError, ValueError):
        logger.warning("verify: invalid risk_score %r — defaulting to 5", raw_score)
        result["risk_score"] = 5
        score = 5

    issues_text = " ".join(result.get("issues", []))
    if result.get("compliant") is True:
        if _NON_COMPLIANT_SIGNALS.search(issues_text):
            logger.warning("verify: overriding compliant=True → False (violation signals)")
            result["compliant"] = False
        elif score >= 7:
            logger.warning("verify: overriding compliant=True → False (risk_score=%d)", score)
            result["compliant"] = False

    compliant = result.get("compliant")
    if compliant is True:
        result["compliant_label"] = "Да"
    elif compliant is False:
        result["compliant_label"] = "Нет"
    else:
        result["compliant_label"] = "Не определено"

    return result


# ── Stage 5: Human-readable summary ──────────────────────────────────────────

def _format_summary(result: dict) -> str:
    compliant_label = result.get("compliant_label", "Не определено")
    risk_score = result.get("risk_score")
    risk_display = f"{risk_score}/10" if risk_score is not None else "—"

    if result.get("compliant") is True:
        compliance_line = f"✅ **Соответствие:** {compliant_label}"
    elif result.get("compliant") is False:
        compliance_line = f"❌ **Соответствие:** {compliant_label}"
    else:
        compliance_line = f"❓ **Соответствие:** {compliant_label}"

    parts = [
        compliance_line,
        f"⚠️ **Риск-оценка:** {risk_display}",
    ]

    issues = result.get("issues", [])
    if issues:
        issues_str = "\n".join(f"- {i}" for i in issues)
        parts.append(f"**Выявленные проблемы:**\n{issues_str}")

    law_refs = result.get("law_refs", [])
    if law_refs:
        refs_str = "\n".join(f"- {r}" for r in law_refs)
        parts.append(f"**Применённые нормы:**\n{refs_str}")

    fix_hints = result.get("fix_hints", [])
    if fix_hints:
        hints_str = "\n".join(f"- {h}" for h in fix_hints)
        parts.append(f"**Рекомендации по устранению:**\n{hints_str}")

    return "\n\n".join(parts)


# ── Graph entry point ────────────────────────────────────────────────────────

def verify_node(state: AgentState) -> AgentState:
    """Compliance check node: fetch doc → gather norms → LLM → risk score.

    Supports DOCUMENT scope: when supervisor detects full-document verification,
    uses Map-Reduce to verify all chunks instead of a 5-hit sample.
    """
    t_start = time.perf_counter()
    raw_query = state["user_query"]
    query = strip_conversational_prefix(raw_query)
    scope = state.get("scope", "point")
    doc_ids = state.get("document_ids") or []
    doc_id = doc_ids[0] if doc_ids else None

    if scope == "document" and doc_id:
        # Map-Reduce verify: fetch legal context first, then map-reduce the document
        legal_context, graph_context, obl_context = _fetch_legal_context(query)
        combined_legal = (
            f"Применимые нормы:\n{legal_context}\n\n"
            f"Статьи из графа:\n{graph_context}\n\n"
            f"Обязательства:\n{obl_context}"
        )
        result = retrieve(
            query, scope="document", doc_id=doc_id,
            task_type="verify", legal_context=combined_legal,
        )

        if result.has_context and result.chunks:
            # Map-Reduce returned a single chunk with the reduce result.
            # REDUCE_VERIFY prompt asks for JSON but LLM may return markdown.
            reduce_text = result.chunks[0].get("content", "")

            # Strategy: try JSON parsing first, then structured LLM call,
            # then use raw text as the analysis result.
            from ..core.utils import safe_parse_json
            parsed = safe_parse_json(reduce_text, {})

            if parsed and "compliant" in parsed:
                # LLM returned valid JSON — use it directly
                verify_result = _parse_verify_result(parsed)
            elif reduce_text.strip():
                # LLM returned markdown/text — run a second pass to structure it
                # via the standard compliance LLM with the reduce output as input
                legal_summary = reduce_text[:3000]
                result_dict, _ok = _run_compliance_llm(
                    legal_summary, legal_context, graph_context, obl_context,
                )
                verify_result = _parse_verify_result(result_dict)
            else:
                verify_result = _parse_verify_result({
                    "compliant": None, "risk_score": None,
                    "issues": ["Map-Reduce вернул пустой результат."],
                    "law_refs": [], "fix_hints": [], "_parse_failed": True,
                })
        else:
            verify_result = _parse_verify_result({
                "compliant": None, "risk_score": None,
                "issues": ["Не удалось проверить документ — фрагменты не найдены."],
                "law_refs": [], "fix_hints": [], "_parse_failed": True,
            })
    else:
        # Standard verify: fetch doc content + legal context + LLM
        doc_content = _fetch_document_content(state, query)
        legal_context, graph_context, obl_context = _fetch_legal_context(query)
        result_dict, _parse_ok = _run_compliance_llm(
            doc_content, legal_context, graph_context, obl_context,
        )
        verify_result = _parse_verify_result(result_dict)

    summary = _format_summary(verify_result)
    final_response = build_final_response(summary, state.get("combined_responses") or [])

    elapsed = time.perf_counter() - t_start
    logger.info(
        "verify_node: scope=%s compliant=%s risk=%s elapsed=%.2fs",
        scope, verify_result["compliant_label"],
        verify_result.get("risk_score", "N/A"), elapsed,
    )

    return {
        **state,
        "verify_result": verify_result,
        "final_response": final_response,
        "retrieval_metrics": {
            "node": "verify",
            "intent": state.get("intent", ""),
            "tier": state.get("tier", ""),
            "scope": scope,
            "doc_content_len": 0,
            "risk_score": verify_result.get("risk_score"),
            "json_parse_success": not verify_result.get("_parse_failed", False),
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
