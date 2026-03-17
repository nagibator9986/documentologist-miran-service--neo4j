# Testing Practices

Observed from all test files, CI workflow, and pyproject configuration.

---

## Framework and Toolchain

| Tool | Version | Role |
|---|---|---|
| pytest | `>=8.0` | Test runner for both services |
| pytest-asyncio | `>=0.23.0` | Async test support (declared as dev dep; not yet used) |
| httpx | `>=0.27.0` | HTTP client for async integration tests (declared; not yet used) |
| FastAPI TestClient | bundled with FastAPI | Synchronous WSGI test client used in `test_api_upload.py` |
| unittest.mock | stdlib | `MagicMock`, `AsyncMock`, `monkeypatch` |
| ruff | separate install | Linting (not a test tool, but part of the same CI job) |

Both services declare their test dependencies under `[project.optional-dependencies] dev` (langgraph-agent `pyproject.toml`) or implicitly via `requirements/dev.txt` (ocr-service).

---

## CI Configuration

Defined in `.github/workflows/python-ci.yml`.

Trigger: push or pull request to `main` / `master`.

Pipeline steps (single job, `ubuntu-latest`, Python 3.11, `fail-fast: false`):

1. Checkout
2. Install ruff globally
3. Install `ocr-service` dependencies (`requirements/api.txt`, `requirements/chunker.txt`, `requirements/dev.txt` — each conditional on file existence)
4. Install `langgraph-agent` as editable package with dev extras (`pip install ".[dev]"`)
5. `ruff check ocr-service`
6. `ruff check langgraph-agent`
7. `pytest -q` inside `ocr-service/`
8. `pytest -q` inside `langgraph-agent/`

No parallelism, no matrix, no test report artifacts, no coverage upload. The `-q` flag suppresses verbose output.

---

## Test Directory Layout

```
documentologist-miran-service--neo4j/
  langgraph-agent/
    tests/
      test_analyze_classify.py    # 1 file, pure unit tests
  ocr-service/
    tests/
      __init__.py
      test_core.py                # unit tests for hasher + dedup logic
      test_api_upload.py          # integration tests for the upload route
```

No `conftest.py` files exist in either service. The `ocr-service/pyproject.toml` configures pytest via:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
```

The `langgraph-agent` has no pytest ini config; pytest discovers `tests/` by convention.

---

## Test File: `langgraph-agent/tests/test_analyze_classify.py`

**What it tests:** The `_detect_task()` function in `analyze_agent.py` — pure regex-based task classification.

**Approach:** Parametrized unit tests with no mocking, no I/O, no fixtures.

**Coverage:**

| Test | Queries covered |
|---|---|
| `test_compare` | 7 Russian compare-intent variants |
| `test_extract` | 6 Russian extract-intent variants |
| `test_summary` | 6 Russian summary-intent variants |
| `test_qa_default` | 4 QA fallbacks + English query (non-match) + Kazakh query (non-match) + empty string |
| `test_case_insensitive` | 3 UPPER_CASE inputs for each non-QA task |
| `test_priority_compare_over_extract` | 1 ambiguous compound query |

Total: 27 test cases via `@pytest.mark.parametrize` + 2 standalone functions.

**Pattern:**

```python
@pytest.mark.parametrize("query", ["...", "..."])
def test_compare(query):
    assert _detect_task(query) == "compare", f"FAIL: '{query}'"
```

Failure messages always include the failing query string for fast diagnosis.

---

## Test File: `ocr-service/tests/test_core.py`

**What it tests:** `compute_sha256` and `compute_sha256_bytes` from `app/services/hasher.py`, plus path-construction logic for MinIO.

**Approach:** Class-grouped unit tests, no mocking, no fixtures.

**Classes:**

- `TestHasher` — 4 tests: determinism, distinctness, file-pointer reset after hashing, known SHA-256 value for empty bytes.
- `TestDeduplicationLogic` — 2 tests: logic-level dedup reasoning (hash equality implies existing record).
- `TestMinIOServiceUnit` — 2 tests: path format assertions (pure string math, no actual MinIO call).

**Notable:** The `TestDeduplicationLogic` tests are logic proofs only — they verify that equal content produces equal hashes, documenting the dedup invariant rather than testing a code path. The actual DB interaction is explicitly noted as mocked in a comment.

---

## Test File: `ocr-service/tests/test_api_upload.py`

**What it tests:** The `POST /api/v1/upload` route in `ocr-service/app/api/routes.py` under various scenarios.

**Approach:** Integration tests using `FastAPI.TestClient` with full dependency override and `monkeypatch`.

**Fixture:**

```python
@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes_module.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
```

A minimal `DummyDBSession` is wired in that tracks `commit_calls` and `rollback_calls` and can be configured to raise on commit via `DummyDBSession.fail_commit`.

All external dependencies (`DocumentService`, `get_minio_service`, `_trigger_prefect_flow`, `compute_sha256`) are patched with `monkeypatch.setattr` and `MagicMock` / `AsyncMock`.

**Test cases (6 total):**

| Test | Scenario | Asserted |
|---|---|---|
| `test_upload_duplicate_returns_existing_document` | Hash already in DB | 201, `is_duplicate=True`, no MinIO call, no flow trigger |
| `test_upload_new_document_stores_file_and_triggers_flow` | New file | 201, `is_duplicate=False`, MinIO upload called, flow triggered |
| `test_upload_rolls_back_when_storage_upload_fails` | MinIO raises on upload | 502, `rollback_calls >= 1`, no MinIO delete, no flow trigger |
| `test_upload_deletes_source_when_commit_fails` | DB commit raises | 500, MinIO delete called with correct path, no flow trigger |
| `test_upload_rejects_file_when_size_exceeds_limit` | File exceeds `max_upload_size_mb` | 413, hash never computed |
| `test_upload_handles_unique_hash_race_as_duplicate` | `IntegrityError` on create (concurrent upload) | 201, `is_duplicate=True`, `mark_duplicate` awaited, no flow trigger |

These tests have high branch coverage of the upload route's error-handling paths.

---

## What Is Covered

| Area | Coverage level |
|---|---|
| `_detect_task()` intent classification (analyze agent) | Good — 6 intents, 4 task types, case sensitivity, priority |
| SHA-256 hasher correctness and file-pointer behavior | Good |
| Upload route: happy path, dedup, error rollback, race condition, size limit | Good |
| MinIO path construction (string format only) | Minimal |

---

## What Is Missing

### No tests at all for

- **`supervisor.py`** — `classify_intent()`, `_keyword_classify()`, `_detect_intents_from_llm()`, all compound and keyword regex patterns. This is the highest-traffic code path.
- **`search_agent.py`** — the entire 13-stage hybrid RAG pipeline. Individual stages (`_prepare_query`, `_detect_exact_search`, `_merge_all_hits`, `_filter_by_relevance`, `_rerank_and_calibrate`, `_build_llm_prompt`, `_build_citations`) are all pure functions and testable without mocking.
- **`analyze_agent.py`** — `_extract_compare_subjects()`, `_build_context_string()`, `_parse_result()`, `_format_output()` are pure functions. Only `_detect_task` has a test file.
- **`verify_agent.py`** — not tested at all.
- **`generate_agent.py`** — not tested at all.
- **`ingest_agent.py`** — `_extract_doc_id()`, `_format_status_response()` are pure functions.
- **`memory_agent.py`** — not tested at all.
- **`core/utils.py`** — `safe_parse_json()` (complex brace-counting parser), `strip_conversational_prefix()`, `extract_hit_filename()`, `extract_hit_page()`, `build_final_response()`, `build_history_messages()`, `sigmoid_score()` are all pure functions with no tests.
- **`core/llm.py`** — `invoke_with_retry()` retry behaviour, fallback to empty string.
- **`graph/workflow.py`** — LangGraph graph construction and routing.
- **OCR pipeline** — `SuryaOCRService.process_document()`, `_structure_output()`, `_table_to_markdown()`.
- **OCR workers** (`workers/pipeline.py`, `workers/main.py`) — no tests.
- **`ocr-service/app/services/documents.py`** — `DocumentService` CRUD methods.
- **`ocr-service/app/services/storage.py`** — MinIO actual upload/download operations.
- **`ocr-service/app/api/routes.py`** — status, result, list, delete, and bulk upload endpoints.

### No integration or end-to-end tests

There are no tests that exercise the full request lifecycle through the LangGraph pipeline (supervisor → agent → state update). All LLM, Qdrant, and Neo4j calls are untested because no fixtures or environment stubs exist for those backends.

### No async test patterns in use

`pytest-asyncio` is declared as a dev dependency but is not used in any existing test. All tests are synchronous, including those that test async route handlers (which use `TestClient`'s sync wrapper).

### No coverage measurement

No `pytest-cov` or coverage config is present in either `pyproject.toml`. CI runs `pytest -q` with no `--cov` flag. Coverage is not tracked or enforced.

### No property-based or fuzz testing

All tests use fixed parametrized inputs. The regex patterns across `supervisor.py`, `search_agent.py`, and `analyze_agent.py` are good candidates for hypothesis-based testing given their linguistic complexity.

---

## Test Execution

```bash
# langgraph-agent
cd langgraph-agent
pip install ".[dev]"
pytest -v

# ocr-service
cd ocr-service
pip install -r requirements/dev.txt
pytest -v
```

The `-q` flag is used in CI for concise output; `-v` is recommended locally for readable parametrize output.
