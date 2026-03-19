"""Supervisor Agent — intent classification (supports multi-intent).

Responsibility: classify the user query into one or two agent intents and
write them to state.  Routing is the graph's responsibility (workflow.py).

Design principles:
- Stateless: no conversation history is passed to the LLM.
  History caused context contamination — previous topics biased classification.
- Three-tier resolution (cheapest → most expensive):
  1. Compound heuristics   — regex for known multi-intent patterns
  2. Keyword override      — high-confidence single-intent patterns, zero cost
  3. Draft LLM fallback    — 7b model for genuinely ambiguous queries only
"""
from __future__ import annotations

import logging
import re
import time
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_draft_llm, invoke_with_retry
from ..core.utils import DOC_REF_RE, strip_conversational_prefix
from ..graph.state import AgentState, Scope
from ..prompts import SUPERVISOR_CLASSIFY

logger = logging.getLogger(__name__)

Intent = Literal["ingest", "search", "verify", "generate", "analyze"]
_VALID_INTENTS = {"ingest", "search", "verify", "generate", "analyze"}

# ── Scope detection ──────────────────────────────────────────────────────────
# Keywords that signal the user wants to process the ENTIRE document
_FULL_DOC_KEYWORDS = re.compile(
    r"\b(весь\s+документ|целиком|полностью|резюмируй|суммаризуй|суммарно|"
    r"краткое\s+содержание|общий\s+анализ|полный\s+анализ|"
    r"проверь\s+весь|проверь\s+целиком|проверить\s+весь|"
    r"извлеки\s+все|извлечь\s+все|перечисли\s+все)\b",
    re.IGNORECASE | re.UNICODE,
)


def _detect_scope(query: str, doc_ids: list[str]) -> Scope:
    """Determine retrieval scope from query + available document IDs.

    Resolution order:
      1. DOCUMENT — doc_ids present AND full-document keywords detected
      2. SCOPED  — doc_ids present OR referential phrase ("этот документ")
      3. POINT   — everything else (general question across collection)
    """
    has_doc = bool(doc_ids)
    has_ref = bool(DOC_REF_RE.search(query))
    wants_full = bool(_FULL_DOC_KEYWORDS.search(query))

    if (has_doc or has_ref) and wants_full:
        return "document"
    if has_doc or has_ref:
        return "scoped"
    return "point"

# ── Compound patterns (multi-intent, checked BEFORE single-intent) ────────────
# Format: (compiled_regex, primary_intent, secondary_intent)
# These patterns detect two co-occurring intents in a single query.
# Checked first so they are not incorrectly reduced to a single intent.
_COMPOUND_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"найд\w{0,4}.{0,50}(проверь|провери|провер\w+)", re.I | re.U), "search", "verify"),
    (re.compile(r"поищ\w{0,4}.{0,50}(проверь|провери|провер\w+)", re.I | re.U), "search", "verify"),
    (re.compile(r"(проверь|провери).{0,50}(составь|создай|сгенер\w+)", re.I | re.U), "verify", "generate"),
    (re.compile(r"(найди|поищи).{0,50}(составь|создай|сгенер\w+)", re.I | re.U), "search", "generate"),
    (re.compile(r"(сравни|проанализ\w+).{0,50}(составь|создай|сгенер\w+)", re.I | re.U), "analyze", "generate"),
    (re.compile(r"(составь|создай).{0,50}(проверь|провери)", re.I | re.U), "generate", "verify"),
    (re.compile(r"найди.{0,5}и.{0,5}провер", re.I | re.U), "search", "verify"),
]

# ── Single-intent keyword patterns (high-confidence, no LLM needed) ──────────
#
# Ordering in _keyword_classify matters:
#   ingest first  — avoids "загрузи договор" → generate
#   analyze next  — avoids enumeration queries falling into search
#   generate next — avoids "составь" → search
#   verify next   — avoids "нарушения" → search
#   search last   — broadest, catches anything clearly informational

_ANALYZE_KW = re.compile(
    r"\b(сравн|сравнен|сопостав|отличи[её]|отличи[яе]|разниц[аеу]|различи[её]|различи[яе]|"
    r"резюм|суммар|краткое\s+содержани|кратк[ое]+\s+излож|суммаризу|"
    r"извлек[ии]|вытащи|вытащите|выдели\s+все|"
    r"перечисли|перечислите|перечисление|проанализируй|"
    r"какие\s+виды|какие\s+типы|какие\s+категории|какие\s+формы|"
    r"что\s+упоминается|что\s+упомянуто|упоминается\s+в|упомянут[оа]?\s+в|"
    r"в\s+документе\s+упомина|в\s+тексте\s+упомина|"
    r"список\s+(?!документ)|что\s+содержится|что\s+есть\s+в|"
    r"какие\s+есть\s+в|перечень\s+(?!документ))\b",
    re.IGNORECASE | re.UNICODE,
)
_GENERATE_KW = re.compile(
    r"\b(составь|составьте|создай|создайте|сгенер|напиши\s+договор|напишите\s+договор|"
    r"подготовь\s+договор|сформируй\s+договор|оформи\s+договор)\b",
    re.IGNORECASE | re.UNICODE,
)
_VERIFY_KW = re.compile(
    r"\b("
    # Explicit compliance checks
    r"проверь\s+на\s+соответств|провери\s+на\s+соответств|"
    r"проверь\s+(?:договор|документ|условия|сделку|контракт)|"
    r"проверить\s+на\s+соответств|"
    # Violation discovery
    r"есть\s+ли\s+нарушени|выяви\s+нарушени|нарушает\s+ли|нарушает\s+закон|"
    r"есть\s+ли\s+(?:риски|нарушения|проблемы|противоречия)|"
    r"какие\s+(?:риски|нарушения|нарушения\s+есть)|"
    # Legality checks
    r"законно\s+ли|незаконн|соответствует\s+ли|соответствует\s+нормам|"
    r"не\s+противоречит\s+ли|допустимо\s+ли|правомерн|"
    r"верно\s+ли|допустим[оа]?\s+ли|имеет\s+ли\s+право|вправе\s+ли|"
    r"позволяет\s+ли\s+закон|можно\s+ли\s+(?!.*(?:получить|оформить|подать))|"
    # Risk analysis
    r"оцени\s+риски|риск[ио]вый\s+анализ|правовые\s+риски"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)
_INGEST_KW = re.compile(
    # Only match queries about uploading/indexing, NOT about document content.
    # "какие документы нужны для ипотеки?" must NOT match — removed какие\s+документ.
    r"\b("
    r"загруз|загрузить|загрузи|загружа|"
    r"добавь\s+(?:документ|файл)|добавить\s+(?:документ|файл)|"
    r"обработай|проиндексируй|прикреп|"
    r"статус\s+обработки|статус\s+документ|"
    r"список\s+загруженн|загруженн[ые]\s+документ|"
    r"какие\s+документы\s+(?:загружены|загружены\s+в|есть\s+в\s+базе|доступны)|"
    r"новый\s+документ\s+загруз|загружён|загруженн"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)
_SEARCH_KW = re.compile(
    r"\b("
    # Informational intent
    r"что\s+такое|что\s+это\s+такое|расскажи\s+про|расскажи\s+о|"
    r"объясни|как\s+работает|как\s+устроен|какие\s+права|какова\s+процедура|"
    r"каков\s+порядок|в\s+чём\s+смысл|что\s+означает|что\s+представляет|"
    # Explicit retrieval — "найди" alone (compound patterns already handled "найди и проверь")
    r"найди|найдите|поищи|поищите|найти\s+информацию|"
    r"покажи\s+информацию|дай\s+информацию|предоставь\s+информацию|"
    # Procedural queries
    r"как\s+(?:получить|оформить|подать|рассчитать|открыть|закрыть)\b|"
    # Domain-specific informational
    r"порядок\s+|условия\s+|требования\s+(?:к|для|по)|ставка\s+|"
    r"обязанности\s+|процедура\s+|страхование\s+"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


def _keyword_classify(query: str) -> str | None:
    """Return a high-confidence single intent from keywords, or None if ambiguous.

    Only ingest and generate are in keyword tier — they are unambiguous actions.
    verify, analyze, search require LLM because they are semantically similar:
    - "Является ли исламский банк...?" looks like verify but is search (factual question)
    - verify ONLY applies when user provides their own document/text to check
    """
    if _INGEST_KW.search(query):
        return "ingest"
    if _GENERATE_KW.search(query):
        return "generate"
    return None


def _detect_intents_from_llm(query: str, llm_raw: str) -> list[str]:
    """Parse LLM output and extract up to 2 valid intents, preserving order."""
    words = re.findall(r"[a-z]+", llm_raw.lower())
    # dict.fromkeys deduplicates while preserving insertion order
    parsed = list(dict.fromkeys(w for w in words if w in _VALID_INTENTS))
    if len(parsed) >= 2:
        return parsed[:2]
    return [parsed[0] if parsed else "search"]


def classify_intent(state: AgentState) -> AgentState:
    """Classify user intent(s) and update state.

    Resolution order (cheapest → most expensive):
    1. Compound patterns  — multi-intent heuristics ("найди и проверь")
    2. Keyword override   — high-confidence single-intent, zero LLM cost
    3. Draft LLM fallback — 7b model, stateless (no history), for ambiguous queries
    """
    t_start = time.perf_counter()
    raw_query = state["user_query"]
    # Strip conversational noise before classification so prefixes like
    # "Скажите пожалуйста, ..." don't confuse compound/keyword matching.
    query = strip_conversational_prefix(raw_query)
    q_lower = query.lower()

    # 0. Detect retrieval scope (runs before intent classification)
    doc_ids: list[str] = state.get("document_ids") or []
    scope: Scope = _detect_scope(query, doc_ids)

    # 1. Compound patterns — checked before single-intent so "найди и проверь"
    #    is not incorrectly collapsed to a single intent.
    for pattern, primary, secondary in _COMPOUND_PATTERNS:
        if pattern.search(q_lower):
            elapsed = time.perf_counter() - t_start
            logger.info(
                "supervisor: tier=compound intent=%s secondary=%s scope=%s query=%r",
                primary, secondary, scope, query[:60],
            )
            return {
                **state,
                "intent": primary,
                "intents": [primary, secondary],
                "tier": "compound",
                "scope": scope,
                "combined_responses": state.get("combined_responses") or [],
                "retrieval_metrics": {
                    "node": "supervisor",
                    "intent": primary,
                    "tier": "compound",
                    "scope": scope,
                    "elapsed_s": round(elapsed, 2),
                },
            }

    # 2. Keyword-based single-intent (no LLM cost)
    kw_intent = _keyword_classify(query)
    if kw_intent:
        elapsed = time.perf_counter() - t_start
        logger.info("supervisor: tier=keyword intent=%s scope=%s query=%r", kw_intent, scope, query[:60])
        return {
            **state,
            "intent": kw_intent,
            "intents": [kw_intent],
            "tier": "keyword",
            "scope": scope,
            "combined_responses": state.get("combined_responses") or [],
            "retrieval_metrics": {
                "node": "supervisor",
                "intent": kw_intent,
                "tier": "keyword",
                "scope": scope,
                "elapsed_s": round(elapsed, 2),
            },
        }

    # 3. Draft LLM fallback — stateless (no history passed).
    #    Using the 7b draft model: classification needs only 1-2 words of output,
    #    not the full reasoning capability of the 14b main model.
    s = get_settings()
    llm = get_draft_llm(temperature=0.0, num_predict=s.supervisor_num_predict)
    raw = invoke_with_retry(llm, [
        SystemMessage(content=SUPERVISOR_CLASSIFY),
        HumanMessage(content=query),
    ]) or "search"

    intents = _detect_intents_from_llm(query, raw)
    intent: Intent = intents[0]  # type: ignore[assignment]
    elapsed = time.perf_counter() - t_start

    logger.info("supervisor: tier=llm intent=%s scope=%s query=%r raw=%r", intent, scope, query[:60], raw[:40])
    return {
        **state,
        "intent": intent,
        "intents": intents,
        "tier": "llm",
        "scope": scope,
        "combined_responses": state.get("combined_responses") or [],
        "retrieval_metrics": {
            "node": "supervisor",
            "intent": intent,
            "tier": "llm",
            "scope": scope,
            "elapsed_s": round(elapsed, 2),
        },
    }
