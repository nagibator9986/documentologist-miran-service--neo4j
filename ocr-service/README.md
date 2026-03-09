# Документолог — Система автоматического распознавания документов

Сервис для пакетной загрузки PDF и изображений, автоматического OCR-распознавания через Surya, семантического чанкинга для RAG-пайплайнов и хранения структурированных результатов.

---

## Содержание

1. [Обзор](#обзор)
2. [Архитектура](#архитектура)
3. [Технологический стек](#технологический-стек)
4. [Структура проекта](#структура-проекта)
5. [Жизненный цикл документа](#жизненный-цикл-документа)
6. [API — эндпоинты](#api--эндпоинты)
7. [Chunker API — эндпоинты](#chunker-api--эндпоинты)
8. [Семантический чанкинг — как работает](#семантический-чанкинг--как-работает)
9. [Prometheus метрики](#prometheus-метрики)
10. [Схема базы данных](#схема-базы-данных)
11. [Конфигурация .env](#конфигурация-env)
12. [Быстрый старт через Docker](#быстрый-старт-через-docker)
13. [Локальная разработка](#локальная-разработка)
14. [Миграции базы данных](#миграции-базы-данных)
15. [Фронтенд Streamlit](#фронтенд-streamlit)
16. [Частые проблемы](#частые-проблемы)
17. [Известные ограничения](#известные-ограничения)

---

## Обзор

**Документолог** — это микросервисная система, которая:

- Принимает PDF и изображения (PNG, JPG, JPEG, TIFF, BMP, WEBP) через REST API или веб-интерфейс
- Дедуплицирует файлы по SHA-256 хешу — повторная загрузка одного файла не создаёт новую задачу
- Запускает OCR в фоновом воркере через движок Surya (DetectionPredictor + RecognitionPredictor)
- Хранит исходные файлы и JSON-результаты в MinIO (S3-совместимое хранилище)
- **Автоматически чанкует** завершённые документы для загрузки в RAG-базу (векторный поиск)
- Предоставляет статус и результат через REST API и Streamlit-интерфейс
- **Экспортирует Prometheus-метрики** для мониторинга в production

---

## Архитектура

```
 Браузер / API-клиент
        │
        ▼
  ┌─────────────┐    HTTP :8000
  │  FastAPI    │◄──────────────────────┐
  │   (api)     │                       │
  └──────┬──────┘                       │
         │                    ┌─────────────────┐
         │ PostgreSQL         │  Streamlit UI   │
         │ (запись/чтение)    │  :8501          │
         │                    └─────────────────┘
         ▼
  ┌─────────────┐
  │ PostgreSQL  │◄───────────────────────────────┐
  │   :5433     │                                │
  └──────┬──────┘                                │
         │ PENDING docs                          │
         ▼                                       │
  ┌─────────────────────────────────────┐        │
  │  Worker (polling loop, 2s)          │        │
  │                                     │        │
  │  recover_stuck_documents()          │        │
  │  pre_warm_ocr() — модели в RAM      │        │
  │  poll_and_process()                 │        │
  │    └─► document_processing_pipeline │        │
  │          ├─ task_initialize         │        │
  │          ├─ task_ocr_analysis       │        │
  │          │    └─► Surya OCR         │        │
  │          ├─ task_data_structuring   │        │
  │          └─ task_save_and_update ───┼────────┘
  └──────────────────┬──────────────────┘
                     │ MinIO PUT
                     ▼
              ┌─────────────┐
              │    MinIO    │
              │ :9000/:9001 │
              │ source-files│
              │ analysis-   │
              │  results    │
              └──────┬──────┘
                     │ OCR JSON (status=completed)
                     ▼
  ┌──────────────────────────────────────┐
  │  Chunker Service (polling, 5s)       │
  │  :8100                               │
  │                                      │
  │  Находит completed-документы         │
  │  без чанков → скачивает OCR JSON     │
  │  → семантический чанкинг             │
  │  → сохраняет в document_chunks       │
  └──────────────────────────────────────┘
         │ HTTP :8100
         ▼
  ┌─────────────────┐
  │   RAG-сервис    │  GET /chunks/{doc_id}
  │  (внешний)      │  → векторная БД
  └─────────────────┘

  ┌─────────────┐     ┌─────────────────┐
  │   Prefect   │     │   Prometheus /  │
  │ Server :4200│     │   Grafana       │
  └─────────────┘     │  GET /metrics   │
                      └─────────────────┘
```

### Ключевые архитектурные решения

| Решение | Обоснование |
|---|---|
| Polling-воркер вместо `prefect serve()` | `prefect serve()` запускает новый subprocess на каждый документ — перезагрузка модели Surya (~1.5 GB) каждый раз, задержка 5–42 с и OOM. Polling держит модель в памяти постоянно. |
| Синхронный DB в воркере | Prefect tasks работают вне event loop. Async SQLAlchemy там не работает — отдельный sync engine. |
| SHA-256 дедупликация | Уникальный хеш на уровне приложения + UNIQUE constraint в DB + обработка IntegrityError при race condition. |
| Компенсирующие транзакции | MinIO upload → commit DB. Если commit упал, файл из MinIO удаляется. |
| JIT-прогрев при старте | Первый инференс PyTorch на dummy-изображении при старте воркера — реальные документы обрабатываются с полной скоростью с первого запроса. |
| Cursor-based pagination | Keyset-пагинация по `(created_at DESC, id DESC)` — O(log N) вместо O(N) OFFSET. |
| Chunker как отдельный сервис | Чанкинг не блокирует OCR-воркер. Сервис масштабируется независимо и предоставляет REST API для RAG-пайплайна. |
| Перекрытие чанков (overlap) | Каждый чанк содержит ~200 символов из конца предыдущего — контекст не теряется на границах при семантическом поиске. |

---

## Технологический стек

| Компонент | Технология | Версия |
|---|---|---|
| API | FastAPI + Uvicorn | `>=0.110` / `>=0.27` |
| БД | PostgreSQL | 16-alpine |
| ORM | SQLAlchemy async + asyncpg | `>=2.0` |
| Миграции | Alembic | `>=1.13` |
| Хранилище | MinIO (S3-compatible) | latest |
| Очередь задач | Prefect | `>=3.0` |
| OCR движок | Surya-OCR + PyTorch CPU | `0.16.0` / `>=2.2.0` |
| PDF → изображения | pdf2image + PyMuPDF + Pillow | latest |
| Семантический чанкинг | sentence-transformers (multilingual) | `paraphrase-multilingual-MiniLM-L12-v2` |
| Метрики | prometheus-client | `>=0.20` |
| Фронтенд | Streamlit | latest |
| Конфигурация | Pydantic Settings | `>=2.1` |
| Логирование | Loguru | `>=0.7` |
| Контейнеризация | Docker Compose | `3.9` |

---

## Структура проекта

```
documentolog/
│
├── app/                            # FastAPI приложение
│   ├── main.py                     # Инициализация app, lifespan, Prometheus middleware, /metrics
│   ├── api/
│   │   └── routes.py               # Все REST эндпоинты
│   ├── core/
│   │   ├── config.py               # Pydantic Settings (env-переменные)
│   │   └── database.py             # Async/sync движки SQLAlchemy, сессии
│   ├── models/
│   │   └── document.py             # ORM-модель Document, enum DocumentStatus
│   ├── schemas/
│   │   └── document.py             # Pydantic-схемы запросов/ответов
│   └── services/
│       ├── documents.py            # DocumentService (CRUD, дедупликация, cursor pagination)
│       ├── storage.py              # MinIOService (upload/download/delete)
│       ├── ocr.py                  # SuryaOCRService (постраничная обработка)
│       ├── hasher.py               # SHA-256 (chunk-based, не грузит файл в RAM)
│       └── metrics.py              # Prometheus метрики (счётчики, гистограммы, gauge)
│
├── workers/
│   ├── main.py                     # Точка входа воркера (recovery + warmup + polling)
│   └── pipeline.py                 # Prefect flow + 4 tasks (все с timeout_seconds)
│
├── chunker/                        # Сервис семантического чанкинга
│   ├── __init__.py
│   ├── main.py                     # FastAPI + фоновый polling-поток
│   ├── models.py                   # ORM-модель DocumentChunk
│   └── splitter.py                 # Алгоритм: предложения → эмбеддинги → cosine sim → чанки
│
├── migrations/
│   ├── env.py                      # Alembic env (async engine)
│   ├── script.py.mako
│   └── versions/
│       ├── 001_initial.py          # Создание таблицы documents
│       ├── 002_file_hash_unique.py # UNIQUE constraint на file_hash
│       ├── 003_document_chunks.py  # Таблица document_chunks + индексы
│       └── 004_status_index.py     # INDEX на documents.status (ускорение polling)
│
├── requirements/
│   ├── base.txt                    # Общие зависимости (DB, MinIO, Prefect, Pydantic)
│   ├── api.txt                     # FastAPI + Uvicorn + prometheus-client + base
│   ├── worker.txt                  # Surya OCR + PyTorch + PDF libs + base
│   ├── chunker.txt                 # FastAPI + sentence-transformers + base (без PyTorch)
│   └── dev.txt                     # Инструменты разработки
│
├── tests/
│   ├── test_api_upload.py          # Unit-тесты эндпоинтов загрузки
│   └── test_core.py                # Unit-тесты утилит (хеш, пути MinIO)
│
├── frontend_app.py                 # Streamlit UI (cursor-based пагинация)
│
├── Dockerfile.api                  # Image для FastAPI
├── Dockerfile.worker               # Image для воркера (Surya + PyTorch)
├── Dockerfile.frontend             # Image для Streamlit
├── Dockerfile.chunker              # Image для чанкера (без PyTorch, лёгкий)
├── docker-compose.yml              # Prod-оркестрация
├── docker-compose.dev.yml          # Dev-оверрайд (CPU режим)
├── alembic.ini                     # Конфигурация Alembic
└── .env                            # Переменные окружения (не коммитится)
```

---

## Жизненный цикл документа

```
Загрузка файла (POST /upload или /upload/bulk)
        │
        ▼
  Валидация размера (max 50 MB по умолчанию)
        │
        ▼
  MIME-валидация (magic bytes + расширение файла)
        │
        ▼
  SHA-256 хеш файла
        │
        ├─── Хеш найден в БД? ──YES──► Вернуть is_duplicate=true, doc уже существует
        │
        NO
        │
        ▼
  Создать запись в БД (status=pending)
        │
        ▼
  Загрузить файл в MinIO (bucket: source-files)
        │
        ▼
  Commit транзакции (если упал — откатить и удалить файл из MinIO)
        │
        ▼
  [Ответ API] doc_id, status=pending, is_duplicate=false
        │
        ▼  (через ~2 секунды — polling-воркер)
  ┌──────────────────────────────────────┐
  │     Prefect Pipeline                 │
  │                                      │
  │  task_initialize    (retries=2,      │
  │                      timeout=30s)    │
  │    └► status → processing            │
  │    └► MinIO health check             │
  │                                      │
  │  task_ocr_analysis  (retries=1,      │
  │                      timeout=300s)   │
  │    └► скачать файл из MinIO          │
  │    └► concurrency slot (настраивается│
  │       через OCR_CONCURRENCY_SLOT)    │
  │    └► Surya: DetectionPredictor      │
  │    └► Surya: RecognitionPredictor    │
  │    └► постраничная обработка + GC    │
  │                                      │
  │  task_data_structuring (timeout=30s) │
  │    └► добавить metadata              │
  │        (doc_id, processed_at,        │
  │         page_count)                  │
  │                                      │
  │  task_save_and_update  (retries=2,   │
  │                          timeout=60s)│
  │    └► JSON → MinIO (analysis-results)│
  │    └► status → completed             │
  │    └► result_path, page_count в БД   │
  └──────────────────────────────────────┘
        │
        ▼  (через ~5 секунд — chunker polling)
  ┌──────────────────────────────────────┐
  │     Chunker Service                  │
  │                                      │
  │  Находит completed без чанков        │
  │    └► скачивает OCR JSON из MinIO    │
  │    └► семантический чанкинг          │
  │       (sentence-transformers)        │
  │       или структурный fallback       │
  │    └► сохраняет в document_chunks    │
  └──────────────────────────────────────┘
        │
        ▼
  GET /chunks/{doc_id}  →  чанки готовы к загрузке в RAG
```

**Статусы документа:**

| Статус | Значение |
|---|---|
| `pending` | Загружен, ожидает воркера |
| `processing` | Воркер обрабатывает прямо сейчас |
| `completed` | OCR завершён, результат доступен |
| `failed` | Ошибка на любом этапе, смотри `error_message` |

**Что происходит при перезапуске воркера:**
При старте `workers/main.py` автоматически сбрасывает все документы со статусом `processing` в `failed` с сообщением "Обработка прервана: воркер был перезапущен".

---

## API — эндпоинты

Базовый URL: `http://localhost:8000/api/v1`

Интерактивная документация: `http://localhost:8000/docs` (Swagger UI)

---

### POST `/upload` — загрузка одного файла

**Request:** `multipart/form-data`, поле `file`

**Response 201 — новый файл:**
```json
{
  "doc_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "is_duplicate": false,
  "message": "Файл загружен. Обработка запущена."
}
```

**Response 200 — дубликат:**
```json
{
  "doc_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "is_duplicate": true,
  "message": "Файл уже был загружен ранее. Возвращён существующий результат."
}
```

**Пример:**
```bash
curl -X POST http://localhost:8000/api/v1/upload \
  -F "file=@/path/to/document.pdf"
```

---

### POST `/upload/bulk` — пакетная загрузка

**Request:** `multipart/form-data`, поле `files` (несколько файлов)

**Response 207 Multi-Status:**
```json
{
  "successful": [
    {
      "doc_id": "550e8400-...",
      "status": "pending",
      "is_duplicate": false,
      "message": "Файл загружен. Обработка запущена."
    }
  ],
  "failed": [
    {
      "filename": "bad.exe",
      "error": "Неподдерживаемый тип файла: '.exe'."
    }
  ]
}
```

**Пример:**
```bash
curl -X POST http://localhost:8000/api/v1/upload/bulk \
  -F "files=@/path/to/doc1.pdf" \
  -F "files=@/path/to/doc2.png"
```

---

### GET `/status/{doc_id}` — статус документа

**Response 200:**
```json
{
  "doc_id": "550e8400-...",
  "status": "completed",
  "filename": "contract.pdf",
  "result_path": "analysis-results/abc123/surya_output.json",
  "error_message": null
}
```

**Пример:**
```bash
curl http://localhost:8000/api/v1/status/550e8400-e29b-41d4-a716-446655440000
```

---

### GET `/result/{doc_id}` — OCR-результат

Возвращает JSON из MinIO. Доступен только если `status=completed`.

**Response 200:**
```json
{
  "doc_id": "550e8400-...",
  "page_count": 3,
  "processed_at": "2024-01-15T10:30:00Z",
  "full_text": "Полный распознанный текст всех страниц...",
  "pages": [
    {
      "page_number": 1,
      "full_text": "Текст первой страницы...",
      "blocks": [
        {
          "type": "text",
          "text": "Строка текста",
          "confidence": 0.97,
          "bbox": [10, 20, 500, 40]
        }
      ]
    }
  ]
}
```

---

### GET `/documents` — реестр документов (cursor-based пагинация)

Использует keyset-пагинацию по `(created_at DESC, id DESC)` — не замедляется при росте базы.

**Query-параметры:**

| Параметр | Тип | По умолчанию | Описание |
|---|---|---|---|
| `cursor` | string | — | Токен следующей страницы (из поля `next_cursor` предыдущего ответа) |
| `limit` | int | `50` | Размер страницы (max 200) |
| `latest_only` | bool | `true` | Только последние версии файлов |
| `status` | string | — | Фильтр: `pending` / `processing` / `completed` / `failed` |
| `filename` | string | — | Поиск по названию (подстрока, регистронезависимо) |
| `created_after` | datetime | — | Дата создания от (ISO 8601) |
| `created_before` | datetime | — | Дата создания до (ISO 8601) |

**Response 200:**
```json
{
  "total": 142,
  "next_cursor": "eyJ0cyI6ICIyMDI0LTAxLTE1VDEwOjAwOjAwKzAwOjAwIiwgImlkIjogIjU1MGU4NDAwLSJ9",
  "documents": [
    {
      "id": "550e8400-...",
      "file_hash": "abc123...",
      "filename": "contract.pdf",
      "status": "completed",
      "is_latest": true,
      "s3_path": "source-files/abc123/contract.pdf",
      "result_path": "analysis-results/abc123/surya_output.json",
      "error_message": null,
      "page_count": 5,
      "created_at": "2024-01-15T10:00:00Z",
      "updated_at": "2024-01-15T10:02:30Z"
    }
  ]
}
```

**Как листать страницы:**
```bash
# Первая страница
curl "http://localhost:8000/api/v1/documents?limit=20"

# Следующая страница (next_cursor из предыдущего ответа)
curl "http://localhost:8000/api/v1/documents?limit=20&cursor=eyJ0cy..."

# С фильтрами
curl "http://localhost:8000/api/v1/documents?limit=20&status=completed&filename=contract"
```

> `next_cursor` равен `null`, если текущая страница — последняя.
> `total` всегда содержит общее число документов по заданным фильтрам.

---

### GET `/health` — состояние сервиса

```json
{
  "status": "ok",
  "version": "0.1.0",
  "database": "ok",
  "minio": "ok"
}
```

---

## Chunker API — эндпоинты

Базовый URL: `http://localhost:8100`

Интерактивная документация: `http://localhost:8100/docs` (Swagger UI)

Chunker-сервис **автоматически** находит `completed`-документы и чанкует их в фоновом потоке. REST API предназначен для RAG-пайплайна — получение готовых чанков для загрузки в векторную базу.

---

### GET `/health` — состояние чанкера

```bash
curl http://localhost:8100/health
```

```json
{
  "status": "ok",
  "service": "chunker"
}
```

---

### GET `/chunks` — список отчанкованных документов

Возвращает пагинированный список документов, для которых чанки уже готовы.

**Query-параметры:**

| Параметр | Тип | По умолчанию | Описание |
|---|---|---|---|
| `offset` | int | `0` | Смещение |
| `limit` | int | `50` | Размер страницы (max 200) |

**Пример:**
```bash
curl "http://localhost:8100/chunks?offset=0&limit=10"
```

**Response 200:**
```json
{
  "total": 15,
  "offset": 0,
  "limit": 10,
  "documents": [
    {
      "doc_id": "550e8400-e29b-41d4-a716-446655440000",
      "filename": "contract.pdf",
      "chunk_count": 12,
      "total_tokens": 3840,
      "chunked_at": "2024-01-15T10:05:00Z"
    }
  ]
}
```

---

### GET `/chunks/{doc_id}/status` — готовность чанков

Быстрая проверка — готовы ли чанки для документа. Удобно для polling перед запросом полных данных.

**Пример:**
```bash
curl http://localhost:8100/chunks/550e8400-e29b-41d4-a716-446655440000/status
```

**Response 200:**
```json
{
  "doc_id": "550e8400-e29b-41d4-a716-446655440000",
  "ready": true,
  "chunk_count": 12
}
```

---

### GET `/chunks/{doc_id}` — все чанки документа

Основной эндпоинт для RAG-пайплайна. Возвращает все чанки с готовым `metadata`-блоком для загрузки в векторную БД (Qdrant, Weaviate, Chroma и т.д.).

**Пример:**
```bash
curl http://localhost:8100/chunks/550e8400-e29b-41d4-a716-446655440000
```

**Response 200:**
```json
{
  "doc_id": "550e8400-e29b-41d4-a716-446655440000",
  "filename": "contract.pdf",
  "total_chunks": 12,
  "chunks": [
    {
      "chunk_index": 0,
      "total_chunks": 12,
      "text": "Договор аренды нежилого помещения...",
      "token_count": 320,
      "page_numbers": [1, 2],
      "char_start": 0,
      "char_end": 1987,
      "metadata": {
        "doc_id": "550e8400-e29b-41d4-a716-446655440000",
        "filename": "contract.pdf",
        "chunk_index": 0,
        "total_chunks": 12,
        "page_numbers": [1, 2],
        "char_start": 0,
        "char_end": 1987,
        "token_count": 320,
        "chunked_at": "2024-01-15T10:05:00Z"
      }
    }
  ]
}
```

**Коды ошибок:**

| Код | Описание |
|---|---|
| `404` | Документ не найден или чанки ещё не готовы (попробуйте позже) |
| `409` | OCR ещё не завершён (статус не `completed`) |

**Пример интеграции с RAG (Python):**
```python
import requests

chunks_resp = requests.get(
    f"http://localhost:8100/chunks/{doc_id}"
).json()

# Каждый chunk["metadata"] готов к передаче в векторную БД
for chunk in chunks_resp["chunks"]:
    vector_db.upsert(
        id=f"{doc_id}_{chunk['chunk_index']}",
        text=chunk["text"],
        metadata=chunk["metadata"],
    )
```

---

### POST `/chunks/{doc_id}/reprocess` — принудительный перечанкинг

Удаляет существующие чанки и ставит документ в очередь на повторную обработку. Полезно после изменения параметров `CHUNK_MAX_CHARS` / `CHUNK_OVERLAP_CHARS`.

**Пример:**
```bash
curl -X POST http://localhost:8100/chunks/550e8400-e29b-41d4-a716-446655440000/reprocess
```

**Response 202 Accepted:**
```json
{
  "doc_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "message": "Документ поставлен в очередь на повторный чанкинг."
}
```

**Коды ошибок:**

| Код | Описание |
|---|---|
| `404` | Документ не найден |
| `409` | OCR ещё не завершён |

---

## Семантический чанкинг — как работает

Реализован в `chunker/splitter.py`. Алгоритм автоматически выбирает режим в зависимости от доступности библиотеки и объёма текста.

### Режим 1: Семантический (основной)

Используется когда `sentence-transformers` установлен и в документе ≥ 5 предложений.

**Модель:** `paraphrase-multilingual-MiniLM-L12-v2`
- Размер: ~118 MB
- Размерность эмбеддингов: 384
- Поддерживает: русский, казахский, английский и 50+ языков
- Скорость: 1–3 сек на документ на CPU

**Шаги алгоритма:**

```
1. ИЗВЛЕЧЕНИЕ ТЕКСТА
   OCR JSON (pages[].blocks[].text или pages[].full_text)
        │
        ▼
2. РАЗБИЕНИЕ НА ПРЕДЛОЖЕНИЯ
   Regex по .!?؟ + пробел + заглавная буква/цифра
   Поддержка кириллицы, латиницы, казахских букв
   Короткие фрагменты (< 15 символов) объединяются с предыдущим
        │
        ▼
3. СКОЛЬЗЯЩЕЕ ОКНО (WINDOW_SIZE=3)
   Каждые 3 соседних предложения объединяются в один «контекст»
   для вычисления эмбеддинга — сглаживает шум отдельных предложений
        │
        ▼
4. BATCH-ЭМБЕДДИНГИ
   Все окна → SentenceTransformer.encode(batch_size=64)
   normalize_embeddings=True → dot product = cosine similarity
        │
        ▼
5. ПОИСК СЕМАНТИЧЕСКИХ ГРАНИЦ
   Cosine similarity между соседними окнами → distance = 1 - sim
   Порог = BREAKPOINT_PERCENTILE (80-й процентиль) расстояний
   Позиции выше порога = «граница смены темы»
        │
        ▼
6. СБОРКА ЧАНКОВ
   Предложения группируются между границами
   Если группа > CHUNK_MAX_CHARS → дробится по предложениям
   Если группа < CHUNK_MIN_CHARS → сливается с соседом
        │
        ▼
7. ДОБАВЛЕНИЕ ПЕРЕКРЫТИЯ (OVERLAP)
   Последние CHUNK_OVERLAP_CHARS символов предыдущего чанка
   добавляются в начало следующего (выравнивание по границе предложения)
        │
        ▼
8. Chunk объекты: text, page_numbers, char_start, char_end, token_count
```

**Параметры (настраиваются через env):**

| Параметр | Значение | Описание |
|---|---|---|
| `CHUNK_MAX_CHARS` | 2000 | Максимальный размер чанка (~500 токенов для RU/KZ) |
| `CHUNK_OVERLAP_CHARS` | 200 | Символов перекрытия (~50 токенов) |
| `CHUNK_MIN_CHARS` | 100 | Минимальный размер — короткие сливаются с соседом |
| `WINDOW_SIZE` | 3 | Размер скользящего окна для эмбеддингов |
| `BREAKPOINT_PERCENTILE` | 80 | Процентиль для порога границ (выше → меньше чанков) |

---

### Режим 2: Структурный (fallback)

Используется автоматически когда:
- `sentence-transformers` не установлен, или
- документ содержит менее 5 предложений, или
- семантический режим вернул 0 чанков (ошибка эмбеддингов)

**Алгоритм:**
1. Группировка строк в абзацы по вертикальным зазорам bbox (от Surya)
2. Рекурсивное разбиение длинных абзацев по иерархии разделителей: `\n\n` → `\n` → `. ` → `! ` → `? ` → `; ` → `,` → ` `
3. Слияние коротких фрагментов + добавление overlap

---

### Пример вывода чанкера

Для документа из 3 страниц с 4500 символами (≈1125 токенов):

```
Найдено 47 предложений
Найдено 3 семантических границы (80-й перцентиль)
Результат: 4 чанка
  ├── chunk_index=0: 2 страницы, 1987 символов, 497 токенов
  ├── chunk_index=1: 2 страницы, 1843 символов, 461 токен  (первые 200 символов — overlap)
  ├── chunk_index=2: 3 страницы, 1956 символов, 489 токенов (первые 200 символов — overlap)
  └── chunk_index=3: 3 страница,  714 символов, 179 токенов (первые 200 символов — overlap)
```

---

## Prometheus метрики

API экспортирует метрики по стандарту Prometheus на эндпоинте `GET /metrics`.

```bash
curl http://localhost:8000/metrics
```

### Доступные метрики

| Метрика | Тип | Описание | Labels |
|---|---|---|---|
| `documentolog_http_requests_total` | Counter | Количество HTTP-запросов | `method`, `path`, `status_code` |
| `documentolog_http_request_duration_seconds` | Histogram | Задержка HTTP-запросов | `method`, `path` |
| `documentolog_uploads_total` | Counter | Загрузки документов | `is_duplicate` (`true`/`false`) |
| `documentolog_documents_by_status` | Gauge | Текущее число документов по статусу | `status` |

> `documentolog_documents_by_status` обновляется при каждом scrape — всегда актуален без фоновых задач.

### Пример вывода

```
# HELP documentolog_uploads_total Total document upload attempts
# TYPE documentolog_uploads_total counter
documentolog_uploads_total{is_duplicate="false"} 42.0
documentolog_uploads_total{is_duplicate="true"} 7.0

# HELP documentolog_documents_by_status Current number of documents per status
# TYPE documentolog_documents_by_status gauge
documentolog_documents_by_status{status="completed"} 38.0
documentolog_documents_by_status{status="failed"} 2.0
documentolog_documents_by_status{status="pending"} 2.0

# HELP documentolog_http_requests_total Total number of HTTP requests
# TYPE documentolog_http_requests_total counter
documentolog_http_requests_total{method="GET",path="/api/v1/documents",status_code="200"} 15.0
documentolog_http_requests_total{method="POST",path="/api/v1/upload",status_code="201"} 42.0
```

### Настройка Prometheus + Grafana

Добавьте в `docker-compose.yml`:

```yaml
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    networks:
      - docolog-net

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3001:3000"
    networks:
      - docolog-net
```

Создайте `prometheus.yml`:

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: documentolog_api
    static_configs:
      - targets: ['api:8000']
    metrics_path: /metrics
```

Запустите:
```bash
docker compose up -d prometheus grafana
```

- **Prometheus UI:** http://localhost:9090
- **Grafana:** http://localhost:3001 (admin / admin) → Add datasource → Prometheus → `http://prometheus:9090`

---

## Схема базы данных

**Таблица: `documents`**

| Колонка | Тип | Ограничения | Описание |
|---|---|---|---|
| `id` | UUID | PK | Уникальный идентификатор |
| `file_hash` | VARCHAR(64) | UNIQUE, NOT NULL | SHA-256 хеш файла |
| `filename` | VARCHAR(512) | NOT NULL | Оригинальное имя файла |
| `s3_path` | VARCHAR(1024) | nullable | Путь к файлу в MinIO |
| `result_path` | VARCHAR(1024) | nullable | Путь к JSON результату в MinIO |
| `status` | ENUM | NOT NULL | `pending` / `processing` / `completed` / `failed` |
| `is_latest` | BOOLEAN | NOT NULL, default true | Флаг последней версии файла |
| `error_message` | TEXT | nullable | Текст ошибки при `failed` |
| `page_count` | INTEGER | nullable | Число страниц после OCR |
| `created_at` | TIMESTAMPTZ | NOT NULL | Время загрузки |
| `updated_at` | TIMESTAMPTZ | NOT NULL | Время последнего обновления |

**Индексы `documents`:**
- `UNIQUE (file_hash)` — дедупликация на уровне БД
- `INDEX (filename, is_latest)` — быстрая фильтрация по имени файла
- `INDEX (status)` — ускорение polling-запроса воркера (добавлен в migration 004)

---

**Таблица: `document_chunks`**

| Колонка | Тип | Ограничения | Описание |
|---|---|---|---|
| `id` | UUID | PK | Уникальный идентификатор чанка |
| `doc_id` | UUID | FK → documents.id CASCADE | Ссылка на документ |
| `chunk_index` | INTEGER | NOT NULL | Порядковый номер чанка (0-based) |
| `total_chunks` | INTEGER | NOT NULL | Общее число чанков в документе |
| `text` | TEXT | NOT NULL | Текст чанка (с перекрытием) |
| `page_numbers` | INTEGER[] | NOT NULL | Страницы исходного документа в чанке |
| `char_start` | INTEGER | nullable | Позиция начала в конкатенированном тексте |
| `char_end` | INTEGER | nullable | Позиция конца в конкатенированном тексте |
| `token_count` | INTEGER | nullable | Приближённое число токенов (~4 символа = 1 токен) |
| `created_at` | TIMESTAMPTZ | NOT NULL | Время создания чанка |

**Индексы `document_chunks`:**
- `INDEX (doc_id)` — быстрый поиск чанков по документу
- `UNIQUE INDEX (doc_id, chunk_index)` — идемпотентность повторного чанкинга

---

**MinIO бакеты:**

| Бакет | Назначение | Путь к объекту |
|---|---|---|
| `source-files` | Исходные загруженные файлы | `{file_hash}/{filename}` |
| `analysis-results` | JSON с OCR-результатом | `{file_hash}/surya_output.json` |

---

## Конфигурация .env

Создайте файл `.env` в корне проекта:

```env
# ── PostgreSQL ──────────────────────────────────────────────
POSTGRES_USER=docolog
POSTGRES_PASSWORD=docolog_secret
POSTGRES_DB=documentolog

# ── MinIO ───────────────────────────────────────────────────
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin123

# ── OCR ─────────────────────────────────────────────────────
# Максимальное число параллельных OCR-задач (рекомендуется 1 для CPU)
OCR_CONCURRENCY_LIMIT=1

# Имя Prefect-слота для ограничения параллельного OCR
OCR_CONCURRENCY_SLOT=ocr-gpu-slots

# Таймаут OCR-задачи в секундах
OCR_TIMEOUT_SECONDS=300

# Задержка перед повтором OCR-задачи
OCR_RETRY_DELAY_SECONDS=60

# ── Pipeline task timeouts ────────────────────────────────────
OCR_INIT_TIMEOUT_SECONDS=30
OCR_STRUCTURE_TIMEOUT_SECONDS=30
OCR_SAVE_TIMEOUT_SECONDS=60

# ── Настройки приложения ─────────────────────────────────────
# Максимальный размер файла в мегабайтах
MAX_UPLOAD_SIZE_MB=50

# CORS (разрешённые источники)
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

# Уровень логирования: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL=INFO

# ── Chunker ──────────────────────────────────────────────────
# Максимальный размер чанка в символах
CHUNK_MAX_CHARS=2000

# Перекрытие между соседними чанками в символах
CHUNK_OVERLAP_CHARS=200

# Минимальный размер чанка (меньшие объединяются с предыдущим)
CHUNK_MIN_CHARS=100

# Интервал опроса БД чанкером в секундах
CHUNK_POLL_INTERVAL=5
```

**Все переменные и их значения по умолчанию:**

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://docolog:docolog_secret@localhost:5433/documentolog` | Async URL для FastAPI |
| `DATABASE_URL_SYNC` | `postgresql://docolog:docolog_secret@localhost:5433/documentolog` | Sync URL для Alembic и воркера |
| `MINIO_ENDPOINT` | `localhost:9000` | Адрес MinIO |
| `MINIO_ACCESS_KEY` | `minioadmin` | Логин MinIO |
| `MINIO_SECRET_KEY` | `minioadmin123` | Пароль MinIO |
| `MINIO_SECURE` | `false` | HTTPS для MinIO |
| `PREFECT_API_URL` | `http://localhost:4200/api` | URL Prefect Server |
| `OCR_CONCURRENCY_LIMIT` | `1` | Лимит параллельных OCR |
| `OCR_CONCURRENCY_SLOT` | `ocr-gpu-slots` | Имя Prefect-слота для OCR |
| `OCR_TIMEOUT_SECONDS` | `300` | Таймаут OCR-задачи |
| `OCR_RETRY_DELAY_SECONDS` | `60` | Задержка перед повтором OCR |
| `OCR_INIT_TIMEOUT_SECONDS` | `30` | Таймаут задачи инициализации |
| `OCR_STRUCTURE_TIMEOUT_SECONDS` | `30` | Таймаут задачи структурирования |
| `OCR_SAVE_TIMEOUT_SECONDS` | `60` | Таймаут задачи сохранения |
| `MAX_UPLOAD_SIZE_MB` | `50` | Максимальный размер файла |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `CORS_ORIGINS` | `http://localhost:3000,...` | Разрешённые CORS-источники |
| `CHUNK_MAX_CHARS` | `2000` | Максимальный размер чанка (символы) |
| `CHUNK_OVERLAP_CHARS` | `200` | Перекрытие между чанками (символы) |
| `CHUNK_MIN_CHARS` | `100` | Минимальный размер чанка (символы) |
| `CHUNK_POLL_INTERVAL` | `5` | Интервал опроса чанкера (секунды) |

> **Внимание:** Не коммитьте `.env` с реальными учётными данными. Добавьте `.env` в `.gitignore`.

---

## Быстрый старт через Docker

### Требования

- Docker Desktop >= 24
- Docker Compose >= 2.20
- Свободная RAM: минимум 6 GB (Surya-модели ~1.5 GB + sentence-transformers ~500 MB + OS + остальные сервисы)

### Запуск

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd documentolog

# 2. Создать .env файл
cp .env.example .env
# Отредактируйте .env при необходимости

# 3. Запустить все сервисы
docker compose up -d --build

# 4. Проверить статус
docker compose ps
curl http://localhost:8000/health
curl http://localhost:8100/health
curl http://localhost:8000/metrics
```

### Доступные интерфейсы после запуска

| Сервис | URL | Описание |
|---|---|---|
| Streamlit UI | http://localhost:8501 | Веб-интерфейс пользователя |
| FastAPI Swagger | http://localhost:8000/docs | Интерактивная документация основного API |
| FastAPI ReDoc | http://localhost:8000/redoc | Альтернативная документация |
| Chunker Swagger | http://localhost:8100/docs | Интерактивная документация Chunker API |
| Prometheus метрики | http://localhost:8000/metrics | Scrape-эндпоинт для Prometheus |
| MinIO Console | http://localhost:9001 | Управление хранилищем (minioadmin / minioadmin123) |
| Prefect UI | http://localhost:4200 | Мониторинг задач OCR |
| PostgreSQL | localhost:5433 | БД (docolog / docolog_secret) |

### Перезапуск после изменения кода

Большинство сервисов используют volume-mount — код применяется без пересборки:

```bash
# Изменились зависимости (requirements/*.txt) → rebuild нужен
docker compose up -d --build api

# Изменился только Python-код (volume-mount) → просто restart
docker compose restart worker
docker compose restart frontend
docker compose restart chunker
```

### CPU-only режим (разработка)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

### Остановка

```bash
# Остановить сервисы (данные сохраняются)
docker compose down

# Остановить и удалить ВСЕ данные (необратимо)
docker compose down -v
```

### Логи

```bash
# Все сервисы
docker compose logs -f

# Конкретный сервис
docker compose logs -f worker
docker compose logs -f api
docker compose logs -f chunker
docker compose logs -f frontend
```

---

## Локальная разработка

### Требования

- Python 3.11
- Docker (для инфраструктуры: PostgreSQL, MinIO, Prefect)

### Установка

```bash
# 1. Создать виртуальное окружение
python3.11 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. Зависимости API (без Surya/PyTorch — быстро)
pip install -r requirements/api.txt

# 3. Зависимости воркера (Surya + PyTorch CPU — долго, ~3 GB)
pip install -r requirements/worker.txt

# 4. Зависимости чанкера (лёгкие, без PyTorch)
pip install -r requirements/chunker.txt

# 5. Запустить только инфраструктуру
docker compose up -d postgres minio minio-init prefect-server

# 6. Применить миграции
alembic upgrade head

# 7. API (терминал 1)
uvicorn app.main:app --reload --port 8000

# 8. Воркер (терминал 2)
python -m workers.main

# 9. Чанкер (терминал 3)
uvicorn chunker.main:app --reload --port 8100

# 10. Фронтенд (терминал 4)
streamlit run frontend_app.py
```

### Запуск тестов

```bash
pip install -r requirements/dev.txt
pytest tests/ -v
```

---

## Миграции базы данных

```bash
# Применить все миграции
alembic upgrade head

# Откатить последнюю миграцию
alembic downgrade -1

# Текущая версия схемы
alembic current

# История миграций
alembic history

# Создать новую миграцию после изменения модели
alembic revision --autogenerate -m "описание изменений"
```

**Список миграций:**

| Файл | Описание |
|---|---|
| `001_initial.py` | Создание таблицы `documents`, enum `documentstatus` |
| `002_file_hash_unique.py` | UNIQUE constraint на `file_hash` |
| `003_document_chunks.py` | Таблица `document_chunks`, индексы по `doc_id` и `(doc_id, chunk_index)` |
| `004_status_index.py` | INDEX на `documents.status` — ускорение polling-воркера (O(log N) вместо full scan) |

> Миграции применяются автоматически при старте `api`-контейнера и `chunker`-контейнера командой `alembic upgrade head`.

---

## Фронтенд Streamlit

Приложение `frontend_app.py` состоит из трёх страниц, навигация через левое меню.

### Страница "Загрузка файлов"

- Загрузка одного или нескольких файлов через drag-and-drop
- После отправки — карточка по каждому файлу:
  - `✅ Загружен` — новый файл принят в обработку
  - `⚠️ Дубликат` — файл уже есть в системе, возвращён существующий результат
  - `❌ Ошибка` — неверный формат, слишком большой файл и т.д.
- Кнопка `Открыть →` — переход на детальную страницу конкретного документа

### Страница "Реестр документов"

- Таблица всех документов с cursor-based пагинацией (10 / 20 / 50 / 100 записей на страницу)
- Навигация: ⏪ Первая / ◀ Предыдущая / ▶ Следующая (cursor-based, не замедляется при росте базы)
- Фильтры: поиск по названию, статус, диапазон дат
- KPI-блок: всего в базе, готово, в обработке, ошибки
- **Клик на строку таблицы** → автоматический переход на страницу детальной информации

### Страница "Детальная информация"

- Карточка: ID документа, имя файла, статус (цветной), путь к результату OCR
- Сообщение об ошибке (если `status=failed`)
- Кнопка `🔄 Обновить статус` — перезагружает данные
- Кнопка `📄 Показать OCR текст` (только для `completed`):
  - Вкладка **Текст** — распознанный текст
  - Вкладка **Raw JSON** — полный JSON ответ

---

## Частые проблемы

### `curl: (7) Failed to connect to localhost port 8000`

API не запущен или в рестарте:
```bash
docker compose ps api
docker compose logs --tail=100 api
```

### Документ навсегда застрял в `processing`

Воркер упал во время обработки. При следующем старте воркера такие документы автоматически переходят в `failed`. Для ручного сброса:
```bash
docker compose restart worker
```

### Чанки не появляются для `completed`-документа

Chunker-сервис опрашивает БД каждые `CHUNK_POLL_INTERVAL` секунд. Подождите несколько секунд. Если чанков нет — проверьте логи:
```bash
docker compose logs --tail=100 chunker
```

Для принудительного перечанкинга:
```bash
curl -X POST http://localhost:8100/chunks/<doc_id>/reprocess
```

### Метрики `/metrics` недоступны

Убедитесь, что контейнер пересобран с новым `prometheus-client`:
```bash
docker compose up -d --build api
```

### Нужно сбросить всё окружение

```bash
docker compose down -v
docker compose up -d --build
```

### Первый документ обрабатывается очень долго

Это нормально: воркер выполняет JIT-прогрев PyTorch при старте (~30–45 сек). Последующие документы обрабатываются быстрее.

### MinIO Console недоступна

Проверьте порт 9001 и состояние контейнера:
```bash
docker compose ps minio
docker compose logs minio
```

---

## Известные ограничения

| Ограничение | Описание |
|---|---|
| Sanitize filename удаляет Unicode | Русские/нелатинские имена файлов → `_` в MinIO путях |
| `/api/v1/ask` — заглушка | AI-агент не реализован, эндпоинт возвращает placeholder |
| Частичное покрытие тестами | Нет тестов для `/result`, `/documents`, конкурентной загрузки, chunker |
| Нет auth | API открыт без аутентификации — необходимо добавить для production |
| Нет эндпоинта статуса чанкинга в основном API | Чтобы узнать, готовы ли чанки, нужно обращаться к Chunker API отдельно |
| Prefect PostgreSQL разделяет БД с приложением | Для высокой нагрузки рекомендуется вынести Prefect на отдельный инстанс |
