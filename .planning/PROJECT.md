# Project: Documentolog Ultra — Production-Ready Multi-Agent RAG

## What We're Building

**Доведение существующей RAG-системы до production-ready состояния.**

Система уже построена и частично работает. Задача — устранить нестабильность, исправить конкретные баги и добавить необходимую инфраструктуру для надёжного развёртывания.

## Domain

Анализ банковских и юридических документов на **русском языке (Казахстан)**.
Пользователи: сотрудники банков, юристы, аналитики.
Чувствительность: высокая — ошибки в правовых вопросах недопустимы.

## Existing Stack

| Компонент | Технология |
|---|---|
| LLM | Ollama: qwen2.5:14b (main) + qwen2.5:7b (draft) |
| Embeddings | bge-m3:latest (через Ollama) |
| Vector DB | Qdrant (hybrid dual-vector collection) |
| Lexical search | BM25 (over Qdrant results) |
| Knowledge graph | Neo4j (sections, articles, obligations, entities, laws) |
| Reranker | cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 |
| Agent framework | LangGraph StateGraph |
| API | FastAPI |
| Chat UI | Chainlit |
| OCR | Surya (GPU, отдельный сервис) |
| Memory | Redis (short-term) + PostgreSQL (long-term) |
| Export | DOCX/PDF → MinIO |
| Observability | MLflow (отключён по умолчанию) |

## Agent Architecture

**Supervisor** (3-tier intent classification):
1. Compound regex — multi-intent ("найди и проверь")
2. Keyword regex — single-intent, zero LLM cost
3. LLM fallback (7b model, stateless, `num_predict=32`)

**5 specialist agents** (LangGraph nodes):
- `search` — hybrid RAG: vector + BM25 + Neo4j + cross-encoder + citations
- `analyze` — task detection (qa/compare/extract/summary) + structured output
- `verify` — compliance check + risk scoring (JSON output)
- `generate` — 3-pass document generation (plan → draft → validate) + DOCX/PDF export
- `ingest` — OCR pipeline orchestration

## Known Problems (Confirmed by Developer)

### P0 — JSON везде ломается
`get_json_llm` с Ollama возвращает нестабильный JSON во всех агентах с структурированным выводом:
- `verify` → `_run_compliance_llm` (VERIFY_COMPLIANCE prompt)
- `generate` → `_plan_document` (GENERATE_PLAN prompt)
- `analyze` → compare/extract tasks

Симптом: `safe_parse_json` падает в fallback, пользователь видит "анализ недоступен".

### P1 — Пустой контекст при существующих документах
Документы есть в Qdrant, но агент возвращает "не найдено".
Вероятные причины:
- Sigmoid-нормализация cross-encoder scores → значения ниже `search_min_confidence: 0.25`
- Порог `min_relevance_score: 0.35` срезает релевантные chunks
- Query expansion (7b LLM) портит запрос вместо улучшения

### P2 — Неверный агент (редко)
Supervisor иногда направляет не туда, особенно на edge-case запросах где LLM fallback не справляется.

## What "Production-Ready" Means Here

1. **JSON надёжность** ≥ 95% (парсится без fallback)
2. **Retrieval recall** — документы которые есть находятся корректно
3. **Оценочная инфраструктура** — eval suite для регрессии
4. **Observability** — MLflow включён, структурированные логи по retrieval
5. **Тесты** — pytest integration tests
6. **Docker** — production-ready compose конфиг

## Constraints

- Локальный деплой (on-premise), интернет недоступен в prod
- Все модели через Ollama — нет облачных API
- Существующий код нельзя сломать — нужно эволюционное улучшение, не переписывание
