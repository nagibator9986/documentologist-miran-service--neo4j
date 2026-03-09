"""Supervisor Agent — intent classification and routing (supports multi-intent)."""
from __future__ import annotations

import logging
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_llm, invoke_with_retry
from ..core.utils import build_history_messages
from ..graph.state import AgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Classify the user request into EXACTLY one of these intents (output the single English word only):

search   - find information, answer questions, retrieve documents
           Examples: "что такое", "расскажи про", "найди", "какие права", "как работает", "объясни"
analyze  - compare documents/articles, summarize, extract entities
           Examples: "сравни", "отличие между", "разница", "резюмируй", "извлеки", "перечисли сущности"
verify   - check compliance, find violations, assess legal risk
           Examples: "проверь на соответствие", "есть ли нарушения", "оцени риски"
generate - create a new document (contract, report, letter)
           Examples: "составь договор", "создай отчёт", "напиши письмо", "сгенерируй"
ingest   - upload document, check processing status, manage indexed files
           Examples: "загрузи документ", "статус обработки", "добавь файл", "проиндексируй", "какие документы загружены"

If TWO different operations are needed, output both separated by comma, e.g.: search,verify

Output ONLY the intent word(s), nothing else."""

Intent = Literal["ingest", "search", "verify", "generate", "analyze"]
_VALID_INTENTS = {"ingest", "search", "verify", "generate", "analyze"}

# Compound patterns (checked BEFORE single-intent classification).
# Format: (regex, primary_intent, secondary_intent)
_COMPOUND_PATTERNS: list[tuple[str, str, str]] = [
    (r"найд\w{0,4}.{0,50}(проверь|провери|провер\w+)", "search", "verify"),
    (r"поищ\w{0,4}.{0,50}(проверь|провери|провер\w+)", "search", "verify"),
    (r"(проверь|провери).{0,50}(составь|создай|сгенер\w+)", "verify", "generate"),
    (r"(найди|поищи).{0,50}(составь|создай|сгенер\w+)", "search", "generate"),
    (r"(сравни|проанализ\w+).{0,50}(составь|создай|сгенер\w+)", "analyze", "generate"),
    (r"(составь|создай).{0,50}(проверь|провери)", "generate", "verify"),
    (r"найди.{0,5}и.{0,5}провер", "search", "verify"),
]

# High-confidence keyword routing (avoids LLM call for unambiguous queries).
_ANALYZE_KW = re.compile(
    r"\b(сравн|сравнен|отличи[её]|отличи[яе]|разниц[аеу]|различи[её]|различи[яе]|"
    r"резюм|суммар|краткое\s+содержани|кратк[ое]+\s+излож|"
    r"извлек[ии]|вытащи|вытащите)\b",
    re.IGNORECASE | re.UNICODE,
)
_GENERATE_KW = re.compile(
    r"\b(составь|составьте|создай|создайте|сгенер|напиши\s+договор|напишите\s+договор|"
    r"подготовь\s+договор|сформируй\s+договор|оформи\s+договор)\b",
    re.IGNORECASE | re.UNICODE,
)
_VERIFY_KW = re.compile(
    r"\b(проверь\s+на\s+соответств|провери\s+на\s+соответств|"
    r"есть\s+ли\s+нарушени|выяви\s+нарушени|"
    r"оцени\s+риски|риск[ио]вый\s+анализ)\b",
    re.IGNORECASE | re.UNICODE,
)
_INGEST_KW = re.compile(
    r"\b(загруз|загрузить|добавь|добавить|обработай|проиндексируй|прикреп|"
    r"статус\s+обработки|статус\s+документ|какие\s+документ|список\s+документ|"
    r"новый\s+документ|загруженн)",
    re.IGNORECASE | re.UNICODE,
)
_SEARCH_KW = re.compile(
    r"\b(что\s+такое|что\s+это\s+такое|расскажи\s+про|расскажи\s+о|"
    r"объясни|как\s+работает|какие\s+права|какова\s+процедура|"
    r"каков\s+порядок|в\s+чём\s+смысл|что\s+означает)\b",
    re.IGNORECASE | re.UNICODE,
)


def _keyword_classify(query: str) -> str | None:
    """Return a high-confidence single intent from keywords, or None if ambiguous."""
    q = query
    if _INGEST_KW.search(q):
        return "ingest"
    if _ANALYZE_KW.search(q):
        return "analyze"
    if _GENERATE_KW.search(q):
        return "generate"
    if _VERIFY_KW.search(q):
        return "verify"
    if _SEARCH_KW.search(q):
        return "search"
    return None


def _detect_intents_from_llm(query: str, llm_raw: str) -> list[str]:
    """Parse LLM output and extract up to 2 valid intents, preserving order."""
    words = re.findall(r"[a-z]+", llm_raw.lower())
    # dict.fromkeys deduplicates while preserving insertion order (no extra loop needed)
    parsed = list(dict.fromkeys(w for w in words if w in _VALID_INTENTS))
    if len(parsed) >= 2:
        return parsed[:2]
    return [parsed[0] if parsed else "search"]


def classify_intent(state: AgentState) -> AgentState:
    """Classify user intent(s) and update state.

    Resolution order:
    1. Compound patterns  (multi-intent heuristics, e.g. "найди и проверь")
    2. Keyword override   (high-confidence single-intent patterns)
    3. LLM classification (fallback for ambiguous queries)

    The primary `intent` is always intents[0].
    """
    query = state["user_query"]
    q_lower = query.lower()

    # 1. Check compound patterns first — preserves multi-intent before any single-intent check
    for pattern, primary, secondary in _COMPOUND_PATTERNS:
        if re.search(pattern, q_lower):
            logger.info(
                "Supervisor: compound intent [%s, %s] query=%r",
                primary, secondary, query[:80],
            )
            return {
                **state,
                "intent": primary,
                "intents": [primary, secondary],
                "combined_responses": state.get("combined_responses") or [],
            }

    # 2. Keyword-based single-intent classification (no LLM call needed)
    kw_intent = _keyword_classify(query)
    if kw_intent:
        logger.info("Supervisor: keyword intent=%s query=%r", kw_intent, query[:80])
        return {
            **state,
            "intent": kw_intent,
            "intents": [kw_intent],
            "combined_responses": state.get("combined_responses") or [],
        }

    # 3. LLM fallback for ambiguous queries
    s = get_settings()
    llm = get_llm(temperature=0.0, num_predict=32)
    history = build_history_messages(state, max_turns=s.history_turns)
    raw = invoke_with_retry(llm, [
        SystemMessage(content=_SYSTEM_PROMPT),
        *history,
        HumanMessage(content=query),
    ]) or "search"

    intents = _detect_intents_from_llm(query, raw)
    intent: Intent = intents[0]  # type: ignore[assignment]

    logger.info("Supervisor: llm intent=%s query=%r raw=%r", intent, query[:80], raw[:40])
    return {
        **state,
        "intent": intent,
        "intents": intents,
        "combined_responses": state.get("combined_responses") or [],
    }


def route_intent(state: AgentState) -> str:
    """LangGraph conditional edge — returns the next node name."""
    return state.get("intent", "search")
