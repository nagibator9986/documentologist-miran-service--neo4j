# Refactoring: SRP, централизованный конфиг и промпты

Ветка: `refactor/srp-config-prompts`
База: `main` (коммит `a39f900`)

---

## Мотивация

Исходный код содержал:
- хардкоды имён моделей, индексов и векторов прямо в tool-файлах
- системные промпты разбросаны по 5 агентам как приватные константы
- дублированные regex-паттерны и хелперы (одно и то же в 2–3 агентах)
- `route_intent` — routing-функция жила в файле агента вместо графа
- монолитный `search_agent.py` (~600 строк в одной функции)
- `ThreadPoolExecutor` создавался при импорте модуля до инициализации конфига

---

## Изменения по файлам

### Новый модуль: `langgraph-agent/app/prompts/__init__.py`

Создан новый модуль, все 8 системных промптов вынесены в одно место.

| Константа | Агент | Описание |
|---|---|---|
| `SUPERVISOR_CLASSIFY` | supervisor | Классификация интентов |
| `SEARCH_EXPERT` | search | Системный промпт ответа |
| `SEARCH_EXPAND_QUERY` | search | Расширение запроса |
| `VERIFY_COMPLIANCE` | verify | Проверка соответствия (JSON) |
| `GENERATE_PLAN` | generate | Планирование документа (JSON) |
| `GENERATE_VALIDATE` | generate | Валидация черновика |
| `ANALYZE_DOCUMENT` | analyze | QA / extract / summary (JSON) |
| `ANALYZE_COMPARE` | analyze | Сравнение документов (JSON) |

**Принцип:** промпт — это конфигурация домена, а не логика агента. Изменить тон, язык или структуру ответа теперь можно в одном файле.

---

### `langgraph-agent/app/core/config.py`

Добавлено 11 настроек, которые раньше были захардкожены в tool/agent-файлах:

```
reranker_model           = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
reranker_fallback_model  = "cross-encoder/ms-marco-MiniLM-L6-v2"
reranker_max_content     = 1024
search_min_confidence    = 0.25
graph_enrichment_timeout = 8.0
supervisor_num_predict   = 32
verify_pasted_doc_threshold = 300
qdrant_named_vector      = "q_vec"
neo4j_fulltext_index     = "sectionText"
pg_pool_min_size         = 1
pg_pool_max_size         = 5
pg_thread_workers        = 2
```

Все значения переопределяются через `.env` без пересборки образа.

---

### `langgraph-agent/app/graph/workflow.py`

**Удалён импорт `route_intent` из `supervisor.py`.**

```python
# Было
from ..agents.supervisor import classify_intent, route_intent
builder.add_conditional_edges("supervisor", route_intent, ...)

# Стало
builder.add_conditional_edges(
    "supervisor",
    lambda state: state.get("intent", "search"),
    {k: k for k in _AGENT_NODES},
)
```

**Принцип LangGraph:** нода пишет в `state`, граф читает из `state` и принимает решение о маршруте. Routing-логика не должна жить в файле агента.

---

### `langgraph-agent/app/agents/supervisor.py`

- Удалена функция `route_intent` (перенесена в граф как lambda)
- Удалена константа `_SYSTEM_PROMPT` → теперь `SUPERVISOR_CLASSIFY` из `prompts`
- Хардкод `num_predict=32` → `s.supervisor_num_predict`
- Добавлен комментарий в docstring: "Routing is the graph's responsibility"

---

### `langgraph-agent/app/agents/search_agent.py`

Главное изменение рефакторинга. Монолит ~600 строк разбит на 12 именованных pipeline-функций:

```
_prepare_query          — обогащение референциальных запросов + вызов _expand_query
_expand_query           — LLM-расширение запроса для семантического поиска
_detect_exact_search    — детект запросов на точное совпадение (кавычки / префиксы)
_retrieve_exact         — Qdrant MatchText поиск
_retrieve_vector_bm25   — векторный поиск + BM25 лексическое ранжирование
_retrieve_graph         — параллельный Neo4j: секции, статьи, обязательства, сущности, законы
_normalize_graph_hits   — конвертация Neo4j-записей в единый формат хитов
_merge_all_hits         — дедуплицированное объединение всех источников
_filter_by_relevance    — отсев хитов ниже порога cosine-score
_rerank_and_calibrate   — cross-encoder reranking + оценка уверенности
_build_llm_prompt       — сборка контекстного промпта для LLM
_generate_answer        — LLM генерация ответа с историей
_build_citations        — форматирование структурированных цитат
search_node             — оркестратор: вызывает стадии, возвращает state
```

`search_node` стал ~50-строчным оркестратором без деталей реализации.

---

### `langgraph-agent/app/agents/verify_agent.py`

Декомпозиция на 5 pipeline-стадий:

```
_fetch_document_content — приоритет: document_ids → длинный текст → Qdrant поиск
_fetch_legal_context    — нормы из Qdrant + Neo4j секции + Neo4j обязательства
_run_compliance_llm     — LLM JSON-анализ на соответствие
_parse_verify_result    — валидация JSON, clamping risk_score [0,10], compliant_label
_format_summary         — human-readable строка результата
verify_node             — оркестратор
```

Использует `VERIFY_COMPLIANCE` из промптов, `s.verify_pasted_doc_threshold` из конфига.

---

### `langgraph-agent/app/agents/generate_agent.py`

Декомпозиция на 6 pipeline-стадий:

```
_retrieve_legal_context — Qdrant поиск шаблонов и применимых норм
_plan_document          — LLM JSON: тип, заголовок, секции, формат экспорта
_generate_draft         — doc_generate tool: разворачивает план в полный текст
_validate_draft         — LLM ревью: добавляет недостающие секции, правит формулировки
_export_document        — DOCX/PDF экспорт + загрузка в MinIO
_minio_upload           — внутренний upload-хелпер (non-fatal)
generate_node           — оркестратор
```

---

### `langgraph-agent/app/agents/analyze_agent.py`

- Удалены дублированные `_DOC_REF_RE`, `_FILENAME_RE`, `_extract_recent_filename`
- Удалены `_SYSTEM`, `_COMPARE_SYSTEM` → теперь из `prompts`
- Декомпозиция на 5 стадий: `_detect_task`, `_enrich_search_query`, `_fetch_context`, `_run_analysis_llm`, `_format_summary`

---

### `langgraph-agent/app/core/utils.py`

Добавлены общие утилиты, которые дублировались в агентах:

```python
DOC_REF_RE          # regex для "этот документ", "в нём" и т.д.
FILENAME_RE         # regex для имён файлов (pdf, docx, doc, txt, json)

extract_recent_filename(state)           # поиск последнего упомянутого файла в истории
build_final_response(answer, combined)   # слияние multi-intent ответов
build_history_messages(state, max_turns) # нарезка истории для LLM-вызова
```

---

### `langgraph-agent/app/tools/reranker.py`

- Удалены `_MODEL_NAME`, `_FALLBACK_MODEL_NAME` → `s.reranker_model`, `s.reranker_fallback_model`
- `_CONTENT_MAX_CHARS` → `get_settings().reranker_max_content`

---

### `langgraph-agent/app/tools/qdrant_search.py`

- Хардкод `"q_vec"` → `s.qdrant_named_vector`
- Лог-сообщения показывают фактическое имя вектора

---

### `langgraph-agent/app/tools/neo4j_query.py`

- Константа `_FULLTEXT_INDEX = "sectionText"` → `get_settings().neo4j_fulltext_index`

---

### `langgraph-agent/app/tools/session_memory.py`

Замена module-level executor на lazy singleton с двойной проверкой:

```python
# Было: создаётся при импорте (до инициализации конфига)
_sync_executor = ThreadPoolExecutor(max_workers=2)

# Стало: double-checked locking lazy init
_sync_executor: ThreadPoolExecutor | None = None
_sync_executor_lock = threading.Lock()

def _get_sync_executor() -> ThreadPoolExecutor:
    global _sync_executor
    if _sync_executor is None:
        with _sync_executor_lock:
            if _sync_executor is None:
                s = get_settings()
                _sync_executor = ThreadPoolExecutor(
                    max_workers=s.pg_thread_workers,
                    thread_name_prefix="pg-sync",
                )
    return _sync_executor
```

asyncpg pool использует `s.pg_pool_min_size` и `s.pg_pool_max_size`.

---

## Применённые паттерны

| Паттерн | Где |
|---|---|
| Separation of Configuration | `config.py` + `.env` |
| Separation of Concerns (промпты) | `prompts/__init__.py` |
| Pipeline / Chain of Responsibility | все агенты |
| DRY — убраны дубли | `core/utils.py` |
| Graph owns routing | `workflow.py` lambda |
| Double-Checked Locking Singleton | `session_memory.py` |
| Single Responsibility (функции) | `search_agent.py` и др. |
| Externalize Configuration | все tool-файлы |

---

## Известные точки для дальнейшего улучшения

1. `_sigmoid` дублирован: `reranker.py` и `core/utils.py:sigmoid_score` — одна и та же функция
2. `verify_agent.py` импортирует `qdrant_client.models` прямо в агенте — слоёвое нарушение
3. `core/utils.py` перегружен: DB-клиенты стоит вынести в `core/db.py`
4. `llm.py`: `_ollama_retry` определена но нигде не используется (мёртвый код)
5. `chat.py`: `_build_initial_state` должен быть синхронизирован с `AgentState` — при добавлении поля нужно обновить оба места
6. Отсутствие поддержки казахского языка в keyword-паттернах supervisor и graph-routing
