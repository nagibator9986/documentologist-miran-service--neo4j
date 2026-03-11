"""Unit tests for analyze_agent._detect_task pre-classification.

Run:
    cd langgraph-agent
    python -m pytest tests/test_analyze_classify.py -v
"""
import pytest

from app.agents.analyze_agent import _detect_task


# ── compare ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "сравни два договора",
    "сравните эти документы",
    "сравнение условий кредита",
    "в чём отличие договора А от Б",
    "отличия между документами",
    "какая разница между пунктами",
    "найди различия в условиях",
])
def test_compare(query):
    assert _detect_task(query) == "compare", f"FAIL: '{query}'"


# ── extract ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "извлеки все суммы из договора",
    "вытащи реквизиты",
    "выдели ключевые пункты",
    "найди все даты",
    "укажи все обязательства",
    "перечисли стороны договора",
])
def test_extract(query):
    assert _detect_task(query) == "extract", f"FAIL: '{query}'"


# ── summary ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "резюмируй документ",
    "дай краткое резюме",
    "суммарно опиши договор",
    "суммаризуй содержание",
    "кратко о чём этот документ",
    "краткое содержание",
])
def test_summary(query):
    assert _detect_task(query) == "summary", f"FAIL: '{query}'"


# ── qa fallback (default) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "что такое овердрафт",
    "каков срок действия договора",
    "объясни пункт 3.2",
    "compare this document",          # английский — не матчит
    "салыстыр екі құжатты",           # казахский — не матчит
    "",                               # пустой запрос
])
def test_qa_default(query):
    assert _detect_task(query) == "qa", f"FAIL: '{query}'"


# ── регистр ───────────────────────────────────────────────────────────────────

def test_case_insensitive():
    assert _detect_task("СРАВНИ документы") == "compare"
    assert _detect_task("РЕЗЮМИРУЙ текст") == "summary"
    assert _detect_task("ИЗВЛЕКИ данные") == "extract"


# ── приоритет: compare > extract > summary ────────────────────────────────────

def test_priority_compare_over_extract():
    # "сравни и извлеки" → compare wins (первый в цепочке)
    assert _detect_task("сравни и извлеки все пункты") == "compare"
