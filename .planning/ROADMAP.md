# Roadmap — Documentolog Ultra v1.0

## Milestone 1: Production-Ready Agent System

Цель: довести систему от "работает иногда" до стабильного production-ready состояния.

---

## Phase 1: Observability & Eval Infrastructure
**Goal:** Создать измерительную инфраструктуру — невозможно чинить то, что нельзя измерить.
**Plans:** 3/3 plans executed (COMPLETE)

Plans:
- [x] 01-01-PLAN.md — MLflow config, structured logging, tier in AgentState
- [x] 01-02-PLAN.md — Eval dataset (31 queries), debug retrieval endpoint
- [x] 01-03-PLAN.md — Agent metrics enrichment, eval runner script

### Tasks
1. Включить MLflow в `docker-compose.yml` по умолчанию; создать `.env.example` с `MLFLOW_ENABLED=true`
2. Добавить structured JSON logging во все агенты: `intent`, `tier` (compound/keyword/llm), `retrieved_docs`, `best_rerank_score`, `json_parse_success`, `elapsed_s`
3. Создать `tests/eval/` — eval dataset: 30+ запросов × {expected_intent, expected_docs, expected_json_valid}
4. Создать `tests/eval/run_eval.py` — скрипт запускает все запросы и выводит baseline metrics: routing accuracy, retrieval recall@5, JSON validity rate
5. Создать диагностический endpoint `GET /api/v1/debug/retrieval?q=...` — возвращает scores всех retrieval stages без LLM call

**UAT:** `python tests/eval/run_eval.py` выводит baseline без ошибок; MLflow UI показывает traces.

---

## Phase 2: JSON Reliability Fix
**Goal:** JSON parse success ≥ 95% во всех агентах с структурированным выводом.
**Plans:** 4/4 plans complete

Plans:
- [x] 02-01-PLAN.md — Core JSON output layer (Pydantic schemas + parse_with_retry + get_schema_llm)
- [x] 02-02-PLAN.md — Anti-markdown instructions in all 4 JSON prompts
- [ ] 02-03-PLAN.md — Wire verify_agent + generate_agent to parse_with_retry
- [ ] 02-04-PLAN.md — Wire analyze_agent compare/extract to parse_with_retry

### Tasks
1. Создать `app/core/json_output.py` — универсальный слой: Pydantic-схемы для каждого агента + `parse_with_retry(llm, messages, schema, max_retries=3)`
2. Определить Pydantic-схемы: `VerifyResult`, `GeneratePlan`, `AnalyzeCompareResult`, `AnalyzeExtractResult`
3. Переписать `_run_compliance_llm` в `verify_agent.py` — использовать `parse_with_retry` + `VerifyResult`
4. Переписать `_plan_document` в `generate_agent.py` — использовать `parse_with_retry` + `GeneratePlan`
5. Переписать JSON tasks в `analyze_agent.py` (compare, extract) — `parse_with_retry` + соответствующие схемы
6. Улучшить JSON prompts: убрать markdown-обёртки, добавить explicit "return only raw JSON, no backticks" instruction во все SYSTEM prompts для JSON агентов
7. Запустить `tests/eval/run_eval.py` — JSON validity rate должен быть ≥ 95%

**UAT:** Запустить 10 verify + 10 analyze/compare + 5 generate запросов через API — все возвращают структурированный ответ, ни один не падает в "анализ недоступен".

---

## Phase 3: Retrieval Calibration
**Goal:** Документы в базе находятся корректно; убрать false negatives.

### Tasks
1. Аудит cross-encoder нормализации в `app/tools/reranker.py` — проверить что sigmoid применяется правильно, добавить логирование raw scores vs normalized
2. Снизить `search_min_confidence: 0.25 → 0.15` и `min_relevance_score: 0.35 → 0.25` как starting point; задокументировать в Settings комментарии почему выбраны значения
3. Добавить per-stage retrieval logging в `search_agent.py` и `analyze_agent.py`: количество hits и score range на каждом этапе (exact / vector / BM25 / graph / after rerank)
4. Добавить endpoint `POST /api/v1/debug/retrieval` — принимает `{query, collection}`, возвращает все stages без LLM
5. Протестировать 10 "probing queries" через debug endpoint против документов в базе — убедиться что recall@5 ≥ 80%
6. Зафиксировать финальные пороги в `config.py` с обоснованием

**UAT:** Для каждого из 5 тестовых документов в базе — прямой вопрос по их содержимому возвращает этот документ в top-3. Проверить через eval suite.

---

## Phase 4: Agent Logic Hardening
**Goal:** Устранить оставшиеся edge cases в routing и ответах агентов.

### Tasks
1. Расширить keyword patterns в `supervisor.py` — добавить 10+ banking/legal edge cases которые не покрыты сейчас (из eval dataset Phase 1)
2. Добавить логирование tier + intent в supervisor: `logger.info("supervisor: tier=%s intent=%s query=%r", tier, intent, query[:60])`
3. Исправить analyze/compare double-retrieval: `_retrieve_compare_pair` и `_retrieve_and_rerank` сейчас выполняют дублирующие поиски — унифицировать
4. Добавить response quality guard в `search_agent.py`: если `answer` содержит признаки hallucination-заглушки ("у меня нет информации" при `has_context=True`) — повторить с другим prompt
5. Добавить graceful degradation для всех агентов: при timeout Ollama — честный ответ вместо 500 ошибки
6. Проверить `memory_agent.py` — убедиться что Redis failure не ломает весь запрос (должен быть non-fatal)

**UAT:** Eval suite routing accuracy ≥ 90%. Искусственно отключить Redis — система продолжает работать (без памяти). Искусственно вызвать Ollama timeout — ответ 200 с сообщением об ошибке.

---

## Phase 5: Production Hardening
**Goal:** Система готова к деплою: тесты, CI, Docker prod конфиг.

### Tasks
1. Написать `tests/integration/` — pytest suite: happy path для каждого агента (search, analyze qa, verify, generate, ingest)
2. Написать `Makefile` с командами: `make test`, `make eval`, `make lint`, `make docker-up`, `make docker-down`
3. Создать `docker-compose.prod.yml`: ресурсные лимиты (CPU/memory), health checks, restart policies, volume mounts для данных
4. Создать `.env.example` — полный список всех переменных окружения с описанием
5. Добавить `/health` endpoint в FastAPI — проверяет Qdrant, Neo4j, Redis, Ollama connectivity
6. Финальный прогон eval suite — зафиксировать метрики v1.0: routing accuracy, JSON validity, retrieval recall

**UAT:** `make test` проходит все integration tests. `docker-compose -f docker-compose.prod.yml up` поднимает систему. `GET /health` возвращает статус всех зависимостей. Eval suite v1.0 metrics задокументированы.

---

## Success Criteria for Milestone 1

| Метрика | Цель |
|---|---|
| JSON parse success rate | ≥ 95% |
| Supervisor routing accuracy | ≥ 90% |
| Retrieval recall@5 (doc in base) | ≥ 80% |
| System crash on LLM timeout | 0 |
| Integration tests passing | 100% |
| `make test` passes from scratch | ✓ |
