# Requirements

## Functional Requirements

### FR-1: JSON Output Reliability
Все агенты с JSON-выводом (verify, generate, analyze/compare, analyze/extract) должны:
- Возвращать валидный парсируемый JSON в ≥ 95% случаев
- При parse failure — возвращать осмысленный fallback ответ (не сырой JSON)
- Валидировать структуру по Pydantic-схеме перед отправкой пользователю

### FR-2: Retrieval — No False Negatives [IN PROGRESS - Phase 3]
Если документ проиндексирован в Qdrant:
- [x] Поисковый агент должен его находить при прямом вопросе по его содержимому
- [x] Пороги `min_relevance_score` и `search_min_confidence` должны быть откалиброваны так, чтобы не срезать релевантные результаты (03-01: 0.35->0.25, 0.25->0.15)
- [x] Query expansion removed -- 7b model was dropping key legal terms (03-01)
- Диагностический endpoint `/api/v1/debug/retrieval` — показывает scores всех retrieval stages

### FR-3: Supervisor Routing Accuracy [COMPLETE - Phase 1]
- [x] Keyword-based routing покрывает ≥ 85% типовых запросов
- [x] LLM fallback покрывает оставшиеся edge cases
- [x] Добавить логирование: какой tier сработал + финальный intent

### FR-4: Evaluation Suite [COMPLETE - Phase 1]
Набор из ≥ 30 тестовых запросов с ожидаемыми outcomes:
- [x] Routing tests — ожидаемый agent для каждого запроса
- [x] Retrieval tests — ожидаемые документы в top-5
- [x] JSON validity tests — структура ответа для verify/generate/analyze
- [ ] Answer quality tests — ответ содержит ключевые факты (не hallucination)

### FR-5: Observability [COMPLETE - Phase 1]
- [x] MLflow включён в docker-compose по умолчанию
- [x] Каждый запрос логирует: intent, retrieved_docs_count, best_rerank_score, json_parse_success, elapsed_s
- [x] Structured logs в JSON format для production

### FR-6: Integration Tests
- `pytest` suite покрывает happy path каждого агента
- Tests запускаются против реальных сервисов (Qdrant, Ollama) или их mock-эквивалентов
- CI-ready: `make test` запускает всё

### FR-7: Production Docker Config
- `docker-compose.prod.yml` с правильными ресурсными лимитами
- Health checks для всех сервисов
- Restart policies
- Volume mounts для данных

## Non-Functional Requirements

### NFR-1: Stability
- Система не падает при LLM timeout (tenacity retry уже есть — нужно покрыть JSON-specific retries)
- Graph enrichment timeout 8s уже настроен — проверить что работает корректно

### NFR-2: Latency
- search/analyze/verify: P95 < 30s (на qwen2.5:14b local)
- generate: P95 < 90s (3 LLM passes)
- Supervisor classification: P95 < 2s

### NFR-3: Privacy
- Все данные on-premise, никакие документы не уходят во внешние API
- MLflow запускается локально

### NFR-4: Maintainability
- Конфиги порогов в `config.py` (Settings) — не хардкод в коде
- Каждый агент имеет документированные tuning parameters

## Out of Scope

- Смена LLM провайдера (остаётся Ollama)
- Переписывание с нуля (эволюционное улучшение)
- Multilingual поддержка (только русский)
- Горизонтальное масштабирование (single-node деплой)
