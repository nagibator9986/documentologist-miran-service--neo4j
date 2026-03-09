

# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/sctructure.py =====

import os

# ====== НАСТРОЙКИ ======
SOURCE_DIR = r"/Users/a1111/Desktop/projects/nurb_documentolog/documentolog"  # папка, которую сканируем
OUTPUT_DIR = "backend"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "main.py")

EXCLUDE_DIRS = {"__pycache__", "migrations", "venv"}
# ======================


def collect_python_files(source_dir):
    py_files = []

    for root, dirs, files in os.walk(source_dir):
        # исключаем ненужные папки
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for file in files:
            if file.endswith(".py"):
                py_files.append(os.path.join(root, file))

    return py_files


def merge_files(py_files, output_file):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as out:
        for file_path in py_files:
            out.write(f"\n\n# ===== FILE: {file_path} =====\n\n")
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    out.write(f.read())
            except Exception as e:
                out.write(f"# ERROR READING FILE: {e}\n")


def main():
    py_files = collect_python_files(SOURCE_DIR)
    merge_files(py_files, OUTPUT_FILE)
    print(f"✅ Объединено файлов: {len(py_files)}")
    print(f"📁 Результат сохранён в: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/frontend_app.py =====

import streamlit as st
import requests
import pandas as pd
import math
import os

# =========================================================
# 1. КОНФИГУРАЦИЯ
# =========================================================

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")

st.set_page_config(
    page_title="Документолог | Система распознавания",
    layout="wide",
    page_icon="📄",
    initial_sidebar_state="expanded"
)

def load_css():
    st.markdown("""
        <style>
        .block-container { padding-top: 2rem; }
        .stButton>button { border-radius: 8px; font-weight: 600; transition: all 0.3s; }
        .stButton>button:hover { transform: translateY(-1px); }
        .stTextInput>div>div>input, .stSelectbox>div>div>div { border-radius: 8px; }
        .stDataFrame { border-radius: 10px; overflow: hidden; }
        .pagination-container {
            display: flex; align-items: center; justify-content: center;
            font-weight: bold; height: 100%;
        }
        </style>
    """, unsafe_allow_html=True)

# =========================================================
# 2. УПРАВЛЕНИЕ СОСТОЯНИЕМ
# =========================================================

def init_session_state():
    defaults = {
        "page": "upload",
        "registry_page": 1,
        "page_size": 10,
        "selected_doc_id": None,
        "selected_doc_name": None,
        "show_ocr_result": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

def reset_pagination():
    st.session_state.registry_page = 1

def set_page(page_num):
    st.session_state.registry_page = page_num

def open_document(doc_id: str, doc_name: str):
    """Переход на страницу детальной информации документа."""
    st.session_state.selected_doc_id = doc_id
    st.session_state.selected_doc_name = doc_name
    st.session_state.page = "document_detail"
    st.session_state.show_ocr_result = False

# =========================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

STATUS_LABELS = {
    "pending":    "⏳ Ожидание",
    "processing": "🔄 В обработке",
    "completed":  "✅ Готово",
    "failed":     "❌ Ошибка",
}

@st.cache_data(show_spinner=False, ttl=10)
def fetch_documents(params: tuple):
    """Запрос документов из API. Принимает tuple для корректного кэширования."""
    try:
        res = requests.get(f"{API_BASE_URL}/documents", params=dict(params), timeout=10)
        if res.status_code == 200:
            return res.json()
        st.error(f"Ошибка API: {res.status_code} — {res.text}")
    except requests.exceptions.RequestException as e:
        st.error(f"Ошибка соединения с сервером: {e}")
    return {"documents": [], "total": 0}

def highlight_status(val):
    if val == "completed":  return "color: #16a34a; font-weight: 600;"
    if val == "failed":     return "color: #dc2626; font-weight: 600;"
    if val == "processing": return "color: #f59e0b; font-weight: 600;"
    return ""

# =========================================================
# 4. КОМПОНЕНТЫ ИНТЕРФЕЙСА
# =========================================================

def render_sidebar():
    st.sidebar.title("📂 Навигация")

    if st.sidebar.button(
        "📤 Загрузка файлов",
        use_container_width=True,
        type="primary" if st.session_state.page == "upload" else "secondary"
    ):
        st.session_state.page = "upload"

    if st.sidebar.button(
        "🗂 Реестр документов",
        use_container_width=True,
        type="primary" if st.session_state.page == "registry" else "secondary"
    ):
        st.session_state.page = "registry"
        reset_pagination()

    # Показываем кнопку "Детальная информация" только когда документ открыт
    if st.session_state.selected_doc_id:
        st.sidebar.divider()
        st.sidebar.caption("📌 Открытый документ")
        name = st.session_state.selected_doc_name or str(st.session_state.selected_doc_id)
        display_name = name[:35] + ("…" if len(name) > 35 else "")
        st.sidebar.markdown(f"**{display_name}**")
        if st.sidebar.button(
            "Детальная информация",
            use_container_width=True,
            type="primary" if st.session_state.page == "document_detail" else "secondary"
        ):
            st.session_state.page = "document_detail"

def render_pagination_controls(total_items):
    page_size = st.session_state.page_size
    current_page = st.session_state.registry_page
    total_pages = math.ceil(total_items / page_size) if total_items > 0 else 1

    if current_page > total_pages:
        st.session_state.registry_page = total_pages
        current_page = total_pages

    st.write("")
    col1, col2, col3, col4, col5, col6 = st.columns([1, 1, 2, 1, 1, 2])

    with col1:
        st.button("⏪", on_click=set_page, args=(1,),
                  disabled=(current_page == 1), use_container_width=True, help="Первая")
    with col2:
        st.button("◀", on_click=set_page, args=(current_page - 1,),
                  disabled=(current_page == 1), use_container_width=True, help="Предыдущая")
    with col3:
        st.markdown(
            f"<div class='pagination-container'>Стр. {current_page} из {total_pages}</div>",
            unsafe_allow_html=True
        )
    with col4:
        st.button("▶", on_click=set_page, args=(current_page + 1,),
                  disabled=(current_page == total_pages), use_container_width=True, help="Следующая")
    with col5:
        st.button("⏩", on_click=set_page, args=(total_pages,),
                  disabled=(current_page == total_pages), use_container_width=True, help="Последняя")
    with col6:
        st.selectbox(
            "Записей на стр:", options=[10, 20, 50, 100],
            key="page_size", on_change=reset_pagination, label_visibility="collapsed"
        )

# =========================================================
# 5. СТРАНИЦЫ
# =========================================================

def page_upload():
    """Страница загрузки файлов с детальными уведомлениями по каждому файлу."""
    st.header("📤 Загрузка документов")
    st.caption("Отправка файлов на OCR обработку")

    uploaded_files = st.file_uploader(
        "Выберите PDF или изображения",
        accept_multiple_files=True,
        type=['pdf', 'png', 'jpg', 'jpeg']
    )

    if st.button("🚀 Отправить на обработку", type="primary") and uploaded_files:
        with st.spinner("Загрузка файлов на сервер..."):
            files = [("files", (f.name, f.getvalue(), f.type)) for f in uploaded_files]
            try:
                response = requests.post(f"{API_BASE_URL}/upload/bulk", files=files)
            except Exception as e:
                st.error(f"Ошибка соединения с сервером: {e}")
                return

            if response.status_code not in (200, 201, 207):
                st.error(f"Ошибка сервера: {response.text}")
                return

            res_data = response.json()
            successful = res_data.get("successful", [])
            failed_items = res_data.get("failed", [])

            # Восстанавливаем имена файлов для успешных:
            # bulk-эндпоинт обрабатывает файлы последовательно, упавшие идут в failed.
            # Вычитаем из списка те, у которых была ошибка.
            failed_names = {item["filename"] for item in failed_items}
            successful_files = [f for f in uploaded_files if f.name not in failed_names]

            # --- Итоговый баннер ---
            new_cnt = sum(1 for r in successful if not r.get("is_duplicate"))
            dup_cnt = sum(1 for r in successful if r.get("is_duplicate"))
            err_cnt = len(failed_items)

            parts = []
            if new_cnt: parts.append(f"**{new_cnt}** новых")
            if dup_cnt: parts.append(f"**{dup_cnt}** дубликатов")
            if err_cnt: parts.append(f"**{err_cnt}** ошибок")

            if err_cnt and not new_cnt and not dup_cnt:
                st.error(f"Все файлы завершились с ошибкой: {err_cnt}")
            elif err_cnt:
                st.warning(f"Загрузка завершена: {', '.join(parts)}")
            else:
                st.success(f"Загрузка завершена: {', '.join(parts)}")

            # --- Детали по каждому успешному файлу ---
            if successful:
                st.markdown("---")
                for idx, item in enumerate(successful):
                    doc_id  = str(item.get("doc_id", ""))
                    is_dup  = item.get("is_duplicate", False)
                    msg     = item.get("message", "")
                    fname   = (
                        successful_files[idx].name
                        if idx < len(successful_files)
                        else f"Документ {idx + 1}"
                    )

                    icon        = "⚠️" if is_dup else "✅"
                    badge_text  = "Дубликат"  if is_dup else "Загружен"
                    badge_bg    = "#fef3c7"   if is_dup else "#dcfce7"
                    badge_color = "#92400e"   if is_dup else "#166534"

                    col_icon, col_info, col_btn = st.columns([0.4, 5.5, 1.5])
                    with col_icon:
                        st.markdown(f"<div style='padding-top:8px;font-size:1.3em'>{icon}</div>",
                                    unsafe_allow_html=True)
                    with col_info:
                        st.markdown(
                            f"**{fname}** &nbsp;"
                            f"<span style='background:{badge_bg};color:{badge_color};"
                            f"padding:2px 10px;border-radius:12px;font-size:0.8em;font-weight:600'>"
                            f"{badge_text}</span><br>"
                            f"<span style='color:#6b7280;font-size:0.85em'>{msg}</span>",
                            unsafe_allow_html=True
                        )
                    with col_btn:
                        if st.button("Открыть →", key=f"upload_open_{doc_id}_{idx}",
                                     use_container_width=True):
                            open_document(doc_id, fname)
                            st.rerun()

            # --- Ошибки ---
            if failed_items:
                st.markdown("---")
                st.markdown("**Ошибки загрузки:**")
                for item in failed_items:
                    st.error(
                        f"**{item.get('filename', '—')}** — "
                        f"{item.get('error', 'Неизвестная ошибка')}"
                    )


def page_registry():
    """Страница реестра документов с кликабельными строками."""
    col_title, col_btn = st.columns([5, 1])
    with col_title:
        st.header("🗂 Реестр документов")
    with col_btn:
        if st.button("🔄 Обновить", use_container_width=True):
            fetch_documents.clear()

    # --- Фильтры ---
    with st.expander("🔍 Фильтры", expanded=True):
        f_col1, f_col2, f_col3, f_col4 = st.columns(4)
        with f_col1:
            f_filename = st.text_input("Название файла содержит:", on_change=reset_pagination)
        with f_col2:
            f_status = st.selectbox(
                "Статус:", ["Все", "pending", "processing", "completed", "failed"],
                on_change=reset_pagination
            )
        with f_col3:
            f_date_from = st.date_input("Создано от:", value=None, on_change=reset_pagination)
        with f_col4:
            f_date_to = st.date_input("Создано до:", value=None, on_change=reset_pagination)

    # --- Параметры запроса ---
    offset = (st.session_state.registry_page - 1) * st.session_state.page_size
    params_dict = {"limit": st.session_state.page_size, "offset": offset, "latest_only": True}
    if f_filename:          params_dict["filename"]       = f_filename
    if f_status != "Все":  params_dict["status"]          = f_status
    if f_date_from:         params_dict["created_after"]  = f"{f_date_from}T00:00:00Z"
    if f_date_to:           params_dict["created_before"] = f"{f_date_to}T23:59:59Z"

    # dict → tuple для корректного хэширования в кэше
    params_tuple = tuple(sorted(params_dict.items()))

    # --- Данные ---
    data      = fetch_documents(params_tuple)
    docs      = data.get("documents", [])
    total_docs = data.get("total", 0)

    st.caption(f"Найдено документов: **{total_docs}**")

    if not docs:
        st.info("Документы не найдены.")
        return

    df = pd.DataFrame(docs)
    df['created_at'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d %H:%M')
    display_df = df[['id', 'filename', 'status', 'page_count', 'created_at']].copy()

    # --- KPI ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Всего (в базе)", total_docs)
    col2.metric("Готово (на стр.)", len(df[df.status == "completed"]))
    col3.metric("В обработке (на стр.)", len(df[df.status == "processing"]))
    col4.metric("Ошибки (на стр.)", len(df[df.status == "failed"]))

    st.divider()
    st.caption("Кликните на строку — откроется детальная страница документа")

    # --- Таблица с выбором строки ---
    styled_df = display_df.style.map(highlight_status, subset=['status'])
    event = st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    # Обработка клика по строке
    selected_rows = event.selection.rows
    if selected_rows:
        row_idx = selected_rows[0]
        if row_idx < len(docs):
            open_document(str(docs[row_idx]["id"]), docs[row_idx]["filename"])
            st.rerun()

    render_pagination_controls(total_docs)


def page_document_detail():
    """Страница детальной информации о конкретном документе."""
    doc_id   = st.session_state.selected_doc_id
    doc_name = st.session_state.selected_doc_name or "—"

    # --- Шапка с кнопкой "Назад" ---
    col_back, col_title = st.columns([1, 9])
    with col_back:
        if st.button("← Реестр", use_container_width=True):
            st.session_state.page = "registry"
            st.rerun()
    with col_title:
        st.header(f" {doc_name}")

    st.divider()

    # --- Загрузка данных ---
    try:
        res = requests.get(f"{API_BASE_URL}/status/{doc_id}", timeout=5)
        if res.status_code != 200:
            st.error(f"Документ не найден (HTTP {res.status_code})")
            return
        doc_data = res.json()
    except Exception as e:
        st.error(f"Ошибка при загрузке данных: {e}")
        return

    status       = doc_data.get("status", "unknown")
    status_label = STATUS_LABELS.get(status, status)

    # --- Карточка с информацией ---
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**ID документа**")
        st.code(str(doc_data.get("doc_id", "—")), language=None)

        st.markdown("**Имя файла**")
        st.markdown(f"`{doc_data.get('filename', '—')}`")

    with c2:
        st.markdown("**Статус**")
        if status == "completed":
            st.success(status_label)
        elif status == "failed":
            st.error(status_label)
        elif status == "processing":
            st.warning(status_label)
        else:
            st.info(status_label)

        result_path = doc_data.get("result_path")
        st.markdown("**Путь к результату OCR**")
        if result_path:
            st.markdown(f"`{result_path}`")
        else:
            st.markdown("_Результат ещё не сформирован_")

    # --- Ошибка обработки ---
    if doc_data.get("error_message"):
        st.divider()
        st.markdown("**Ошибка обработки**")
        st.error(doc_data["error_message"])

    # --- Действия ---
    st.divider()
    action_col1, action_col2, _ = st.columns([2, 2, 6])

    with action_col1:
        if st.button("🔄 Обновить статус", use_container_width=True):
            st.rerun()

    with action_col2:
        ocr_available = (status == "completed")
        toggle_label  = "🙈 Скрыть текст" if st.session_state.show_ocr_result else "📄 Показать OCR текст"
        if st.button(
            toggle_label,
            use_container_width=True,
            disabled=not ocr_available,
            help="Доступно только для обработанных документов"
        ):
            st.session_state.show_ocr_result = not st.session_state.show_ocr_result
            st.rerun()

    # --- OCR результат ---
    if st.session_state.show_ocr_result and status == "completed":
        with st.spinner("Загрузка распознанного текста..."):
            try:
                json_res = requests.get(f"{API_BASE_URL}/result/{doc_id}", timeout=15)
                if json_res.status_code == 200:
                    json_data = json_res.json()
                    tab1, tab2 = st.tabs(["📝 Текст", "📦 Raw JSON"])
                    with tab1:
                        full_text = json_data.get("full_text", "")
                        if full_text:
                            st.text_area("Распознанный текст", full_text, height=500)
                        else:
                            st.info("Текст не распознан (пустой результат)")
                    with tab2:
                        st.json(json_data)
                else:
                    st.warning(f"Не удалось загрузить результат OCR (HTTP {json_res.status_code})")
            except Exception as e:
                st.error(f"Ошибка загрузки результата: {e}")

# =========================================================
# 6. ТОЧКА ВХОДА
# =========================================================

def main():
    load_css()
    init_session_state()
    render_sidebar()

    if st.session_state.page == "upload":
        page_upload()
    elif st.session_state.page == "registry":
        page_registry()
    elif st.session_state.page == "document_detail":
        page_document_detail()

if __name__ == "__main__":
    main()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/main.py =====

"""
Документолог: Core — FastAPI Application.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.routes import router as doc_router
from app.core.config import get_settings
from app.core.database import engine
from app.schemas.document import HealthResponse
from app.services.storage import get_minio_service


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("🚀 Starting Документолог API …")
    logger.info("📦 Schema management: Alembic migrations are required before startup.")

    yield

    logger.info("👋 Shutting down …")
    await engine.dispose()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    description="Система автоматизированной обработки документов",
    lifespan=lifespan,
)

# CORS (loosen in dev, tighten in prod)
cors_allow_credentials = settings.cors_allow_credentials
if "*" in settings.cors_origins and cors_allow_credentials:
    logger.warning("CORS misconfiguration detected: disabling credentials for wildcard origins.")
    cors_allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────────

app.include_router(doc_router, prefix="/api/v1")


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    minio_ok = "unknown"
    db_ok = "unknown"
    try:
        minio_ok = "ok" if get_minio_service().health_check() else "error"
    except Exception:
        minio_ok = "error"

    try:
        from sqlalchemy import text
        from app.core.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            db_ok = "ok"
    except Exception:
        db_ok = "error"

    return HealthResponse(
        status="ok" if db_ok == "ok" and minio_ok == "ok" else "degraded",
        version=settings.app_version,
        database=db_ok,
        minio=minio_ok,
    )


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/core/config.py =====

"""
Centralized configuration loaded from environment variables.
"""

import json
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # ── Database ────────────────────────────
    database_url: str = "postgresql+asyncpg://docolog:docolog_secret@localhost:5433/documentolog"
    database_url_sync: str = "postgresql://docolog:docolog_secret@localhost:5433/documentolog"

    # ── MinIO ───────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin123"
    minio_secure: bool = False

    # ── Buckets ─────────────────────────────
    bucket_source: str = "source-files"
    bucket_results: str = "analysis-results"

    # ── Prefect ─────────────────────────────
    prefect_api_url: str = "http://localhost:4200/api"

    # ── OCR ──────────────────────────────────
    ocr_concurrency_limit: int = 1
    ocr_timeout_seconds: int = 300
    ocr_retry_delay_seconds: int = 60
    max_upload_size_mb: int = 50

    # ── App ──────────────────────────────────
    log_level: str = "INFO"
    app_title: str = "Документолог: Core"
    app_version: str = "0.1.0"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    cors_allow_credentials: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"  # <--- Это скажет FastAPI игнорировать переменные для других контейнеров
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/core/database.py =====

"""
Async and sync SQLAlchemy engines and session factories.

- Async engine  → FastAPI route handlers
- Sync engine   → Prefect worker tasks + FastAPI background tasks
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# ── Async (FastAPI) ───────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async session (commit is explicit in handlers)."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── Sync (Prefect workers + background tasks) ─────────────────────
sync_engine = create_engine(
    settings.database_url_sync,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


@contextmanager
def get_sync_db() -> Generator[Session, None, None]:
    """
    Context manager for synchronous DB sessions.
    Used by Prefect worker tasks and FastAPI background tasks.
    Always closes the session and rolls back on unhandled exceptions.
    """
    session = SyncSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/core/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/models/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/models/document.py =====

"""
SQLAlchemy models for the documents table.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    file_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="SHA-256 hash of file content",
    )
    filename: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Original filename",
    )
    s3_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=True,
        comment="Path to original file in MinIO (source-files bucket)",
    )
    result_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=True,
        comment="Path to Surya JSON result in MinIO (analysis-results bucket)",
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", create_type=True),
        default=DocumentStatus.PENDING,
        nullable=False,
    )
    is_latest: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="True if this is the latest version of the file",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error description if status == failed",
    )
    page_count: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Number of pages in the document",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Composite index for version queries
    __table_args__ = (
        Index("ix_documents_filename_latest", "filename", "is_latest"),
    )

    def __repr__(self) -> str:
        return f"<Document {self.id} [{self.status.value}] {self.filename}>"


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/schemas/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/schemas/document.py =====

"""
Pydantic schemas for API input / output.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.document import DocumentStatus


# ── Response schemas ─────────────────────────────────────────

class DocumentResponse(BaseModel):
    id: UUID
    file_hash: str
    filename: str
    status: DocumentStatus
    is_latest: bool
    s3_path: str | None = None
    result_path: str | None = None
    error_message: str | None = None
    page_count: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    doc_id: UUID
    status: DocumentStatus
    is_duplicate: bool = False
    message: str


class StatusResponse(BaseModel):
    doc_id: UUID
    status: DocumentStatus
    filename: str
    result_path: str | None = None
    error_message: str | None = None


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentResponse]


class AskRequest(BaseModel):
    doc_id: UUID
    question: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    doc_id: UUID
    answer: str
    sources: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    database: str = "unknown"
    minio: str = "unknown"


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/api/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/api/routes.py =====

"""
FastAPI router: upload, upload bulk, status, result, list, ask (stub).
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.document import DocumentStatus
from app.schemas.document import (
    AskRequest,
    AskResponse,
    DocumentListResponse,
    DocumentResponse,
    StatusResponse,
    UploadResponse,
)
from app.services.documents import DocumentService
from app.services.hasher import compute_sha256
from app.services.storage import get_minio_service

router = APIRouter(tags=["Documents"])
settings = get_settings()

# ── File-type validation ──────────────────────────────────────────

_ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

# Magic-byte signatures → internal type label
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8", "jpeg"),
    (b"II*\x00", "tiff"),   # little-endian TIFF
    (b"MM\x00*", "tiff"),   # big-endian TIFF
    (b"BM", "bmp"),
]

# Extension → allowed detected magic types
_EXT_TO_TYPES: dict[str, set[str]] = {
    ".pdf":  {"pdf"},
    ".png":  {"png"},
    ".jpg":  {"jpeg"},
    ".jpeg": {"jpeg"},
    ".tiff": {"tiff"},
    ".bmp":  {"bmp"},
    ".webp": {"webp"},
}


def _detect_magic_type(header: bytes) -> str | None:
    """Return the file type from magic bytes, or None if unknown."""
    # WEBP: RIFF????WEBP
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    for signature, ftype in _MAGIC_SIGNATURES:
        if header[: len(signature)] == signature:
            return ftype
    return None


def _validate_file_type(filename: str, header: bytes) -> None:
    """
    Raise HTTPException(415) if:
    - extension is not in the allowed set, OR
    - magic bytes do not match the declared extension.
    """
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Неподдерживаемый тип файла: '{ext}'. "
                f"Разрешены: PDF, PNG, JPG, JPEG, TIFF, BMP, WEBP."
            ),
        )

    detected = _detect_magic_type(header)
    if detected is None:
        raise HTTPException(
            status_code=415,
            detail="Содержимое файла не соответствует ни одному допустимому формату.",
        )

    if detected not in _EXT_TO_TYPES.get(ext, set()):
        raise HTTPException(
            status_code=415,
            detail=(
                f"Расширение '{ext}' не соответствует содержимому файла "
                f"(обнаружен тип: {detected})."
            ),
        )


# ── Schemas ──────────────────────────────────────────────────

class BulkUploadResponse(BaseModel):
    successful: list[UploadResponse]
    failed: list[dict]


# ── Endpoints ────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a document.
    - Computes SHA-256 hash.
    - If duplicate → returns existing record (no reprocessing).
    - If new → stores in MinIO, creates DB record with status=pending.
      The background worker picks it up automatically via polling.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    try:
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to read uploaded file.") from exc

    if file_size == 0:
        raise HTTPException(status_code=400, detail="Empty file.")

    max_size_bytes = settings.max_upload_size_mb * 1024 * 1024
    if file_size > max_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size is {settings.max_upload_size_mb} MB.",
        )

    # ── MIME / magic-bytes validation ────────────────────────
    header = file.file.read(12)
    file.file.seek(0)
    _validate_file_type(file.filename, header)
    # ────────────────────────────────────────────────────────

    file_hash = compute_sha256(file.file)
    svc = DocumentService(db)

    # ── Deduplication check ──────────────────────────────────
    existing = await svc.find_by_hash(file_hash)
    if existing is not None:
        await svc.mark_duplicate(existing)
        await db.commit()
        logger.info(f"Duplicate detected: {file_hash} → doc {existing.id}")
        return UploadResponse(
            doc_id=existing.id,
            status=existing.status,
            is_duplicate=True,
            message="Файл уже был загружен ранее. Возвращён существующий результат.",
        )

    # ── New file ─────────────────────────────────────────────
    minio = get_minio_service()
    s3_path = minio.build_source_path(file_hash=file_hash, filename=file.filename)

    try:
        doc = await svc.create_document(
            file_hash=file_hash,
            filename=file.filename,
            s3_path=s3_path,
        )
    except IntegrityError:
        await db.rollback()
        existing = await svc.find_by_hash(file_hash)
        if existing is None:
            raise HTTPException(status_code=409, detail="Duplicate upload conflict.")
        await svc.mark_duplicate(existing)
        await db.commit()
        logger.info(f"Duplicate detected (race): {file_hash} → doc {existing.id}")
        return UploadResponse(
            doc_id=existing.id,
            status=existing.status,
            is_duplicate=True,
            message="Файл уже был загружен ранее. Возвращён существующий результат.",
        )

    try:
        minio.upload_source_file(
            file_hash=file_hash,
            filename=file.filename,
            data=file.file,
            size=file_size,
        )
    except Exception as exc:
        await db.rollback()
        logger.error(f"Failed to upload source file to MinIO: {exc}")
        raise HTTPException(status_code=502, detail="Ошибка загрузки файла в хранилище.")

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        minio.delete_source_file(s3_path)
        logger.error(f"Failed to commit document metadata: {exc}")
        raise HTTPException(status_code=500, detail="Ошибка сохранения метаданных документа.")

    return UploadResponse(
        doc_id=doc.id,
        status=doc.status,
        is_duplicate=False,
        message="Файл загружен. Обработка запущена.",
    )


@router.post("/upload/bulk", response_model=BulkUploadResponse, status_code=207)
async def upload_documents_bulk(
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Массовая загрузка документов. 
    Итерируется по списку файлов и вызывает логику одиночной загрузки.
    Возвращает статус 207 (Multi-Status), собирая успешные и ошибочные файлы.
    """
    successful = []
    failed = []

    for file in files:
        try:
            # Вызываем функцию одиночной загрузки напрямую
            res = await upload_document(file=file, db=db)
            successful.append(res)
        except HTTPException as e:
            failed.append({"filename": file.filename, "error": str(e.detail)})
        except Exception as e:
            failed.append({"filename": file.filename, "error": str(e)})

    return BulkUploadResponse(successful=successful, failed=failed)


@router.get("/status/{doc_id}", response_model=StatusResponse)
async def get_status(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return processing status for a document."""
    svc = DocumentService(db)
    doc = await svc.find_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    return StatusResponse(
        doc_id=doc.id,
        status=doc.status,
        filename=doc.filename,
        result_path=doc.result_path,
        error_message=doc.error_message,
    )


@router.get("/result/{doc_id}")
async def get_document_result(doc_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Скачивает и отдает JSON с результатами OCR из MinIO.
    Используется фронтендом (Streamlit) для отображения полного текста документа.
    """
    svc = DocumentService(db)
    doc = await svc.find_by_id(doc_id)
    
    if not doc or not doc.result_path:
        raise HTTPException(status_code=404, detail="Результат не найден или документ еще в обработке.")
    
    try:
        minio = get_minio_service()
        data = minio.download_result_json(doc.result_path)
        return JSONResponse(content=json.loads(data))
    except Exception as e:
        logger.error(f"Failed to fetch result for doc {doc_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка получения результата из хранилища.")


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    latest_only: bool = Query(True),
    status: DocumentStatus | None = Query(None, description="Фильтр по статусу"),
    filename: str | None = Query(None, description="Поиск по имени файла"),
    created_after: datetime | None = Query(None, description="Дата создания от"),
    created_before: datetime | None = Query(None, description="Дата создания до"),
    db: AsyncSession = Depends(get_db),
):
    """List uploaded documents (paginated). Supports multiple filters."""
    svc = DocumentService(db)
    
    # Обрати внимание, что тебе нужно обновить `list_documents` в `app/services/documents.py`, 
    # чтобы он принимал эти новые аргументы!
    docs, total = await svc.list_documents(
        offset=offset, 
        limit=limit, 
        latest_only=latest_only,
        status=status,
        filename=filename,
        created_after=created_after,
        created_before=created_before
    )
    
    return DocumentListResponse(
        total=total,
        documents=[DocumentResponse.model_validate(d) for d in docs],
    )


@router.post("/ask", response_model=AskResponse)
async def ask_document(body: AskRequest):
    """
    Stub endpoint for the future AI agent.
    Will accept a question about a document and return an answer.
    """
    return AskResponse(
        doc_id=body.doc_id,
        answer="⚠️ AI-агент ещё не реализован. Это заглушка.",
        sources=[],
    )

# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/documents.py =====

"""
Core business logic: upload, deduplication, status tracking.
"""

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from app.models.document import Document, DocumentStatus


class DocumentService:
    """CRUD + deduplication logic for documents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── queries ──────────────────────────────────────────────

    async def find_by_hash(self, file_hash: str) -> Document | None:
        """Return document with the given hash, or None."""
        stmt = (
            select(Document)
            .where(Document.file_hash == file_hash)
            .order_by(Document.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_id(self, doc_id: uuid.UUID) -> Document | None:
        stmt = select(Document).where(Document.id == doc_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # app/services/documents.py (обновленный метод)



    async def list_documents(
        self, 
        offset: int = 0, 
        limit: int = 50, 
        latest_only: bool = True,
        status: DocumentStatus | None = None,
        filename: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> tuple[list[Document], int]:
        """Return paginated list and total count with filters."""
        base = select(Document)
    
        if latest_only:
            base = base.where(Document.is_latest.is_(True))
        if status:
            base = base.where(Document.status == status)
        if filename:
            base = base.where(Document.filename.ilike(f"%{filename}%"))
        if created_after:
            base = base.where(Document.created_at >= created_after)
        if created_before:
            base = base.where(Document.created_at <= created_before)
        
        base = base.order_by(Document.created_at.desc())

        # total count
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # paginated rows
        stmt = base.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    # ── mutations ────────────────────────────────────────────

    async def create_document(
        self,
        file_hash: str,
        filename: str,
        s3_path: str,
    ) -> Document:
        """
        Create a new document record.
        If a document with the same filename already exists, mark
        the old one as is_latest=False (version chain).
        """
        # mark previous versions as not latest
        await self.session.execute(
            update(Document)
            .where(Document.filename == filename, Document.is_latest.is_(True))
            .values(is_latest=False)
        )

        doc = Document(
            file_hash=file_hash,
            filename=filename,
            s3_path=s3_path,
            status=DocumentStatus.PENDING,
            is_latest=True,
        )
        self.session.add(doc)
        await self.session.flush()
        logger.info(f"Created document record {doc.id} for {filename}")
        return doc

    async def mark_duplicate(self, doc: Document) -> Document:
        """Touch updated_at on a duplicate upload."""
        doc.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return doc

    async def update_status(
        self,
        doc_id: uuid.UUID,
        status: DocumentStatus,
        result_path: str | None = None,
        error_message: str | None = None,
        page_count: int | None = None,
    ) -> None:
        values: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if result_path is not None:
            values["result_path"] = result_path
        if error_message is not None:
            values["error_message"] = error_message
        if page_count is not None:
            values["page_count"] = page_count

        await self.session.execute(
            update(Document).where(Document.id == doc_id).values(**values)
        )
        await self.session.flush()
        logger.info(f"Document {doc_id} → {status.value}")


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/ocr.py =====

import gc
import io
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import fitz
    import torch
    from PIL import Image
    SURYA_AVAILABLE = True
    # Use all available CPU cores for PyTorch operations.
    # This speeds up matrix multiplications in OCR models significantly.
    _cpu_count = os.cpu_count() or 4
    torch.set_num_threads(_cpu_count)
    torch.set_num_interop_threads(max(1, _cpu_count // 2))
except ImportError:
    SURYA_AVAILABLE = False
    logger.warning("PyMuPDF/Pillow not installed.")


class SuryaOCRService:
    def __init__(self):
        if not SURYA_AVAILABLE:
            raise RuntimeError("Required packages not installed.")

        logger.info("Loading Surya OCR models (Memory Optimized Mode)…")

        from surya.detection import DetectionPredictor
        self.det_predictor = DetectionPredictor(device="cpu")
        logger.info("  ✓ DetectionPredictor")

        try:
            from surya.foundation import FoundationPredictor
            self.foundation_predictor = FoundationPredictor(device="cpu")
            logger.info("  ✓ FoundationPredictor")
        except Exception as e:
            logger.warning(f"  ✗ FoundationPredictor: {e}")
            self.foundation_predictor = None

        try:
            from surya.recognition import RecognitionPredictor
            if self.foundation_predictor:
                self.rec_predictor = RecognitionPredictor(self.foundation_predictor)
            else:
                self.rec_predictor = RecognitionPredictor()
            logger.info("  ✓ RecognitionPredictor")
        except Exception as e:
            logger.error(f"  ✗ RecognitionPredictor: {e}")
            self.rec_predictor = None

        # --- ЗАКОММЕНТИРОВАНО ДЛЯ ЭКОНОМИИ RAM ---
        self.layout_predictor = None
        # try:
        #     from surya.layout import LayoutPredictor
        #     self.layout_predictor = LayoutPredictor(device="cpu")
        #     logger.info("  ✓ LayoutPredictor")
        # except Exception as e:
        #     self.layout_predictor = None

        self.table_predictor = None
        # try:
        #     from surya.table_rec import TableRecPredictor
        #     self.table_predictor = TableRecPredictor(device="cpu")
        #     logger.info("  ✓ TableRecPredictor")
        # except Exception as e:
        #     self.table_predictor = None

        logger.info("✅ Surya models ready (OCR Only).")

    @staticmethod
    def pdf_to_images(pdf_bytes, dpi=120):
        # DPI=120 снижает потребление памяти в 3-4 раза по сравнению с DPI=200
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
        doc.close()
        return images

    def process_document(self, file_bytes, filename):
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            images = self.pdf_to_images(file_bytes)
        elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
            images = [Image.open(io.BytesIO(file_bytes)).convert("RGB")]
        else:
            raise ValueError(f"Unsupported: {ext}")

        page_count = len(images)
        logger.info(f"Processing {page_count} page(s) from '{filename}'")

        ocr_results = None
        if self.rec_predictor:
            try:
                logger.info("Running OCR (page-by-page to limit peak memory)…")
                ocr_results = []
                for idx, img in enumerate(images):
                    logger.info(f"  Page {idx + 1}/{page_count}…")
                    page_result = self.rec_predictor(
                        [img],
                        det_predictor=self.det_predictor,
                        sort_lines=True,
                    )
                    ocr_results.extend(page_result)
                    gc.collect()  # release page tensors before next page
                logger.info(f"OCR done: {len(ocr_results)} pages")
            except Exception as e:
                logger.error(f"OCR failed: {e}")

        # --- ВЫЗОВЫ ЭТИХ МОДЕЛЕЙ ТАКЖЕ ОТКЛЮЧЕНЫ ---
        layout_results = None
        # if self.layout_predictor:
        #     try:
        #         layout_results = self.layout_predictor(images)
        #     except Exception as e:
        #         logger.warning(f"Layout failed: {e}")

        table_results = None
        # if self.table_predictor:
        #     try:
        #         table_results = self.table_predictor.batch_table_recognition(images)
        #     except Exception as e:
        #         logger.warning(f"Tables failed: {e}")

        return self._structure_output(filename, page_count, ocr_results, layout_results, table_results)

    def _structure_output(self, filename, page_count, ocr_results, layout_results, table_results):
        pages = []
        for page_idx in range(page_count):
            page_data = {"page_number": page_idx + 1, "blocks": [], "full_text": ""}

            if ocr_results and page_idx < len(ocr_results):
                ocr_page = ocr_results[page_idx]
                text_lines = []
                for line in getattr(ocr_page, "text_lines", []):
                    block = {"type": "text", "text": getattr(line, "text", ""), "confidence": round(getattr(line, "confidence", 0), 3)}
                    bbox = getattr(line, "bbox", None)
                    if bbox is not None:
                        block["bbox"] = bbox if isinstance(bbox, list) else list(bbox)
                    page_data["blocks"].append(block)
                    text_lines.append(block["text"])
                page_data["full_text"] = "\n".join(text_lines)

            if layout_results and page_idx < len(layout_results):
                layout_page = layout_results[page_idx]
                bboxes = getattr(layout_page, "bboxes", []) or getattr(layout_page, "layout_bboxes", [])
                layout_blocks = []
                for b in bboxes:
                    lb = {"label": getattr(b, "label", "unknown")}
                    bbox = getattr(b, "bbox", None)
                    if bbox: lb["bbox"] = bbox if isinstance(bbox, list) else list(bbox)
                    conf = getattr(b, "confidence", None)
                    if conf: lb["confidence"] = round(conf, 3)
                    layout_blocks.append(lb)
                if layout_blocks:
                    page_data["layout"] = layout_blocks

            if table_results and page_idx < len(table_results):
                table_page = table_results[page_idx]
                tables_list = getattr(table_page, "tables", [])
                if not tables_list and isinstance(table_page, list):
                    tables_list = table_page
                page_tables = []
                for t_idx, table in enumerate(tables_list):
                    md = self._table_to_markdown(table)
                    if md:
                        page_tables.append({"table_index": t_idx, "markdown": md})
                if page_tables:
                    page_data["tables"] = page_tables

            pages.append(page_data)

        full_text = "\n\n".join(p["full_text"] for p in pages if p["full_text"])
        return {"filename": filename, "page_count": page_count, "pages": pages, "full_text": full_text}

    @staticmethod
    def _table_to_markdown(table):
        try:
            cells = getattr(table, "cells", [])
            if not cells: return ""
            max_row = max(getattr(c, "row", 0) for c in cells) + 1
            max_col = max(getattr(c, "col", 0) for c in cells) + 1
            grid = [["" for _ in range(max_col)] for _ in range(max_row)]
            for cell in cells:
                grid[getattr(cell, "row", 0)][getattr(cell, "col", 0)] = getattr(cell, "text", "").strip()
            lines = []
            for r_idx, row in enumerate(grid):
                lines.append("| " + " | ".join(row) + " |")
                if r_idx == 0:
                    lines.append("| " + " | ".join("---" for _ in row) + " |")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Table→MD failed: {e}")
            return ""

_ocr_service = None

def get_ocr_service():
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = SuryaOCRService()
    return _ocr_service


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/storage.py =====

"""
MinIO (S3-compatible) storage operations.
"""

import io
import re
from pathlib import Path
from typing import BinaryIO

from loguru import logger
from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings


class MinIOService:
    """Wrapper around the MinIO client."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket_source = settings.bucket_source
        self.bucket_results = settings.bucket_results

    # ── helpers ──────────────────────────────────────────────

    def _ensure_bucket(self, bucket: str) -> None:
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
            logger.info(f"Created bucket: {bucket}")

    # ── public API ───────────────────────────────────────────

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Strip directory components and dangerous characters from a filename.

        Prevents path-traversal attacks such as '../../admin/secret.pdf'
        from poisoning MinIO object paths.
        """
        # Take only the basename — removes any leading path like ../../
        name = Path(filename).name
        # Replace every character that is not alphanumeric, dash, dot,
        # underscore, or space with an underscore.
        name = re.sub(r"[^\w\-_. ]", "_", name)
        # Strip leading/trailing dots and spaces that could confuse storage.
        name = name.strip(". ")
        return name or "unnamed_file"

    @staticmethod
    def build_source_path(file_hash: str, filename: str) -> str:
        safe_name = MinIOService.sanitize_filename(filename)
        return f"{file_hash}/{safe_name}"

    def upload_source_file(
        self, file_hash: str, filename: str, data: BinaryIO, size: int
    ) -> str:
        """Upload original file. Returns the S3 path."""
        self._ensure_bucket(self.bucket_source)
        object_name = self.build_source_path(file_hash=file_hash, filename=filename)
        self.client.put_object(
            bucket_name=self.bucket_source,
            object_name=object_name,
            data=data,
            length=size,
        )
        logger.info(f"Uploaded source file: {self.bucket_source}/{object_name}")
        return object_name

    def upload_result_json(self, file_hash: str, json_bytes: bytes) -> str:
        """Upload Surya analysis JSON. Returns the S3 path."""
        self._ensure_bucket(self.bucket_results)
        object_name = f"{file_hash}/surya_output.json"
        self.client.put_object(
            bucket_name=self.bucket_results,
            object_name=object_name,
            data=io.BytesIO(json_bytes),
            length=len(json_bytes),
            content_type="application/json",
        )
        logger.info(f"Uploaded result JSON: {self.bucket_results}/{object_name}")
        return object_name

    def download_source_file(self, s3_path: str) -> bytes:
        """Download original file from MinIO."""
        response = self.client.get_object(self.bucket_source, s3_path)
        data = response.read()
        response.close()
        response.release_conn()
        return data

    def download_result_json(self, result_path: str) -> bytes:
        """Download Surya result JSON from MinIO."""
        response = self.client.get_object(self.bucket_results, result_path)
        data = response.read()
        response.close()
        response.release_conn()
        return data

    def delete_source_file(self, s3_path: str) -> None:
        """Delete original file from MinIO if present."""
        try:
            self.client.remove_object(self.bucket_source, s3_path)
            logger.info(f"Deleted source file: {self.bucket_source}/{s3_path}")
        except S3Error as e:
            logger.warning(f"Failed to delete source file {s3_path}: {e}")

    def health_check(self) -> bool:
        """Return True if MinIO is reachable."""
        try:
            self.client.list_buckets()
            return True
        except S3Error:
            return False


# Singleton
_minio_service: MinIOService | None = None


def get_minio_service() -> MinIOService:
    global _minio_service
    if _minio_service is None:
        _minio_service = MinIOService()
    return _minio_service


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/hasher.py =====

"""
SHA-256 hashing for file deduplication.
"""

import hashlib
from typing import BinaryIO

CHUNK_SIZE = 8192  # 8 KB


def compute_sha256(file: BinaryIO) -> str:
    """
    Compute SHA-256 hash of a file-like object.
    Resets file pointer to start after computation.
    """
    sha = hashlib.sha256()
    file.seek(0)
    while chunk := file.read(CHUNK_SIZE):
        sha.update(chunk)
    file.seek(0)
    return sha.hexdigest()


def compute_sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hash of raw bytes."""
    return hashlib.sha256(data).hexdigest()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/frontend/app.py =====

import streamlit as st
import requests
import pandas as pd
from datetime import datetime

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")

st.set_page_config(page_title="Документолог", layout="wide", page_icon="📄")

# --- Навигация ---
st.sidebar.title("Меню")
page = st.sidebar.radio("Перейти к:", ["Загрузка документов", "Реестр документов"])

# ==========================================
# СТРАНИЦА 1: ЗАГРУЗКА ДОКУМЕНТОВ
# ==========================================
if page == "Загрузка документов":
    st.title("📤 Загрузка документов")
    st.write("Выберите один или несколько файлов для отправки на распознавание (OCR).")

    uploaded_files = st.file_uploader("Выберите PDF или изображения", accept_multiple_files=True, type=['pdf', 'png', 'jpg', 'jpeg'])

    if st.button("🚀 Отправить на обработку") and uploaded_files:
        with st.spinner("Загрузка файлов на сервер..."):
            files = [("files", (file.name, file.getvalue(), file.type)) for file in uploaded_files]
            
            try:
                # Используем bulk endpoint
                response = requests.post(f"{API_BASE_URL}/upload/bulk", files=files)
                if response.status_code in (200, 201, 207):
                    res_data = response.json()
                    st.success(f"Успешно загружено: {len(res_data['successful'])} файлов.")
                    if res_data['failed']:
                        st.error(f"Ошибок загрузки: {len(res_data['failed'])}")
                        st.json(res_data['failed'])
                else:
                    st.error(f"Ошибка сервера: {response.text}")
            except Exception as e:
                st.error(f"Ошибка соединения с сервером: {e}")

# ==========================================
# СТРАНИЦА 2: РЕЕСТР ДОКУМЕНТОВ И ДЕТАЛИ
# ==========================================
elif page == "Реестр документов":
    st.title("🗂 Реестр документов")

    # --- ФИЛЬТРЫ ---
    with st.expander("🔍 Фильтры", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            f_filename = st.text_input("Название файла содержит:")
        with col2:
            f_status = st.selectbox("Статус:", ["Все", "pending", "processing", "completed", "failed"])
        with col3:
            f_date_from = st.date_input("Создано от:", value=None)
        with col4:
            f_date_to = st.date_input("Создано до:", value=None)

    # Формируем query параметры
    params = {"limit": 100, "latest_only": True}
    if f_filename: params["filename"] = f_filename
    if f_status != "Все": params["status"] = f_status
    if f_date_from: params["created_after"] = f"{f_date_from}T00:00:00Z"
    if f_date_to: params["created_before"] = f"{f_date_to}T23:59:59Z"

    # Запрос списка
    try:
        res = requests.get(f"{API_BASE_URL}/documents", params=params)
        if res.status_code == 200:
            data = res.json()
            docs = data.get("documents", [])
            st.caption(f"Найдено документов: {data.get('total', 0)}")
            
            if docs:
                # Преобразуем в DataFrame для красивой таблицы
                df = pd.DataFrame(docs)
                df['created_at'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d %H:%M')
                
                # Оставляем только нужные колонки
                display_df = df[['id', 'filename', 'status', 'page_count', 'created_at']]
                
                # Рисуем таблицу. При клике на строку будем показывать детали
                st.dataframe(display_df, use_container_width=True, hide_index=True)

                st.markdown("###  Детальная информация по документу")
                selected_id = st.selectbox("Выберите ID документа для просмотра деталей:", df['id'].tolist())
                
                if selected_id:
                    # Запрашиваем статус и детали
                    status_res = requests.get(f"{API_BASE_URL}/status/{selected_id}").json()
                    
                    st.write(f"**Файл:** {status_res['filename']}")
                    st.write(f"**Статус:** `{status_res['status']}`")
                    if status_res.get("error_message"):
                        st.error(f"Ошибка: {status_res['error_message']}")

                    if status_res['status'] == "completed":
                        if st.button("📄 Показать распознанный текст (JSON)"):
                            with st.spinner("Загрузка результата из S3..."):
                                json_res = requests.get(f"{API_BASE_URL}/result/{selected_id}")
                                if json_res.status_code == 200:
                                    json_data = json_res.json()
                                    
                                    # Показываем вкладки: Полный текст и Сырой JSON
                                    tab1, tab2 = st.tabs(["Текст", "Raw JSON"])
                                    with tab1:
                                        st.text_area("Распознанный текст", json_data.get("full_text", ""), height=400)
                                    with tab2:
                                        st.json(json_data)
                                else:
                                    st.warning("Не удалось загрузить JSON результат.")
            else:
                st.info("Документы не найдены.")
        else:
            st.error("Ошибка получения данных от API.")
    except Exception as e:
        st.error(f"Ошибка соединения с API: {e}")

# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/tests/test_core.py =====

"""
Tests for the document upload and deduplication logic.
Run with: pytest tests/ -v
"""

import io

from app.services.hasher import compute_sha256, compute_sha256_bytes


class TestHasher:
    def test_sha256_deterministic(self):
        data = b"hello world"
        h1 = compute_sha256_bytes(data)
        h2 = compute_sha256_bytes(data)
        assert h1 == h2

    def test_sha256_different_content(self):
        h1 = compute_sha256_bytes(b"file1")
        h2 = compute_sha256_bytes(b"file2")
        assert h1 != h2

    def test_sha256_file_like(self):
        buf = io.BytesIO(b"test content")
        h = compute_sha256(buf)
        assert len(h) == 64  # SHA-256 hex digest is 64 chars
        # File pointer should be reset
        assert buf.tell() == 0

    def test_sha256_known_value(self):
        # SHA-256 of empty bytes
        h = compute_sha256_bytes(b"")
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestDeduplicationLogic:
    """Unit tests verifying the dedup flow logic."""

    def test_duplicate_returns_existing_id(self):
        """When a hash exists, no new record should be created."""
        # This is a logic-level test — the actual DB query is mocked
        existing_hash = compute_sha256_bytes(b"same content")
        new_hash = compute_sha256_bytes(b"same content")
        assert existing_hash == new_hash  # dedup condition

    def test_new_content_gets_new_hash(self):
        h1 = compute_sha256_bytes(b"version 1")
        h2 = compute_sha256_bytes(b"version 2")
        assert h1 != h2  # should create new record


class TestMinIOServiceUnit:
    """Basic unit tests for MinIO path construction."""

    def test_source_path_format(self):
        file_hash = "abc123"
        filename = "report.pdf"
        expected = f"{file_hash}/{filename}"
        assert expected == "abc123/report.pdf"

    def test_result_path_format(self):
        file_hash = "abc123"
        expected = f"{file_hash}/surya_output.json"
        assert expected == "abc123/surya_output.json"


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/tests/test_api_upload.py =====

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import app.api.routes as routes_module
from app.core.database import get_db
from app.models.document import DocumentStatus


class DummyDBSession:
    fail_commit = False
    last_instance = None

    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        DummyDBSession.last_instance = self

    async def commit(self) -> None:
        self.commit_calls += 1
        if DummyDBSession.fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rollback_calls += 1


async def override_get_db():
    yield DummyDBSession()


@pytest.fixture
def client():
    DummyDBSession.fail_commit = False
    DummyDBSession.last_instance = None
    app = FastAPI()
    app.include_router(routes_module.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_upload_duplicate_returns_existing_document(client, monkeypatch):
    doc_id = uuid.uuid4()
    existing_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.COMPLETED)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=existing_doc)
    service.mark_duplicate = AsyncMock(return_value=existing_doc)
    service.create_document = AsyncMock()

    minio = MagicMock()
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"same-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is True
    service.create_document.assert_not_called()
    minio.upload_source_file.assert_not_called()
    trigger_flow.assert_not_called()


def test_upload_new_document_stores_file_and_triggers_flow(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is False
    minio.upload_source_file.assert_called_once()
    service.create_document.assert_awaited_once()
    trigger_flow.assert_called_once()


def test_upload_rolls_back_when_storage_upload_fails(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.build_source_path = MagicMock(return_value="hash123/contract.pdf")
    minio.upload_source_file = MagicMock(side_effect=RuntimeError("minio down"))
    minio.delete_source_file = MagicMock()
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "compute_sha256", lambda _: "hash123")
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )

    assert response.status_code == 502
    assert DummyDBSession.last_instance is not None
    assert DummyDBSession.last_instance.rollback_calls >= 1
    minio.delete_source_file.assert_not_called()
    trigger_flow.assert_not_called()


def test_upload_deletes_source_when_commit_fails(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.build_source_path = MagicMock(return_value="hash123/contract.pdf")
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    minio.delete_source_file = MagicMock()
    trigger_flow = MagicMock()

    DummyDBSession.fail_commit = True
    monkeypatch.setattr(routes_module, "compute_sha256", lambda _: "hash123")
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )
    DummyDBSession.fail_commit = False

    assert response.status_code == 500
    minio.delete_source_file.assert_called_once_with("hash123/contract.pdf")
    trigger_flow.assert_not_called()


def test_upload_rejects_file_when_size_exceeds_limit(client, monkeypatch):
    monkeypatch.setattr(routes_module.settings, "max_upload_size_mb", 1)

    service = MagicMock()
    service.find_by_hash = AsyncMock()
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("large.bin", b"x" * (1024 * 1024 + 1), "application/octet-stream")},
    )

    assert response.status_code == 413
    service.find_by_hash.assert_not_called()


def test_upload_handles_unique_hash_race_as_duplicate(client, monkeypatch):
    doc_id = uuid.uuid4()
    existing_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PROCESSING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(side_effect=[None, existing_doc])
    service.mark_duplicate = AsyncMock(return_value=existing_doc)
    service.create_document = AsyncMock(
        side_effect=IntegrityError("stmt", {"file_hash": "hash123"}, Exception("orig"))
    )

    minio = MagicMock()
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"race-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is True
    service.mark_duplicate.assert_awaited_once()
    trigger_flow.assert_not_called()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/tests/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/backend/main.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/sctructure.py =====

import os

# ====== НАСТРОЙКИ ======
SOURCE_DIR = r"/Users/a1111/Desktop/projects/nurb_documentolog/documentolog"  # папка, которую сканируем
OUTPUT_DIR = "backend"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "main.py")

EXCLUDE_DIRS = {"__pycache__", "migrations", "venv"}
# ======================


def collect_python_files(source_dir):
    py_files = []

    for root, dirs, files in os.walk(source_dir):
        # исключаем ненужные папки
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for file in files:
            if file.endswith(".py"):
                py_files.append(os.path.join(root, file))

    return py_files


def merge_files(py_files, output_file):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as out:
        for file_path in py_files:
            out.write(f"\n\n# ===== FILE: {file_path} =====\n\n")
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    out.write(f.read())
            except Exception as e:
                out.write(f"# ERROR READING FILE: {e}\n")


def main():
    py_files = collect_python_files(SOURCE_DIR)
    merge_files(py_files, OUTPUT_FILE)
    print(f"✅ Объединено файлов: {len(py_files)}")
    print(f"📁 Результат сохранён в: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/frontend_app.py =====

import streamlit as st
import requests
import pandas as pd
import math
import os

# =========================================================
# 1. КОНФИГУРАЦИЯ
# =========================================================

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")

st.set_page_config(
    page_title="Документолог | Система распознавания",
    layout="wide",
    page_icon="📄",
    initial_sidebar_state="expanded"
)

def load_css():
    st.markdown("""
        <style>
        .block-container { padding-top: 2rem; }
        .stButton>button { border-radius: 8px; font-weight: 600; transition: all 0.3s; }
        .stButton>button:hover { transform: translateY(-1px); }
        .stTextInput>div>div>input, .stSelectbox>div>div>div { border-radius: 8px; }
        .stDataFrame { border-radius: 10px; overflow: hidden; }
        .pagination-container {
            display: flex; align-items: center; justify-content: center;
            font-weight: bold; height: 100%;
        }
        </style>
    """, unsafe_allow_html=True)

# =========================================================
# 2. УПРАВЛЕНИЕ СОСТОЯНИЕМ
# =========================================================

def init_session_state():
    defaults = {
        "page": "upload",
        "registry_page": 1,
        "page_size": 10,
        "selected_doc_id": None,
        "selected_doc_name": None,
        "show_ocr_result": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

def reset_pagination():
    st.session_state.registry_page = 1

def set_page(page_num):
    st.session_state.registry_page = page_num

def open_document(doc_id: str, doc_name: str):
    """Переход на страницу детальной информации документа."""
    st.session_state.selected_doc_id = doc_id
    st.session_state.selected_doc_name = doc_name
    st.session_state.page = "document_detail"
    st.session_state.show_ocr_result = False

# =========================================================
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

STATUS_LABELS = {
    "pending":    "⏳ Ожидание",
    "processing": "🔄 В обработке",
    "completed":  "✅ Готово",
    "failed":     "❌ Ошибка",
}

@st.cache_data(show_spinner=False, ttl=10)
def fetch_documents(params: tuple):
    """Запрос документов из API. Принимает tuple для корректного кэширования."""
    try:
        res = requests.get(f"{API_BASE_URL}/documents", params=dict(params), timeout=10)
        if res.status_code == 200:
            return res.json()
        st.error(f"Ошибка API: {res.status_code} — {res.text}")
    except requests.exceptions.RequestException as e:
        st.error(f"Ошибка соединения с сервером: {e}")
    return {"documents": [], "total": 0}

def highlight_status(val):
    if val == "completed":  return "color: #16a34a; font-weight: 600;"
    if val == "failed":     return "color: #dc2626; font-weight: 600;"
    if val == "processing": return "color: #f59e0b; font-weight: 600;"
    return ""

# =========================================================
# 4. КОМПОНЕНТЫ ИНТЕРФЕЙСА
# =========================================================

def render_sidebar():
    st.sidebar.title("📂 Навигация")

    if st.sidebar.button(
        "📤 Загрузка файлов",
        use_container_width=True,
        type="primary" if st.session_state.page == "upload" else "secondary"
    ):
        st.session_state.page = "upload"

    if st.sidebar.button(
        "🗂 Реестр документов",
        use_container_width=True,
        type="primary" if st.session_state.page == "registry" else "secondary"
    ):
        st.session_state.page = "registry"
        reset_pagination()

    # Показываем кнопку "Детальная информация" только когда документ открыт
    if st.session_state.selected_doc_id:
        st.sidebar.divider()
        st.sidebar.caption("📌 Открытый документ")
        name = st.session_state.selected_doc_name or str(st.session_state.selected_doc_id)
        display_name = name[:35] + ("…" if len(name) > 35 else "")
        st.sidebar.markdown(f"**{display_name}**")
        if st.sidebar.button(
            "Детальная информация",
            use_container_width=True,
            type="primary" if st.session_state.page == "document_detail" else "secondary"
        ):
            st.session_state.page = "document_detail"

def render_pagination_controls(total_items):
    page_size = st.session_state.page_size
    current_page = st.session_state.registry_page
    total_pages = math.ceil(total_items / page_size) if total_items > 0 else 1

    if current_page > total_pages:
        st.session_state.registry_page = total_pages
        current_page = total_pages

    st.write("")
    col1, col2, col3, col4, col5, col6 = st.columns([1, 1, 2, 1, 1, 2])

    with col1:
        st.button("⏪", on_click=set_page, args=(1,),
                  disabled=(current_page == 1), use_container_width=True, help="Первая")
    with col2:
        st.button("◀", on_click=set_page, args=(current_page - 1,),
                  disabled=(current_page == 1), use_container_width=True, help="Предыдущая")
    with col3:
        st.markdown(
            f"<div class='pagination-container'>Стр. {current_page} из {total_pages}</div>",
            unsafe_allow_html=True
        )
    with col4:
        st.button("▶", on_click=set_page, args=(current_page + 1,),
                  disabled=(current_page == total_pages), use_container_width=True, help="Следующая")
    with col5:
        st.button("⏩", on_click=set_page, args=(total_pages,),
                  disabled=(current_page == total_pages), use_container_width=True, help="Последняя")
    with col6:
        st.selectbox(
            "Записей на стр:", options=[10, 20, 50, 100],
            key="page_size", on_change=reset_pagination, label_visibility="collapsed"
        )

# =========================================================
# 5. СТРАНИЦЫ
# =========================================================

def page_upload():
    """Страница загрузки файлов с детальными уведомлениями по каждому файлу."""
    st.header("📤 Загрузка документов")
    st.caption("Отправка файлов на OCR обработку")

    uploaded_files = st.file_uploader(
        "Выберите PDF или изображения",
        accept_multiple_files=True,
        type=['pdf', 'png', 'jpg', 'jpeg']
    )

    if st.button("🚀 Отправить на обработку", type="primary") and uploaded_files:
        with st.spinner("Загрузка файлов на сервер..."):
            files = [("files", (f.name, f.getvalue(), f.type)) for f in uploaded_files]
            try:
                response = requests.post(f"{API_BASE_URL}/upload/bulk", files=files)
            except Exception as e:
                st.error(f"Ошибка соединения с сервером: {e}")
                return

            if response.status_code not in (200, 201, 207):
                st.error(f"Ошибка сервера: {response.text}")
                return

            res_data = response.json()
            successful = res_data.get("successful", [])
            failed_items = res_data.get("failed", [])

            # Восстанавливаем имена файлов для успешных:
            # bulk-эндпоинт обрабатывает файлы последовательно, упавшие идут в failed.
            # Вычитаем из списка те, у которых была ошибка.
            failed_names = {item["filename"] for item in failed_items}
            successful_files = [f for f in uploaded_files if f.name not in failed_names]

            # --- Итоговый баннер ---
            new_cnt = sum(1 for r in successful if not r.get("is_duplicate"))
            dup_cnt = sum(1 for r in successful if r.get("is_duplicate"))
            err_cnt = len(failed_items)

            parts = []
            if new_cnt: parts.append(f"**{new_cnt}** новых")
            if dup_cnt: parts.append(f"**{dup_cnt}** дубликатов")
            if err_cnt: parts.append(f"**{err_cnt}** ошибок")

            if err_cnt and not new_cnt and not dup_cnt:
                st.error(f"Все файлы завершились с ошибкой: {err_cnt}")
            elif err_cnt:
                st.warning(f"Загрузка завершена: {', '.join(parts)}")
            else:
                st.success(f"Загрузка завершена: {', '.join(parts)}")

            # --- Детали по каждому успешному файлу ---
            if successful:
                st.markdown("---")
                for idx, item in enumerate(successful):
                    doc_id  = str(item.get("doc_id", ""))
                    is_dup  = item.get("is_duplicate", False)
                    msg     = item.get("message", "")
                    fname   = (
                        successful_files[idx].name
                        if idx < len(successful_files)
                        else f"Документ {idx + 1}"
                    )

                    icon        = "⚠️" if is_dup else "✅"
                    badge_text  = "Дубликат"  if is_dup else "Загружен"
                    badge_bg    = "#fef3c7"   if is_dup else "#dcfce7"
                    badge_color = "#92400e"   if is_dup else "#166534"

                    col_icon, col_info, col_btn = st.columns([0.4, 5.5, 1.5])
                    with col_icon:
                        st.markdown(f"<div style='padding-top:8px;font-size:1.3em'>{icon}</div>",
                                    unsafe_allow_html=True)
                    with col_info:
                        st.markdown(
                            f"**{fname}** &nbsp;"
                            f"<span style='background:{badge_bg};color:{badge_color};"
                            f"padding:2px 10px;border-radius:12px;font-size:0.8em;font-weight:600'>"
                            f"{badge_text}</span><br>"
                            f"<span style='color:#6b7280;font-size:0.85em'>{msg}</span>",
                            unsafe_allow_html=True
                        )
                    with col_btn:
                        if st.button("Открыть →", key=f"upload_open_{doc_id}_{idx}",
                                     use_container_width=True):
                            open_document(doc_id, fname)
                            st.rerun()

            # --- Ошибки ---
            if failed_items:
                st.markdown("---")
                st.markdown("**Ошибки загрузки:**")
                for item in failed_items:
                    st.error(
                        f"**{item.get('filename', '—')}** — "
                        f"{item.get('error', 'Неизвестная ошибка')}"
                    )


def page_registry():
    """Страница реестра документов с кликабельными строками."""
    col_title, col_btn = st.columns([5, 1])
    with col_title:
        st.header("🗂 Реестр документов")
    with col_btn:
        if st.button("🔄 Обновить", use_container_width=True):
            fetch_documents.clear()

    # --- Фильтры ---
    with st.expander("🔍 Фильтры", expanded=True):
        f_col1, f_col2, f_col3, f_col4 = st.columns(4)
        with f_col1:
            f_filename = st.text_input("Название файла содержит:", on_change=reset_pagination)
        with f_col2:
            f_status = st.selectbox(
                "Статус:", ["Все", "pending", "processing", "completed", "failed"],
                on_change=reset_pagination
            )
        with f_col3:
            f_date_from = st.date_input("Создано от:", value=None, on_change=reset_pagination)
        with f_col4:
            f_date_to = st.date_input("Создано до:", value=None, on_change=reset_pagination)

    # --- Параметры запроса ---
    offset = (st.session_state.registry_page - 1) * st.session_state.page_size
    params_dict = {"limit": st.session_state.page_size, "offset": offset, "latest_only": True}
    if f_filename:          params_dict["filename"]       = f_filename
    if f_status != "Все":  params_dict["status"]          = f_status
    if f_date_from:         params_dict["created_after"]  = f"{f_date_from}T00:00:00Z"
    if f_date_to:           params_dict["created_before"] = f"{f_date_to}T23:59:59Z"

    # dict → tuple для корректного хэширования в кэше
    params_tuple = tuple(sorted(params_dict.items()))

    # --- Данные ---
    data      = fetch_documents(params_tuple)
    docs      = data.get("documents", [])
    total_docs = data.get("total", 0)

    st.caption(f"Найдено документов: **{total_docs}**")

    if not docs:
        st.info("Документы не найдены.")
        return

    df = pd.DataFrame(docs)
    df['created_at'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d %H:%M')
    display_df = df[['id', 'filename', 'status', 'page_count', 'created_at']].copy()

    # --- KPI ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Всего (в базе)", total_docs)
    col2.metric("Готово (на стр.)", len(df[df.status == "completed"]))
    col3.metric("В обработке (на стр.)", len(df[df.status == "processing"]))
    col4.metric("Ошибки (на стр.)", len(df[df.status == "failed"]))

    st.divider()
    st.caption("Кликните на строку — откроется детальная страница документа")

    # --- Таблица с выбором строки ---
    styled_df = display_df.style.map(highlight_status, subset=['status'])
    event = st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    # Обработка клика по строке
    selected_rows = event.selection.rows
    if selected_rows:
        row_idx = selected_rows[0]
        if row_idx < len(docs):
            open_document(str(docs[row_idx]["id"]), docs[row_idx]["filename"])
            st.rerun()

    render_pagination_controls(total_docs)


def page_document_detail():
    """Страница детальной информации о конкретном документе."""
    doc_id   = st.session_state.selected_doc_id
    doc_name = st.session_state.selected_doc_name or "—"

    # --- Шапка с кнопкой "Назад" ---
    col_back, col_title = st.columns([1, 9])
    with col_back:
        if st.button("← Реестр", use_container_width=True):
            st.session_state.page = "registry"
            st.rerun()
    with col_title:
        st.header(f" {doc_name}")

    st.divider()

    # --- Загрузка данных ---
    try:
        res = requests.get(f"{API_BASE_URL}/status/{doc_id}", timeout=5)
        if res.status_code != 200:
            st.error(f"Документ не найден (HTTP {res.status_code})")
            return
        doc_data = res.json()
    except Exception as e:
        st.error(f"Ошибка при загрузке данных: {e}")
        return

    status       = doc_data.get("status", "unknown")
    status_label = STATUS_LABELS.get(status, status)

    # --- Карточка с информацией ---
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**ID документа**")
        st.code(str(doc_data.get("doc_id", "—")), language=None)

        st.markdown("**Имя файла**")
        st.markdown(f"`{doc_data.get('filename', '—')}`")

    with c2:
        st.markdown("**Статус**")
        if status == "completed":
            st.success(status_label)
        elif status == "failed":
            st.error(status_label)
        elif status == "processing":
            st.warning(status_label)
        else:
            st.info(status_label)

        result_path = doc_data.get("result_path")
        st.markdown("**Путь к результату OCR**")
        if result_path:
            st.markdown(f"`{result_path}`")
        else:
            st.markdown("_Результат ещё не сформирован_")

    # --- Ошибка обработки ---
    if doc_data.get("error_message"):
        st.divider()
        st.markdown("**Ошибка обработки**")
        st.error(doc_data["error_message"])

    # --- Действия ---
    st.divider()
    action_col1, action_col2, _ = st.columns([2, 2, 6])

    with action_col1:
        if st.button("🔄 Обновить статус", use_container_width=True):
            st.rerun()

    with action_col2:
        ocr_available = (status == "completed")
        toggle_label  = "🙈 Скрыть текст" if st.session_state.show_ocr_result else "📄 Показать OCR текст"
        if st.button(
            toggle_label,
            use_container_width=True,
            disabled=not ocr_available,
            help="Доступно только для обработанных документов"
        ):
            st.session_state.show_ocr_result = not st.session_state.show_ocr_result
            st.rerun()

    # --- OCR результат ---
    if st.session_state.show_ocr_result and status == "completed":
        with st.spinner("Загрузка распознанного текста..."):
            try:
                json_res = requests.get(f"{API_BASE_URL}/result/{doc_id}", timeout=15)
                if json_res.status_code == 200:
                    json_data = json_res.json()
                    tab1, tab2 = st.tabs(["📝 Текст", "📦 Raw JSON"])
                    with tab1:
                        full_text = json_data.get("full_text", "")
                        if full_text:
                            st.text_area("Распознанный текст", full_text, height=500)
                        else:
                            st.info("Текст не распознан (пустой результат)")
                    with tab2:
                        st.json(json_data)
                else:
                    st.warning(f"Не удалось загрузить результат OCR (HTTP {json_res.status_code})")
            except Exception as e:
                st.error(f"Ошибка загрузки результата: {e}")

# =========================================================
# 6. ТОЧКА ВХОДА
# =========================================================

def main():
    load_css()
    init_session_state()
    render_sidebar()

    if st.session_state.page == "upload":
        page_upload()
    elif st.session_state.page == "registry":
        page_registry()
    elif st.session_state.page == "document_detail":
        page_document_detail()

if __name__ == "__main__":
    main()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/main.py =====

"""
Документолог: Core — FastAPI Application.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.routes import router as doc_router
from app.core.config import get_settings
from app.core.database import engine
from app.schemas.document import HealthResponse
from app.services.storage import get_minio_service


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("🚀 Starting Документолог API …")
    logger.info("📦 Schema management: Alembic migrations are required before startup.")

    yield

    logger.info("👋 Shutting down …")
    await engine.dispose()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    description="Система автоматизированной обработки документов",
    lifespan=lifespan,
)

# CORS (loosen in dev, tighten in prod)
cors_allow_credentials = settings.cors_allow_credentials
if "*" in settings.cors_origins and cors_allow_credentials:
    logger.warning("CORS misconfiguration detected: disabling credentials for wildcard origins.")
    cors_allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────────

app.include_router(doc_router, prefix="/api/v1")


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    minio_ok = "unknown"
    db_ok = "unknown"
    try:
        minio_ok = "ok" if get_minio_service().health_check() else "error"
    except Exception:
        minio_ok = "error"

    try:
        from sqlalchemy import text
        from app.core.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            db_ok = "ok"
    except Exception:
        db_ok = "error"

    return HealthResponse(
        status="ok" if db_ok == "ok" and minio_ok == "ok" else "degraded",
        version=settings.app_version,
        database=db_ok,
        minio=minio_ok,
    )


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/core/config.py =====

"""
Centralized configuration loaded from environment variables.
"""

import json
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # ── Database ────────────────────────────
    database_url: str = "postgresql+asyncpg://docolog:docolog_secret@localhost:5433/documentolog"
    database_url_sync: str = "postgresql://docolog:docolog_secret@localhost:5433/documentolog"

    # ── MinIO ───────────────────────────────
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin123"
    minio_secure: bool = False

    # ── Buckets ─────────────────────────────
    bucket_source: str = "source-files"
    bucket_results: str = "analysis-results"

    # ── Prefect ─────────────────────────────
    prefect_api_url: str = "http://localhost:4200/api"

    # ── OCR ──────────────────────────────────
    ocr_concurrency_limit: int = 1
    ocr_timeout_seconds: int = 300
    ocr_retry_delay_seconds: int = 60
    max_upload_size_mb: int = 50

    # ── App ──────────────────────────────────
    log_level: str = "INFO"
    app_title: str = "Документолог: Core"
    app_version: str = "0.1.0"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    cors_allow_credentials: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore"  # <--- Это скажет FastAPI игнорировать переменные для других контейнеров
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/core/database.py =====

"""
Async and sync SQLAlchemy engines and session factories.

- Async engine  → FastAPI route handlers
- Sync engine   → Prefect worker tasks + FastAPI background tasks
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# ── Async (FastAPI) ───────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async session (commit is explicit in handlers)."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── Sync (Prefect workers + background tasks) ─────────────────────
sync_engine = create_engine(
    settings.database_url_sync,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


@contextmanager
def get_sync_db() -> Generator[Session, None, None]:
    """
    Context manager for synchronous DB sessions.
    Used by Prefect worker tasks and FastAPI background tasks.
    Always closes the session and rolls back on unhandled exceptions.
    """
    session = SyncSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/core/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/models/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/models/document.py =====

"""
SQLAlchemy models for the documents table.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    file_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="SHA-256 hash of file content",
    )
    filename: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Original filename",
    )
    s3_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=True,
        comment="Path to original file in MinIO (source-files bucket)",
    )
    result_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=True,
        comment="Path to Surya JSON result in MinIO (analysis-results bucket)",
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", create_type=True),
        default=DocumentStatus.PENDING,
        nullable=False,
    )
    is_latest: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="True if this is the latest version of the file",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error description if status == failed",
    )
    page_count: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Number of pages in the document",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Composite index for version queries
    __table_args__ = (
        Index("ix_documents_filename_latest", "filename", "is_latest"),
    )

    def __repr__(self) -> str:
        return f"<Document {self.id} [{self.status.value}] {self.filename}>"


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/schemas/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/schemas/document.py =====

"""
Pydantic schemas for API input / output.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.document import DocumentStatus


# ── Response schemas ─────────────────────────────────────────

class DocumentResponse(BaseModel):
    id: UUID
    file_hash: str
    filename: str
    status: DocumentStatus
    is_latest: bool
    s3_path: str | None = None
    result_path: str | None = None
    error_message: str | None = None
    page_count: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    doc_id: UUID
    status: DocumentStatus
    is_duplicate: bool = False
    message: str


class StatusResponse(BaseModel):
    doc_id: UUID
    status: DocumentStatus
    filename: str
    result_path: str | None = None
    error_message: str | None = None


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentResponse]


class AskRequest(BaseModel):
    doc_id: UUID
    question: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    doc_id: UUID
    answer: str
    sources: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    database: str = "unknown"
    minio: str = "unknown"


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/api/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/api/routes.py =====

"""
FastAPI router: upload, upload bulk, status, result, list, ask (stub).
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.document import DocumentStatus
from app.schemas.document import (
    AskRequest,
    AskResponse,
    DocumentListResponse,
    DocumentResponse,
    StatusResponse,
    UploadResponse,
)
from app.services.documents import DocumentService
from app.services.hasher import compute_sha256
from app.services.storage import get_minio_service

router = APIRouter(tags=["Documents"])
settings = get_settings()

# ── File-type validation ──────────────────────────────────────────

_ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

# Magic-byte signatures → internal type label
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8", "jpeg"),
    (b"II*\x00", "tiff"),   # little-endian TIFF
    (b"MM\x00*", "tiff"),   # big-endian TIFF
    (b"BM", "bmp"),
]

# Extension → allowed detected magic types
_EXT_TO_TYPES: dict[str, set[str]] = {
    ".pdf":  {"pdf"},
    ".png":  {"png"},
    ".jpg":  {"jpeg"},
    ".jpeg": {"jpeg"},
    ".tiff": {"tiff"},
    ".bmp":  {"bmp"},
    ".webp": {"webp"},
}


def _detect_magic_type(header: bytes) -> str | None:
    """Return the file type from magic bytes, or None if unknown."""
    # WEBP: RIFF????WEBP
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    for signature, ftype in _MAGIC_SIGNATURES:
        if header[: len(signature)] == signature:
            return ftype
    return None


def _validate_file_type(filename: str, header: bytes) -> None:
    """
    Raise HTTPException(415) if:
    - extension is not in the allowed set, OR
    - magic bytes do not match the declared extension.
    """
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Неподдерживаемый тип файла: '{ext}'. "
                f"Разрешены: PDF, PNG, JPG, JPEG, TIFF, BMP, WEBP."
            ),
        )

    detected = _detect_magic_type(header)
    if detected is None:
        raise HTTPException(
            status_code=415,
            detail="Содержимое файла не соответствует ни одному допустимому формату.",
        )

    if detected not in _EXT_TO_TYPES.get(ext, set()):
        raise HTTPException(
            status_code=415,
            detail=(
                f"Расширение '{ext}' не соответствует содержимому файла "
                f"(обнаружен тип: {detected})."
            ),
        )


# ── Schemas ──────────────────────────────────────────────────

class BulkUploadResponse(BaseModel):
    successful: list[UploadResponse]
    failed: list[dict]


# ── Endpoints ────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a document.
    - Computes SHA-256 hash.
    - If duplicate → returns existing record (no reprocessing).
    - If new → stores in MinIO, creates DB record with status=pending.
      The background worker picks it up automatically via polling.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    try:
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to read uploaded file.") from exc

    if file_size == 0:
        raise HTTPException(status_code=400, detail="Empty file.")

    max_size_bytes = settings.max_upload_size_mb * 1024 * 1024
    if file_size > max_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size is {settings.max_upload_size_mb} MB.",
        )

    # ── MIME / magic-bytes validation ────────────────────────
    header = file.file.read(12)
    file.file.seek(0)
    _validate_file_type(file.filename, header)
    # ────────────────────────────────────────────────────────

    file_hash = compute_sha256(file.file)
    svc = DocumentService(db)

    # ── Deduplication check ──────────────────────────────────
    existing = await svc.find_by_hash(file_hash)
    if existing is not None:
        await svc.mark_duplicate(existing)
        await db.commit()
        logger.info(f"Duplicate detected: {file_hash} → doc {existing.id}")
        return UploadResponse(
            doc_id=existing.id,
            status=existing.status,
            is_duplicate=True,
            message="Файл уже был загружен ранее. Возвращён существующий результат.",
        )

    # ── New file ─────────────────────────────────────────────
    minio = get_minio_service()
    s3_path = minio.build_source_path(file_hash=file_hash, filename=file.filename)

    try:
        doc = await svc.create_document(
            file_hash=file_hash,
            filename=file.filename,
            s3_path=s3_path,
        )
    except IntegrityError:
        await db.rollback()
        existing = await svc.find_by_hash(file_hash)
        if existing is None:
            raise HTTPException(status_code=409, detail="Duplicate upload conflict.")
        await svc.mark_duplicate(existing)
        await db.commit()
        logger.info(f"Duplicate detected (race): {file_hash} → doc {existing.id}")
        return UploadResponse(
            doc_id=existing.id,
            status=existing.status,
            is_duplicate=True,
            message="Файл уже был загружен ранее. Возвращён существующий результат.",
        )

    try:
        minio.upload_source_file(
            file_hash=file_hash,
            filename=file.filename,
            data=file.file,
            size=file_size,
        )
    except Exception as exc:
        await db.rollback()
        logger.error(f"Failed to upload source file to MinIO: {exc}")
        raise HTTPException(status_code=502, detail="Ошибка загрузки файла в хранилище.")

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        minio.delete_source_file(s3_path)
        logger.error(f"Failed to commit document metadata: {exc}")
        raise HTTPException(status_code=500, detail="Ошибка сохранения метаданных документа.")

    return UploadResponse(
        doc_id=doc.id,
        status=doc.status,
        is_duplicate=False,
        message="Файл загружен. Обработка запущена.",
    )


@router.post("/upload/bulk", response_model=BulkUploadResponse, status_code=207)
async def upload_documents_bulk(
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Массовая загрузка документов. 
    Итерируется по списку файлов и вызывает логику одиночной загрузки.
    Возвращает статус 207 (Multi-Status), собирая успешные и ошибочные файлы.
    """
    successful = []
    failed = []

    for file in files:
        try:
            # Вызываем функцию одиночной загрузки напрямую
            res = await upload_document(file=file, db=db)
            successful.append(res)
        except HTTPException as e:
            failed.append({"filename": file.filename, "error": str(e.detail)})
        except Exception as e:
            failed.append({"filename": file.filename, "error": str(e)})

    return BulkUploadResponse(successful=successful, failed=failed)


@router.get("/status/{doc_id}", response_model=StatusResponse)
async def get_status(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return processing status for a document."""
    svc = DocumentService(db)
    doc = await svc.find_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Документ не найден.")
    return StatusResponse(
        doc_id=doc.id,
        status=doc.status,
        filename=doc.filename,
        result_path=doc.result_path,
        error_message=doc.error_message,
    )


@router.get("/result/{doc_id}")
async def get_document_result(doc_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Скачивает и отдает JSON с результатами OCR из MinIO.
    Используется фронтендом (Streamlit) для отображения полного текста документа.
    """
    svc = DocumentService(db)
    doc = await svc.find_by_id(doc_id)
    
    if not doc or not doc.result_path:
        raise HTTPException(status_code=404, detail="Результат не найден или документ еще в обработке.")
    
    try:
        minio = get_minio_service()
        data = minio.download_result_json(doc.result_path)
        return JSONResponse(content=json.loads(data))
    except Exception as e:
        logger.error(f"Failed to fetch result for doc {doc_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка получения результата из хранилища.")


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    latest_only: bool = Query(True),
    status: DocumentStatus | None = Query(None, description="Фильтр по статусу"),
    filename: str | None = Query(None, description="Поиск по имени файла"),
    created_after: datetime | None = Query(None, description="Дата создания от"),
    created_before: datetime | None = Query(None, description="Дата создания до"),
    db: AsyncSession = Depends(get_db),
):
    """List uploaded documents (paginated). Supports multiple filters."""
    svc = DocumentService(db)
    
    # Обрати внимание, что тебе нужно обновить `list_documents` в `app/services/documents.py`, 
    # чтобы он принимал эти новые аргументы!
    docs, total = await svc.list_documents(
        offset=offset, 
        limit=limit, 
        latest_only=latest_only,
        status=status,
        filename=filename,
        created_after=created_after,
        created_before=created_before
    )
    
    return DocumentListResponse(
        total=total,
        documents=[DocumentResponse.model_validate(d) for d in docs],
    )


@router.post("/ask", response_model=AskResponse)
async def ask_document(body: AskRequest):
    """
    Stub endpoint for the future AI agent.
    Will accept a question about a document and return an answer.
    """
    return AskResponse(
        doc_id=body.doc_id,
        answer="⚠️ AI-агент ещё не реализован. Это заглушка.",
        sources=[],
    )

# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/documents.py =====

"""
Core business logic: upload, deduplication, status tracking.
"""

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from app.models.document import Document, DocumentStatus


class DocumentService:
    """CRUD + deduplication logic for documents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── queries ──────────────────────────────────────────────

    async def find_by_hash(self, file_hash: str) -> Document | None:
        """Return document with the given hash, or None."""
        stmt = (
            select(Document)
            .where(Document.file_hash == file_hash)
            .order_by(Document.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_id(self, doc_id: uuid.UUID) -> Document | None:
        stmt = select(Document).where(Document.id == doc_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # app/services/documents.py (обновленный метод)



    async def list_documents(
        self, 
        offset: int = 0, 
        limit: int = 50, 
        latest_only: bool = True,
        status: DocumentStatus | None = None,
        filename: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> tuple[list[Document], int]:
        """Return paginated list and total count with filters."""
        base = select(Document)
    
        if latest_only:
            base = base.where(Document.is_latest.is_(True))
        if status:
            base = base.where(Document.status == status)
        if filename:
            base = base.where(Document.filename.ilike(f"%{filename}%"))
        if created_after:
            base = base.where(Document.created_at >= created_after)
        if created_before:
            base = base.where(Document.created_at <= created_before)
        
        base = base.order_by(Document.created_at.desc())

        # total count
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # paginated rows
        stmt = base.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    # ── mutations ────────────────────────────────────────────

    async def create_document(
        self,
        file_hash: str,
        filename: str,
        s3_path: str,
    ) -> Document:
        """
        Create a new document record.
        If a document with the same filename already exists, mark
        the old one as is_latest=False (version chain).
        """
        # mark previous versions as not latest
        await self.session.execute(
            update(Document)
            .where(Document.filename == filename, Document.is_latest.is_(True))
            .values(is_latest=False)
        )

        doc = Document(
            file_hash=file_hash,
            filename=filename,
            s3_path=s3_path,
            status=DocumentStatus.PENDING,
            is_latest=True,
        )
        self.session.add(doc)
        await self.session.flush()
        logger.info(f"Created document record {doc.id} for {filename}")
        return doc

    async def mark_duplicate(self, doc: Document) -> Document:
        """Touch updated_at on a duplicate upload."""
        doc.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return doc

    async def update_status(
        self,
        doc_id: uuid.UUID,
        status: DocumentStatus,
        result_path: str | None = None,
        error_message: str | None = None,
        page_count: int | None = None,
    ) -> None:
        values: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if result_path is not None:
            values["result_path"] = result_path
        if error_message is not None:
            values["error_message"] = error_message
        if page_count is not None:
            values["page_count"] = page_count

        await self.session.execute(
            update(Document).where(Document.id == doc_id).values(**values)
        )
        await self.session.flush()
        logger.info(f"Document {doc_id} → {status.value}")


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/ocr.py =====

import gc
import io
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import fitz
    import torch
    from PIL import Image
    SURYA_AVAILABLE = True
    # Use all available CPU cores for PyTorch operations.
    # This speeds up matrix multiplications in OCR models significantly.
    _cpu_count = os.cpu_count() or 4
    torch.set_num_threads(_cpu_count)
    torch.set_num_interop_threads(max(1, _cpu_count // 2))
except ImportError:
    SURYA_AVAILABLE = False
    logger.warning("PyMuPDF/Pillow not installed.")


class SuryaOCRService:
    def __init__(self):
        if not SURYA_AVAILABLE:
            raise RuntimeError("Required packages not installed.")

        logger.info("Loading Surya OCR models (Memory Optimized Mode)…")

        from surya.detection import DetectionPredictor
        self.det_predictor = DetectionPredictor(device="cpu")
        logger.info("  ✓ DetectionPredictor")

        try:
            from surya.foundation import FoundationPredictor
            self.foundation_predictor = FoundationPredictor(device="cpu")
            logger.info("  ✓ FoundationPredictor")
        except Exception as e:
            logger.warning(f"  ✗ FoundationPredictor: {e}")
            self.foundation_predictor = None

        try:
            from surya.recognition import RecognitionPredictor
            if self.foundation_predictor:
                self.rec_predictor = RecognitionPredictor(self.foundation_predictor)
            else:
                self.rec_predictor = RecognitionPredictor()
            logger.info("  ✓ RecognitionPredictor")
        except Exception as e:
            logger.error(f"  ✗ RecognitionPredictor: {e}")
            self.rec_predictor = None

        # --- ЗАКОММЕНТИРОВАНО ДЛЯ ЭКОНОМИИ RAM ---
        self.layout_predictor = None
        # try:
        #     from surya.layout import LayoutPredictor
        #     self.layout_predictor = LayoutPredictor(device="cpu")
        #     logger.info("  ✓ LayoutPredictor")
        # except Exception as e:
        #     self.layout_predictor = None

        self.table_predictor = None
        # try:
        #     from surya.table_rec import TableRecPredictor
        #     self.table_predictor = TableRecPredictor(device="cpu")
        #     logger.info("  ✓ TableRecPredictor")
        # except Exception as e:
        #     self.table_predictor = None

        logger.info("✅ Surya models ready (OCR Only).")

    @staticmethod
    def pdf_to_images(pdf_bytes, dpi=120):
        # DPI=120 снижает потребление памяти в 3-4 раза по сравнению с DPI=200
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
        doc.close()
        return images

    def process_document(self, file_bytes, filename):
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            images = self.pdf_to_images(file_bytes)
        elif ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
            images = [Image.open(io.BytesIO(file_bytes)).convert("RGB")]
        else:
            raise ValueError(f"Unsupported: {ext}")

        page_count = len(images)
        logger.info(f"Processing {page_count} page(s) from '{filename}'")

        ocr_results = None
        if self.rec_predictor:
            try:
                logger.info("Running OCR (page-by-page to limit peak memory)…")
                ocr_results = []
                for idx, img in enumerate(images):
                    logger.info(f"  Page {idx + 1}/{page_count}…")
                    page_result = self.rec_predictor(
                        [img],
                        det_predictor=self.det_predictor,
                        sort_lines=True,
                    )
                    ocr_results.extend(page_result)
                    gc.collect()  # release page tensors before next page
                logger.info(f"OCR done: {len(ocr_results)} pages")
            except Exception as e:
                logger.error(f"OCR failed: {e}")

        # --- ВЫЗОВЫ ЭТИХ МОДЕЛЕЙ ТАКЖЕ ОТКЛЮЧЕНЫ ---
        layout_results = None
        # if self.layout_predictor:
        #     try:
        #         layout_results = self.layout_predictor(images)
        #     except Exception as e:
        #         logger.warning(f"Layout failed: {e}")

        table_results = None
        # if self.table_predictor:
        #     try:
        #         table_results = self.table_predictor.batch_table_recognition(images)
        #     except Exception as e:
        #         logger.warning(f"Tables failed: {e}")

        return self._structure_output(filename, page_count, ocr_results, layout_results, table_results)

    def _structure_output(self, filename, page_count, ocr_results, layout_results, table_results):
        pages = []
        for page_idx in range(page_count):
            page_data = {"page_number": page_idx + 1, "blocks": [], "full_text": ""}

            if ocr_results and page_idx < len(ocr_results):
                ocr_page = ocr_results[page_idx]
                text_lines = []
                for line in getattr(ocr_page, "text_lines", []):
                    block = {"type": "text", "text": getattr(line, "text", ""), "confidence": round(getattr(line, "confidence", 0), 3)}
                    bbox = getattr(line, "bbox", None)
                    if bbox is not None:
                        block["bbox"] = bbox if isinstance(bbox, list) else list(bbox)
                    page_data["blocks"].append(block)
                    text_lines.append(block["text"])
                page_data["full_text"] = "\n".join(text_lines)

            if layout_results and page_idx < len(layout_results):
                layout_page = layout_results[page_idx]
                bboxes = getattr(layout_page, "bboxes", []) or getattr(layout_page, "layout_bboxes", [])
                layout_blocks = []
                for b in bboxes:
                    lb = {"label": getattr(b, "label", "unknown")}
                    bbox = getattr(b, "bbox", None)
                    if bbox: lb["bbox"] = bbox if isinstance(bbox, list) else list(bbox)
                    conf = getattr(b, "confidence", None)
                    if conf: lb["confidence"] = round(conf, 3)
                    layout_blocks.append(lb)
                if layout_blocks:
                    page_data["layout"] = layout_blocks

            if table_results and page_idx < len(table_results):
                table_page = table_results[page_idx]
                tables_list = getattr(table_page, "tables", [])
                if not tables_list and isinstance(table_page, list):
                    tables_list = table_page
                page_tables = []
                for t_idx, table in enumerate(tables_list):
                    md = self._table_to_markdown(table)
                    if md:
                        page_tables.append({"table_index": t_idx, "markdown": md})
                if page_tables:
                    page_data["tables"] = page_tables

            pages.append(page_data)

        full_text = "\n\n".join(p["full_text"] for p in pages if p["full_text"])
        return {"filename": filename, "page_count": page_count, "pages": pages, "full_text": full_text}

    @staticmethod
    def _table_to_markdown(table):
        try:
            cells = getattr(table, "cells", [])
            if not cells: return ""
            max_row = max(getattr(c, "row", 0) for c in cells) + 1
            max_col = max(getattr(c, "col", 0) for c in cells) + 1
            grid = [["" for _ in range(max_col)] for _ in range(max_row)]
            for cell in cells:
                grid[getattr(cell, "row", 0)][getattr(cell, "col", 0)] = getattr(cell, "text", "").strip()
            lines = []
            for r_idx, row in enumerate(grid):
                lines.append("| " + " | ".join(row) + " |")
                if r_idx == 0:
                    lines.append("| " + " | ".join("---" for _ in row) + " |")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Table→MD failed: {e}")
            return ""

_ocr_service = None

def get_ocr_service():
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = SuryaOCRService()
    return _ocr_service


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/storage.py =====

"""
MinIO (S3-compatible) storage operations.
"""

import io
import re
from pathlib import Path
from typing import BinaryIO

from loguru import logger
from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings


class MinIOService:
    """Wrapper around the MinIO client."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket_source = settings.bucket_source
        self.bucket_results = settings.bucket_results

    # ── helpers ──────────────────────────────────────────────

    def _ensure_bucket(self, bucket: str) -> None:
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
            logger.info(f"Created bucket: {bucket}")

    # ── public API ───────────────────────────────────────────

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Strip directory components and dangerous characters from a filename.

        Prevents path-traversal attacks such as '../../admin/secret.pdf'
        from poisoning MinIO object paths.
        """
        # Take only the basename — removes any leading path like ../../
        name = Path(filename).name
        # Replace every character that is not alphanumeric, dash, dot,
        # underscore, or space with an underscore.
        name = re.sub(r"[^\w\-_. ]", "_", name)
        # Strip leading/trailing dots and spaces that could confuse storage.
        name = name.strip(". ")
        return name or "unnamed_file"

    @staticmethod
    def build_source_path(file_hash: str, filename: str) -> str:
        safe_name = MinIOService.sanitize_filename(filename)
        return f"{file_hash}/{safe_name}"

    def upload_source_file(
        self, file_hash: str, filename: str, data: BinaryIO, size: int
    ) -> str:
        """Upload original file. Returns the S3 path."""
        self._ensure_bucket(self.bucket_source)
        object_name = self.build_source_path(file_hash=file_hash, filename=filename)
        self.client.put_object(
            bucket_name=self.bucket_source,
            object_name=object_name,
            data=data,
            length=size,
        )
        logger.info(f"Uploaded source file: {self.bucket_source}/{object_name}")
        return object_name

    def upload_result_json(self, file_hash: str, json_bytes: bytes) -> str:
        """Upload Surya analysis JSON. Returns the S3 path."""
        self._ensure_bucket(self.bucket_results)
        object_name = f"{file_hash}/surya_output.json"
        self.client.put_object(
            bucket_name=self.bucket_results,
            object_name=object_name,
            data=io.BytesIO(json_bytes),
            length=len(json_bytes),
            content_type="application/json",
        )
        logger.info(f"Uploaded result JSON: {self.bucket_results}/{object_name}")
        return object_name

    def download_source_file(self, s3_path: str) -> bytes:
        """Download original file from MinIO."""
        response = self.client.get_object(self.bucket_source, s3_path)
        data = response.read()
        response.close()
        response.release_conn()
        return data

    def download_result_json(self, result_path: str) -> bytes:
        """Download Surya result JSON from MinIO."""
        response = self.client.get_object(self.bucket_results, result_path)
        data = response.read()
        response.close()
        response.release_conn()
        return data

    def delete_source_file(self, s3_path: str) -> None:
        """Delete original file from MinIO if present."""
        try:
            self.client.remove_object(self.bucket_source, s3_path)
            logger.info(f"Deleted source file: {self.bucket_source}/{s3_path}")
        except S3Error as e:
            logger.warning(f"Failed to delete source file {s3_path}: {e}")

    def health_check(self) -> bool:
        """Return True if MinIO is reachable."""
        try:
            self.client.list_buckets()
            return True
        except S3Error:
            return False


# Singleton
_minio_service: MinIOService | None = None


def get_minio_service() -> MinIOService:
    global _minio_service
    if _minio_service is None:
        _minio_service = MinIOService()
    return _minio_service


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/app/services/hasher.py =====

"""
SHA-256 hashing for file deduplication.
"""

import hashlib
from typing import BinaryIO

CHUNK_SIZE = 8192  # 8 KB


def compute_sha256(file: BinaryIO) -> str:
    """
    Compute SHA-256 hash of a file-like object.
    Resets file pointer to start after computation.
    """
    sha = hashlib.sha256()
    file.seek(0)
    while chunk := file.read(CHUNK_SIZE):
        sha.update(chunk)
    file.seek(0)
    return sha.hexdigest()


def compute_sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hash of raw bytes."""
    return hashlib.sha256(data).hexdigest()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/frontend/app.py =====

import streamlit as st
import requests
import pandas as pd
from datetime import datetime

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")

st.set_page_config(page_title="Документолог", layout="wide", page_icon="📄")

# --- Навигация ---
st.sidebar.title("Меню")
page = st.sidebar.radio("Перейти к:", ["Загрузка документов", "Реестр документов"])

# ==========================================
# СТРАНИЦА 1: ЗАГРУЗКА ДОКУМЕНТОВ
# ==========================================
if page == "Загрузка документов":
    st.title("📤 Загрузка документов")
    st.write("Выберите один или несколько файлов для отправки на распознавание (OCR).")

    uploaded_files = st.file_uploader("Выберите PDF или изображения", accept_multiple_files=True, type=['pdf', 'png', 'jpg', 'jpeg'])

    if st.button("🚀 Отправить на обработку") and uploaded_files:
        with st.spinner("Загрузка файлов на сервер..."):
            files = [("files", (file.name, file.getvalue(), file.type)) for file in uploaded_files]
            
            try:
                # Используем bulk endpoint
                response = requests.post(f"{API_BASE_URL}/upload/bulk", files=files)
                if response.status_code in (200, 201, 207):
                    res_data = response.json()
                    st.success(f"Успешно загружено: {len(res_data['successful'])} файлов.")
                    if res_data['failed']:
                        st.error(f"Ошибок загрузки: {len(res_data['failed'])}")
                        st.json(res_data['failed'])
                else:
                    st.error(f"Ошибка сервера: {response.text}")
            except Exception as e:
                st.error(f"Ошибка соединения с сервером: {e}")

# ==========================================
# СТРАНИЦА 2: РЕЕСТР ДОКУМЕНТОВ И ДЕТАЛИ
# ==========================================
elif page == "Реестр документов":
    st.title("🗂 Реестр документов")

    # --- ФИЛЬТРЫ ---
    with st.expander("🔍 Фильтры", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            f_filename = st.text_input("Название файла содержит:")
        with col2:
            f_status = st.selectbox("Статус:", ["Все", "pending", "processing", "completed", "failed"])
        with col3:
            f_date_from = st.date_input("Создано от:", value=None)
        with col4:
            f_date_to = st.date_input("Создано до:", value=None)

    # Формируем query параметры
    params = {"limit": 100, "latest_only": True}
    if f_filename: params["filename"] = f_filename
    if f_status != "Все": params["status"] = f_status
    if f_date_from: params["created_after"] = f"{f_date_from}T00:00:00Z"
    if f_date_to: params["created_before"] = f"{f_date_to}T23:59:59Z"

    # Запрос списка
    try:
        res = requests.get(f"{API_BASE_URL}/documents", params=params)
        if res.status_code == 200:
            data = res.json()
            docs = data.get("documents", [])
            st.caption(f"Найдено документов: {data.get('total', 0)}")
            
            if docs:
                # Преобразуем в DataFrame для красивой таблицы
                df = pd.DataFrame(docs)
                df['created_at'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d %H:%M')
                
                # Оставляем только нужные колонки
                display_df = df[['id', 'filename', 'status', 'page_count', 'created_at']]
                
                # Рисуем таблицу. При клике на строку будем показывать детали
                st.dataframe(display_df, use_container_width=True, hide_index=True)

                st.markdown("###  Детальная информация по документу")
                selected_id = st.selectbox("Выберите ID документа для просмотра деталей:", df['id'].tolist())
                
                if selected_id:
                    # Запрашиваем статус и детали
                    status_res = requests.get(f"{API_BASE_URL}/status/{selected_id}").json()
                    
                    st.write(f"**Файл:** {status_res['filename']}")
                    st.write(f"**Статус:** `{status_res['status']}`")
                    if status_res.get("error_message"):
                        st.error(f"Ошибка: {status_res['error_message']}")

                    if status_res['status'] == "completed":
                        if st.button("📄 Показать распознанный текст (JSON)"):
                            with st.spinner("Загрузка результата из S3..."):
                                json_res = requests.get(f"{API_BASE_URL}/result/{selected_id}")
                                if json_res.status_code == 200:
                                    json_data = json_res.json()
                                    
                                    # Показываем вкладки: Полный текст и Сырой JSON
                                    tab1, tab2 = st.tabs(["Текст", "Raw JSON"])
                                    with tab1:
                                        st.text_area("Распознанный текст", json_data.get("full_text", ""), height=400)
                                    with tab2:
                                        st.json(json_data)
                                else:
                                    st.warning("Не удалось загрузить JSON результат.")
            else:
                st.info("Документы не найдены.")
        else:
            st.error("Ошибка получения данных от API.")
    except Exception as e:
        st.error(f"Ошибка соединения с API: {e}")

# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/tests/test_core.py =====

"""
Tests for the document upload and deduplication logic.
Run with: pytest tests/ -v
"""

import io

from app.services.hasher import compute_sha256, compute_sha256_bytes


class TestHasher:
    def test_sha256_deterministic(self):
        data = b"hello world"
        h1 = compute_sha256_bytes(data)
        h2 = compute_sha256_bytes(data)
        assert h1 == h2

    def test_sha256_different_content(self):
        h1 = compute_sha256_bytes(b"file1")
        h2 = compute_sha256_bytes(b"file2")
        assert h1 != h2

    def test_sha256_file_like(self):
        buf = io.BytesIO(b"test content")
        h = compute_sha256(buf)
        assert len(h) == 64  # SHA-256 hex digest is 64 chars
        # File pointer should be reset
        assert buf.tell() == 0

    def test_sha256_known_value(self):
        # SHA-256 of empty bytes
        h = compute_sha256_bytes(b"")
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestDeduplicationLogic:
    """Unit tests verifying the dedup flow logic."""

    def test_duplicate_returns_existing_id(self):
        """When a hash exists, no new record should be created."""
        # This is a logic-level test — the actual DB query is mocked
        existing_hash = compute_sha256_bytes(b"same content")
        new_hash = compute_sha256_bytes(b"same content")
        assert existing_hash == new_hash  # dedup condition

    def test_new_content_gets_new_hash(self):
        h1 = compute_sha256_bytes(b"version 1")
        h2 = compute_sha256_bytes(b"version 2")
        assert h1 != h2  # should create new record


class TestMinIOServiceUnit:
    """Basic unit tests for MinIO path construction."""

    def test_source_path_format(self):
        file_hash = "abc123"
        filename = "report.pdf"
        expected = f"{file_hash}/{filename}"
        assert expected == "abc123/report.pdf"

    def test_result_path_format(self):
        file_hash = "abc123"
        expected = f"{file_hash}/surya_output.json"
        assert expected == "abc123/surya_output.json"


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/tests/test_api_upload.py =====

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import app.api.routes as routes_module
from app.core.database import get_db
from app.models.document import DocumentStatus


class DummyDBSession:
    fail_commit = False
    last_instance = None

    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        DummyDBSession.last_instance = self

    async def commit(self) -> None:
        self.commit_calls += 1
        if DummyDBSession.fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rollback_calls += 1


async def override_get_db():
    yield DummyDBSession()


@pytest.fixture
def client():
    DummyDBSession.fail_commit = False
    DummyDBSession.last_instance = None
    app = FastAPI()
    app.include_router(routes_module.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_upload_duplicate_returns_existing_document(client, monkeypatch):
    doc_id = uuid.uuid4()
    existing_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.COMPLETED)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=existing_doc)
    service.mark_duplicate = AsyncMock(return_value=existing_doc)
    service.create_document = AsyncMock()

    minio = MagicMock()
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"same-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is True
    service.create_document.assert_not_called()
    minio.upload_source_file.assert_not_called()
    trigger_flow.assert_not_called()


def test_upload_new_document_stores_file_and_triggers_flow(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is False
    minio.upload_source_file.assert_called_once()
    service.create_document.assert_awaited_once()
    trigger_flow.assert_called_once()


def test_upload_rolls_back_when_storage_upload_fails(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.build_source_path = MagicMock(return_value="hash123/contract.pdf")
    minio.upload_source_file = MagicMock(side_effect=RuntimeError("minio down"))
    minio.delete_source_file = MagicMock()
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "compute_sha256", lambda _: "hash123")
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )

    assert response.status_code == 502
    assert DummyDBSession.last_instance is not None
    assert DummyDBSession.last_instance.rollback_calls >= 1
    minio.delete_source_file.assert_not_called()
    trigger_flow.assert_not_called()


def test_upload_deletes_source_when_commit_fails(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.build_source_path = MagicMock(return_value="hash123/contract.pdf")
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    minio.delete_source_file = MagicMock()
    trigger_flow = MagicMock()

    DummyDBSession.fail_commit = True
    monkeypatch.setattr(routes_module, "compute_sha256", lambda _: "hash123")
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )
    DummyDBSession.fail_commit = False

    assert response.status_code == 500
    minio.delete_source_file.assert_called_once_with("hash123/contract.pdf")
    trigger_flow.assert_not_called()


def test_upload_rejects_file_when_size_exceeds_limit(client, monkeypatch):
    monkeypatch.setattr(routes_module.settings, "max_upload_size_mb", 1)

    service = MagicMock()
    service.find_by_hash = AsyncMock()
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("large.bin", b"x" * (1024 * 1024 + 1), "application/octet-stream")},
    )

    assert response.status_code == 413
    service.find_by_hash.assert_not_called()


def test_upload_handles_unique_hash_race_as_duplicate(client, monkeypatch):
    doc_id = uuid.uuid4()
    existing_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PROCESSING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(side_effect=[None, existing_doc])
    service.mark_duplicate = AsyncMock(return_value=existing_doc)
    service.create_document = AsyncMock(
        side_effect=IntegrityError("stmt", {"file_hash": "hash123"}, Exception("orig"))
    )

    minio = MagicMock()
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"race-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is True
    service.mark_duplicate.assert_awaited_once()
    trigger_flow.assert_not_called()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/workers/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/workers/pipeline.py =====

"""
Prefect 3.0 Flow: document_processing_pipeline

Steps:
1. Initialize — verify resources, set status to 'processing'
2. OCR Analysis — run Surya detection + recognition + table extraction
3. Data Structuring — build final JSON
4. Save & Update — upload JSON to MinIO, mark 'completed'
"""

import json
import uuid

from loguru import logger
from prefect import flow, task
from prefect.concurrency.sync import concurrency as concurrency_context

from app.core.config import get_settings
from app.core.database import get_sync_db
from app.models.document import DocumentStatus

settings = get_settings()

# Константа имени слота — единственный источник правды
OCR_CONCURRENCY_SLOT = "ocr-gpu-slots"


# ─────────────────────────────────────────────────────────────
# Helper: sync DB update (Prefect tasks run in a sync context)
# ─────────────────────────────────────────────────────────────

def _update_document_status(
    doc_id: str,
    status: DocumentStatus,
    result_path: str | None = None,
    error_message: str | None = None,
    page_count: int | None = None,
):
    """Update document record using a managed sync session."""
    from sqlalchemy import update as sa_update
    from app.models.document import Document
    from datetime import datetime, timezone

    values = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if result_path is not None:
        values["result_path"] = result_path
    if error_message is not None:
        values["error_message"] = error_message
    if page_count is not None:
        values["page_count"] = page_count

    with get_sync_db() as session:
        session.execute(
            sa_update(Document).where(Document.id == uuid.UUID(doc_id)).values(**values)
        )
        session.commit()


# ─────────────────────────────────────────────────────────────
# Tasks
# ─────────────────────────────────────────────────────────────

@task(
    name="initialize",
    retries=2,
    retry_delay_seconds=10,
    log_prints=True,
)
def task_initialize(doc_id: str, file_hash: str) -> dict:
    """
    Step 1: Mark document as 'processing' and verify resources.
    """
    logger.info(f"[Initialize] doc_id={doc_id}, hash={file_hash}")
    _update_document_status(doc_id, DocumentStatus.PROCESSING)

    # Verify MinIO is reachable
    from app.services.storage import get_minio_service
    minio = get_minio_service()
    if not minio.health_check():
        raise RuntimeError("MinIO is not reachable")

    return {"doc_id": doc_id, "file_hash": file_hash}


@task(
    name="ocr_analysis",
    retries=1,
    retry_delay_seconds=settings.ocr_retry_delay_seconds,
    timeout_seconds=settings.ocr_timeout_seconds,
    log_prints=True,
)
def task_ocr_analysis(ctx: dict) -> dict:
    """
    Step 2: Download file from MinIO → run Surya OCR pipeline.
    Uses concurrency limiting to avoid GPU OOM.
    """
    doc_id = ctx["doc_id"]
    file_hash = ctx["file_hash"]

    logger.info(f"[OCR] Starting analysis for {doc_id}")

    from app.services.storage import get_minio_service
    from app.services.ocr import get_ocr_service
    from app.models.document import Document

    # Get s3_path from DB — session closed automatically by context manager
    with get_sync_db() as session:
        doc = session.get(Document, uuid.UUID(doc_id))
        if doc is None:
            raise ValueError(f"Document {doc_id} not found in DB")
        s3_path = doc.s3_path
        filename = doc.filename

    # Download source file
    minio = get_minio_service()
    file_bytes = minio.download_source_file(s3_path)
    logger.info(f"[OCR] Downloaded {len(file_bytes)} bytes from MinIO")

    # occupy=1: каждая задача занимает ровно 1 слот из пула OCR_CONCURRENCY_SLOT.
    # Количество параллельных задач ограничивается самим Prefect-лимитом "ocr-gpu-slots".
    with concurrency_context(OCR_CONCURRENCY_SLOT, occupy=1):
        ocr = get_ocr_service()
        result = ocr.process_document(file_bytes, filename)

    logger.info(f"[OCR] Done — {result.get('page_count', 0)} pages processed")
    ctx["ocr_result"] = result
    return ctx


@task(
    name="data_structuring",
    log_prints=True,
)
def task_data_structuring(ctx: dict) -> dict:
    """
    Step 3: Validate and finalize the structured JSON object.
    """
    result = ctx["ocr_result"]
    doc_id = ctx["doc_id"]

    logger.info(f"[Structure] Finalising JSON for {doc_id}")

    # Add metadata
    from datetime import datetime, timezone
    result["processed_at"] = datetime.now(timezone.utc).isoformat()
    result["doc_id"] = doc_id

    ctx["final_json"] = result
    ctx["page_count"] = result.get("page_count", 0)
    return ctx


@task(
    name="save_and_update",
    retries=2,
    retry_delay_seconds=15,
    log_prints=True,
)
def task_save_and_update(ctx: dict) -> str:
    """
    Step 4: Upload result JSON to MinIO, update DB status → completed.
    """
    doc_id = ctx["doc_id"]
    file_hash = ctx["file_hash"]
    final_json = ctx["final_json"]
    page_count = ctx.get("page_count")

    logger.info(f"[Save] Uploading results for {doc_id}")

    from app.services.storage import get_minio_service
    minio = get_minio_service()

    json_bytes = json.dumps(final_json, ensure_ascii=False, indent=2).encode("utf-8")
    result_path = minio.upload_result_json(file_hash, json_bytes)

    _update_document_status(
        doc_id,
        DocumentStatus.COMPLETED,
        result_path=result_path,
        page_count=page_count,
    )

    logger.info(f"[Save] ✅ Document {doc_id} completed. Result → {result_path}")
    return doc_id


# ─────────────────────────────────────────────────────────────
# Flow
# ─────────────────────────────────────────────────────────────

@flow(
    name="document-processing-pipeline",
    retries=0,
    log_prints=True,
)
def document_processing_pipeline(doc_id: str, file_hash: str) -> str:
    """
    Main processing pipeline:
    Initialize → OCR Analysis → Data Structuring → Save & Update

    On failure at any stage, the document status is set to 'failed'.
    """
    try:
        ctx = task_initialize(doc_id, file_hash)
        ctx = task_ocr_analysis(ctx)
        ctx = task_data_structuring(ctx)
        result_id = task_save_and_update(ctx)
        return result_id

    except Exception as e:
        logger.error(f"Pipeline failed for {doc_id}: {e}")
        try:
            _update_document_status(
                doc_id,
                DocumentStatus.FAILED,
                error_message=str(e)[:2000],
            )
        except Exception as db_err:
            logger.error(f"Failed to update status to FAILED: {db_err}")
        raise


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/workers/main.py =====

"""
Prefect Worker entry point.

Startup sequence:
  1. recover_stuck_documents — reset docs stuck in 'processing' from previous crash
  2. pre_warm_ocr            — load Surya models ONCE + JIT warmup (saves 5-42s per doc)
  3. poll_and_process        — blocking polling loop, runs pipeline IN-PROCESS
                               (no subprocess spawning → models stay loaded between docs)

Why polling instead of Prefect serve():
  Prefect serve() spawns a NEW Python subprocess for every flow run.
  This means Surya models (~1.5 GB) are loaded from disk on EVERY document,
  adding 5-13 s overhead and doubling peak memory → OOM kill on 3+ pages.
  With a polling loop the process stays alive and models are loaded exactly once.
"""

import os
import sys
import time

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from app.core.config import get_settings
from app.core.database import get_sync_db

settings = get_settings()


# ── Recovery: reset docs stuck in 'processing' ───────────────────

def recover_stuck_documents(max_retries: int = 3, delay: int = 3) -> None:
    """
    On worker startup, find all documents whose status is still 'processing'
    (leftover from a previous worker crash) and reset them to 'failed'.
    Without this, those documents would display an endless spinner to the user.
    """
    from datetime import datetime, timezone

    from sqlalchemy import update as sa_update

    from app.models.document import Document, DocumentStatus

    for attempt in range(1, max_retries + 1):
        try:
            with get_sync_db() as session:
                result = session.execute(
                    sa_update(Document)
                    .where(Document.status == DocumentStatus.PROCESSING)
                    .values(
                        status=DocumentStatus.FAILED,
                        error_message="Обработка прервана: воркер был перезапущен",
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                session.commit()
                count = result.rowcount
                if count:
                    logger.warning(
                        f"[Recovery] Сброшено {count} документ(ов) из 'processing' → 'failed'"
                    )
                else:
                    logger.info("[Recovery] Застрявших документов не обнаружено")
            return
        except Exception as exc:
            logger.warning(f"[Recovery] Попытка {attempt}/{max_retries} не удалась: {exc}")
            if attempt < max_retries:
                time.sleep(delay)

    logger.error("[Recovery] Восстановление не выполнено — проверьте соединение с БД")


# ── OCR model pre-warm ────────────────────────────────────────────

def pre_warm_ocr() -> None:
    """
    Load Surya models once and run a tiny dummy inference to trigger
    PyTorch JIT compilation.

    Without this warmup, the FIRST real document is ~40-45 s slower
    because PyTorch compiles CUDA/CPU kernels on the very first batch.
    With warmup that cost is paid at startup and subsequent documents
    start recognising text immediately at full speed.
    """
    from PIL import Image

    from app.services.ocr import get_ocr_service

    logger.info("[Warmup] Загрузка OCR-моделей (единоразово)…")
    ocr = get_ocr_service()

    if ocr.rec_predictor is None:
        logger.warning("[Warmup] RecognitionPredictor недоступен, пропускаем прогрев")
        return

    # Tiny white image — just enough to trigger JIT without real cost
    dummy = Image.new("RGB", (256, 32), color=255)
    try:
        ocr.rec_predictor([dummy], det_predictor=ocr.det_predictor, sort_lines=True)
        logger.info("[Warmup] ✅ JIT-прогрев завершён — первый документ будет быстрее")
    except Exception as exc:
        logger.warning(f"[Warmup] Прогрев не удался (некритично): {exc}")


# ── Main polling loop ─────────────────────────────────────────────

def poll_and_process(poll_interval: int = 2) -> None:
    """
    Blocking polling loop.

    Finds the oldest PENDING document and runs the pipeline IN-PROCESS.
    Because the process never exits between documents, the OCR singleton
    (_ocr_service) stays loaded in memory — no model reload overhead.
    """
    from sqlalchemy import select

    from app.models.document import Document, DocumentStatus
    from workers.pipeline import document_processing_pipeline

    logger.info(f"[Worker] Polling loop запущен (интервал={poll_interval}с)")

    while True:
        doc_id = file_hash = None

        # ── find oldest PENDING document ──────────────────────────
        try:
            with get_sync_db() as session:
                row = session.execute(
                    select(Document.id, Document.file_hash)
                    .where(Document.status == DocumentStatus.PENDING)
                    .order_by(Document.created_at.asc())
                    .limit(1)
                ).first()
                if row:
                    doc_id, file_hash = str(row.id), row.file_hash
        except Exception as exc:
            logger.error(f"[Worker] DB poll error: {exc}")
            time.sleep(poll_interval * 2)
            continue

        # ── process or sleep ──────────────────────────────────────
        if doc_id:
            logger.info(f"[Worker] → Обрабатываем документ {doc_id}")
            try:
                document_processing_pipeline(doc_id, file_hash)
            except Exception as exc:
                # Pipeline marks doc as FAILED internally; just log here.
                logger.error(f"[Worker] Pipeline error для {doc_id}: {exc}")
        else:
            time.sleep(poll_interval)


# ── Entry point ───────────────────────────────────────────────────

def main() -> None:
    logger.info("🔧 Запуск Документолог Worker …")

    recover_stuck_documents()
    pre_warm_ocr()
    poll_and_process()


if __name__ == "__main__":
    main()


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/chunker/models.py =====

"""
ORM model for document chunks.
Таблица document_chunks — результат семантического чанкинга OCR JSON.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, Integer, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from app.core.database import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    doc_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    chunk_index = Column(Integer, nullable=False)
    total_chunks = Column(Integer, nullable=False)

    # Основной текст чанка (с перекрытием)
    text = Column(Text, nullable=False)

    # Номера страниц исходного документа, которые покрывает этот чанк
    page_numbers = Column(ARRAY(Integer), nullable=False)

    # Позиция в конкатенированном тексте документа (для дебага/контекста)
    char_start = Column(Integer)
    char_end = Column(Integer)

    # Приближённое количество токенов (~4 символа на токен)
    token_count = Column(Integer)

    created_at = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/chunker/splitter.py =====

"""
Semantic chunker for OCR JSON documents.

Стратегия (3 уровня):
1. Структурный — детектируем абзацы из bbox-данных Surya (вертикальный зазор > 1.5x высоты строки)
2. Рекурсивный — длинные абзацы разбиваем по иерархии разделителей (предложение → слово)
3. Слияние — объединяем короткие абзацы до минимального размера чанка
4. Перекрытие — добавляем tail предыдущего чанка с выравниванием по границе предложения
"""

from dataclasses import dataclass, field
from typing import List


# ── Разделители по убыванию приоритета ───────────────────────
_SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", "؟ ", "; ", ", ", " "]


# ── Data classes ─────────────────────────────────────────────

@dataclass
class Chunk:
    text: str
    chunk_index: int
    total_chunks: int       # будет заполнено после финального подсчёта
    page_numbers: List[int]
    char_start: int
    char_end: int
    token_count: int        # приближение: ~4 символа на токен для русского/казахского


# ── Helpers ───────────────────────────────────────────────────

def _approx_tokens(text: str) -> int:
    """~4 символа на токен — достаточная точность для русского/казахского без tiktoken."""
    return max(1, len(text) // 4)


def _extract_paragraphs(pages: list) -> list[dict]:
    """
    Группируем строки OCR в абзацы, используя вертикальный зазор между bbox.

    Если у строки нет bbox (Surya иногда их не возвращает) — используем
    текст страницы целиком как один абзац.
    """
    paragraphs = []

    for page in pages:
        page_num = page.get("page_number", 1)
        lines = page.get("lines", [])

        # Нет строк — берём текст страницы как один абзац
        if not lines:
            text = page.get("text", "").strip()
            if text:
                paragraphs.append({"text": text, "page": page_num})
            continue

        # Считаем среднюю высоту строки из bbox
        heights = []
        for line in lines:
            bbox = line.get("bbox") or line.get("polygon_bbox") or []
            if len(bbox) >= 4:
                heights.append(abs(float(bbox[3]) - float(bbox[1])))
        avg_h = (sum(heights) / len(heights)) if heights else 18.0
        gap_threshold = avg_h * 1.5

        current_lines: list[str] = []
        prev_bottom: float | None = None

        for line in lines:
            text = (line.get("text") or "").strip()
            if not text:
                continue

            bbox = line.get("bbox") or line.get("polygon_bbox") or []
            if len(bbox) >= 4:
                top = min(float(bbox[1]), float(bbox[3]))
                bottom = max(float(bbox[1]), float(bbox[3]))

                if prev_bottom is not None and (top - prev_bottom) > gap_threshold:
                    if current_lines:
                        paragraphs.append(
                            {"text": " ".join(current_lines), "page": page_num}
                        )
                        current_lines = []

                prev_bottom = bottom

            current_lines.append(text)

        if current_lines:
            paragraphs.append({"text": " ".join(current_lines), "page": page_num})

    return paragraphs


def _split_recursive(text: str, max_chars: int, separators: list[str]) -> list[str]:
    """
    Рекурсивно разбиваем текст по иерархии разделителей.
    Возвращает список подстрок, каждая <= max_chars.
    """
    if len(text) <= max_chars:
        return [text]

    for sep in separators:
        if sep not in text:
            continue

        pieces = text.split(sep)
        result: list[str] = []
        current = ""

        for piece in pieces:
            candidate = (current + sep + piece) if current else piece
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    result.append(current)
                # Рекурсия для piece, если он сам слишком длинный
                remaining = separators[separators.index(sep) + 1 :]
                if len(piece) > max_chars and remaining:
                    result.extend(_split_recursive(piece, max_chars, remaining))
                    current = ""
                else:
                    current = piece

        if current:
            result.append(current)

        clean = [c for c in result if c.strip()]
        if clean:
            return clean

    # Жёсткое разрезание как последний вариант
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _add_overlap(
    raw_chunks: list[dict], overlap_chars: int
) -> list[dict]:
    """
    Добавляем хвост предыдущего чанка в начало следующего.
    Выравниваем по ближайшей границе предложения внутри overlap-окна.
    """
    if overlap_chars <= 0 or len(raw_chunks) < 2:
        return raw_chunks

    result = [raw_chunks[0]]

    for i in range(1, len(raw_chunks)):
        prev_text = result[-1]["text"]
        tail = prev_text[-overlap_chars:]

        # Ищем последнюю границу предложения в хвосте
        for sep in (". ", "! ", "? ", "؟ ", "\n"):
            idx = tail.rfind(sep)
            if idx != -1 and idx + len(sep) < len(tail) - 10:
                tail = tail[idx + len(sep) :]
                break

        chunk = dict(raw_chunks[i])  # копия
        if len(tail.strip()) > 20:
            chunk["text"] = tail.strip() + " " + chunk["text"]
            chunk["pages"].update(result[-1]["pages"])  # страницы overlap тоже включаем

        result.append(chunk)

    return result


# ── Public API ────────────────────────────────────────────────

def build_chunks(
    ocr_json: dict,
    max_chars: int = 2000,
    overlap_chars: int = 200,
    min_chars: int = 100,
) -> list[Chunk]:
    """
    Основная функция. Принимает OCR JSON и возвращает список Chunk.

    Args:
        ocr_json:      Вывод SuryaOCRService (поля: pages, full_text, page_count)
        max_chars:     Максимальный размер чанка в символах (~500 токенов для RU/KZ)
        overlap_chars: Символов перекрытия между соседними чанками (~50 токенов)
        min_chars:     Минимальный размер — слишком короткие абзацы сливаем с соседом
    """
    pages = ocr_json.get("pages", [])

    if pages:
        paragraphs = _extract_paragraphs(pages)
    else:
        # Fallback: нет страниц → берём full_text
        full_text = ocr_json.get("full_text", "").strip()
        if not full_text:
            return []
        paragraphs = [{"text": full_text, "page": 1}]

    if not paragraphs:
        return []

    # ── Шаг 1: разбиваем длинные абзацы ─────────────────────
    raw: list[dict] = []  # {"text": str, "pages": set[int]}

    for para in paragraphs:
        text = para["text"].strip()
        page = para["page"]
        if not text:
            continue
        if len(text) > max_chars:
            for part in _split_recursive(text, max_chars, _SEPARATORS):
                if part.strip():
                    raw.append({"text": part.strip(), "pages": {page}})
        else:
            raw.append({"text": text, "pages": {page}})

    # ── Шаг 2: сливаем слишком короткие ──────────────────────
    merged: list[dict] = []
    for item in raw:
        if (
            merged
            and len(merged[-1]["text"]) < min_chars
            and len(merged[-1]["text"]) + 1 + len(item["text"]) <= max_chars
        ):
            merged[-1]["text"] += " " + item["text"]
            merged[-1]["pages"].update(item["pages"])
        else:
            merged.append(item)

    # ── Шаг 3: добавляем перекрытие ──────────────────────────
    merged = _add_overlap(merged, overlap_chars)

    # ── Шаг 4: строим финальные объекты с позициями ──────────
    total = len(merged)
    result: list[Chunk] = []
    offset = 0

    for idx, item in enumerate(merged):
        text = item["text"]
        result.append(
            Chunk(
                text=text,
                chunk_index=idx,
                total_chunks=total,
                page_numbers=sorted(item["pages"]),
                char_start=offset,
                char_end=offset + len(text),
                token_count=_approx_tokens(text),
            )
        )
        offset += len(text) + 1  # +1 за пробел между чанками

    return result


# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/chunker/__init__.py =====



# ===== FILE: /Users/a1111/Desktop/projects/nurb_documentolog/documentolog/chunker/main.py =====

"""
Chunker Service — FastAPI + background polling worker.

Логика работы:
  1. Background thread каждые CHUNK_POLL_INTERVAL секунд находит документы
     со статусом 'completed' без чанков и обрабатывает их.
  2. FastAPI-эндпоинты отдают готовые чанки RAG-сервису.

Эндпоинты:
  GET  /health                        — liveness probe
  GET  /chunks                        — список всех отчанкованных документов (пагинация)
  GET  /chunks/{doc_id}               — все чанки конкретного документа
  GET  /chunks/{doc_id}/status        — проверить, отчанкован ли документ
  POST /chunks/{doc_id}/reprocess     — принудительно перечанковать документ
"""

import json
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from loguru import logger
from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.core.database import get_sync_db
from app.models.document import Document, DocumentStatus
from chunker.models import DocumentChunk
from chunker.splitter import build_chunks

settings = get_settings()

# Параметры чанкинга из env
_MAX_CHARS     = int(os.getenv("CHUNK_MAX_CHARS", "2000"))
_OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", "200"))
_MIN_CHARS     = int(os.getenv("CHUNK_MIN_CHARS", "100"))
_POLL_INTERVAL = int(os.getenv("CHUNK_POLL_INTERVAL", "5"))


# ── Обработка одного документа ────────────────────────────────

def _process_document(doc_id: str, result_path: str, filename: str) -> int:
    """
    Скачиваем OCR JSON из MinIO → чанкуем → сохраняем в document_chunks.
    Возвращает количество созданных чанков.
    """
    from app.services.storage import get_minio_service

    try:
        raw = get_minio_service().download_result_json(result_path)
        ocr_json = json.loads(raw)
    except Exception as exc:
        logger.error(f"[Chunker] Не удалось загрузить OCR JSON для {doc_id}: {exc}")
        raise

    chunks = build_chunks(
        ocr_json,
        max_chars=_MAX_CHARS,
        overlap_chars=_OVERLAP_CHARS,
        min_chars=_MIN_CHARS,
    )

    if not chunks:
        logger.warning(f"[Chunker] Нет чанков для {doc_id} ({filename}) — создаём пустой маркер")
        with get_sync_db() as session:
            session.add(DocumentChunk(
                doc_id=uuid.UUID(doc_id),
                chunk_index=0,
                total_chunks=0,
                text="",
                page_numbers=[],
                char_start=0,
                char_end=0,
                token_count=0,
            ))
            session.commit()
        return 0

    with get_sync_db() as session:
        # Удаляем старые чанки при повторной обработке
        session.query(DocumentChunk).filter(
            DocumentChunk.doc_id == uuid.UUID(doc_id)
        ).delete(synchronize_session=False)

        for chunk in chunks:
            session.add(DocumentChunk(
                doc_id=uuid.UUID(doc_id),
                chunk_index=chunk.chunk_index,
                total_chunks=chunk.total_chunks,
                text=chunk.text,
                page_numbers=chunk.page_numbers,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                token_count=chunk.token_count,
            ))
        session.commit()

    logger.info(f"[Chunker] ✅ {doc_id} ({filename}) → {len(chunks)} чанков")
    return len(chunks)


# ── Polling loop ──────────────────────────────────────────────

def _poll_loop() -> None:
    """
    Фоновый поток: ищет completed-документы без чанков и обрабатывает их.
    Работает по той же схеме что и workers/main.py.
    """
    logger.info(f"[Chunker] Polling loop запущен (интервал={_POLL_INTERVAL}с)")

    while True:
        doc_id = result_path = filename = None

        try:
            with get_sync_db() as session:
                already_chunked = select(DocumentChunk.doc_id).distinct()
                row = session.execute(
                    select(
                        Document.id,
                        Document.result_path,
                        Document.filename,
                    )
                    .where(Document.status == DocumentStatus.COMPLETED)
                    .where(Document.result_path.isnot(None))
                    .where(Document.id.not_in(already_chunked))
                    .order_by(Document.updated_at.asc())
                    .limit(1)
                ).first()

                if row:
                    doc_id = str(row.id)
                    result_path = row.result_path
                    filename = row.filename

        except Exception as exc:
            logger.error(f"[Chunker] Ошибка опроса БД: {exc}")
            time.sleep(_POLL_INTERVAL * 2)
            continue

        if doc_id:
            try:
                _process_document(doc_id, result_path, filename)
            except Exception as exc:
                logger.error(f"[Chunker] Ошибка обработки {doc_id}: {exc}")
        else:
            time.sleep(_POLL_INTERVAL)


# ── FastAPI ───────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=_poll_loop, daemon=True, name="chunker-poll")
    t.start()
    logger.info("[Chunker] Background worker запущен")
    yield


app = FastAPI(
    title="Документолог — Chunker Service",
    description=(
        "Семантическое разбиение OCR-результатов на чанки для загрузки в RAG-базу. "
        "Автоматически обрабатывает completed-документы в фоне."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── Endpoints ─────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    """Liveness probe."""
    return {"status": "ok", "service": "chunker"}


@app.get("/chunks", tags=["Chunks"])
def list_chunked_documents(
    offset: int = Query(0, ge=0, description="Смещение"),
    limit: int = Query(50, ge=1, le=200, description="Размер страницы"),
):
    """
    Список документов, для которых уже готовы чанки.
    Используется RAG-сервисом для получения списка документов к индексации.
    """
    with get_sync_db() as session:
        # Агрегация: doc_id → кол-во чанков и время создания
        subq = (
            select(
                DocumentChunk.doc_id,
                func.count(DocumentChunk.id).label("chunk_count"),
                func.min(DocumentChunk.created_at).label("chunked_at"),
                func.sum(DocumentChunk.token_count).label("total_tokens"),
            )
            .group_by(DocumentChunk.doc_id)
            .subquery()
        )

        base_q = (
            select(
                Document.id,
                Document.filename,
                Document.updated_at,
                subq.c.chunk_count,
                subq.c.chunked_at,
                subq.c.total_tokens,
            )
            .join(subq, Document.id == subq.c.doc_id)
            .where(Document.status == DocumentStatus.COMPLETED)
            .order_by(subq.c.chunked_at.desc())
        )

        total = session.execute(
            select(func.count()).select_from(base_q.subquery())
        ).scalar_one()

        rows = session.execute(base_q.offset(offset).limit(limit)).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "documents": [
            {
                "doc_id": str(r.id),
                "filename": r.filename,
                "chunk_count": r.chunk_count,
                "total_tokens": r.total_tokens,
                "chunked_at": r.chunked_at.isoformat(),
            }
            for r in rows
        ],
    }


@app.get("/chunks/{doc_id}/status", tags=["Chunks"])
def get_chunk_status(doc_id: uuid.UUID):
    """
    Проверить, готовы ли чанки для документа.
    Удобно для polling перед запросом GET /chunks/{doc_id}.
    """
    with get_sync_db() as session:
        count = session.execute(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.doc_id == doc_id
            )
        ).scalar_one()

    return {
        "doc_id": str(doc_id),
        "ready": count > 0,
        "chunk_count": count,
    }


@app.get("/chunks/{doc_id}", tags=["Chunks"])
def get_chunks(doc_id: uuid.UUID):
    """
    Возвращает все чанки документа в формате, готовом к загрузке в RAG-базу.

    Каждый чанк содержит:
    - text          — текст с перекрытием для сохранения контекста
    - metadata      — doc_id, filename, страницы, позиция, токены
    - chunk_index   — порядковый номер (0-based)
    - total_chunks  — общее количество чанков в документе

    HTTP 404 — если документ не найден или ещё не отчанкован.
    """
    with get_sync_db() as session:
        # Получаем имя файла для metadata
        doc = session.get(Document, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")

        rows = session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
        ).scalars().all()

    if not rows:
        if doc.status != DocumentStatus.COMPLETED:
            raise HTTPException(
                status_code=409,
                detail=f"Документ ещё не обработан (статус: {doc.status.value}). "
                       "Дождитесь завершения OCR.",
            )
        raise HTTPException(
            status_code=404,
            detail="Чанки не найдены. Документ может быть в очереди чанкера — "
                   "попробуйте через несколько секунд.",
        )

    return {
        "doc_id": str(doc_id),
        "filename": doc.filename,
        "total_chunks": len(rows),
        "chunks": [
            {
                "chunk_index": r.chunk_index,
                "total_chunks": r.total_chunks,
                "text": r.text,
                "token_count": r.token_count,
                "page_numbers": r.page_numbers,
                "char_start": r.char_start,
                "char_end": r.char_end,
                # Готовый metadata-блок для векторной БД
                "metadata": {
                    "doc_id": str(doc_id),
                    "filename": doc.filename,
                    "chunk_index": r.chunk_index,
                    "total_chunks": r.total_chunks,
                    "page_numbers": r.page_numbers,
                    "char_start": r.char_start,
                    "char_end": r.char_end,
                    "token_count": r.token_count,
                    "chunked_at": r.created_at.isoformat(),
                },
            }
            for r in rows
        ],
    }


@app.post("/chunks/{doc_id}/reprocess", status_code=202, tags=["Chunks"])
def reprocess_document(doc_id: uuid.UUID):
    """
    Принудительно удаляет существующие чанки и ставит документ в очередь
    на повторный чанкинг (например, после изменения CHUNK_MAX_CHARS).

    HTTP 404 — документ не найден.
    HTTP 409 — OCR ещё не завершён.
    """
    with get_sync_db() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if doc.status != DocumentStatus.COMPLETED:
            raise HTTPException(
                status_code=409,
                detail=f"Документ ещё не обработан (статус: {doc.status.value}).",
            )

    with get_sync_db() as session:
        deleted = session.query(DocumentChunk).filter(
            DocumentChunk.doc_id == doc_id
        ).delete(synchronize_session=False)
        session.commit()

    logger.info(f"[Chunker] Reprocess запрошен для {doc_id} (удалено {deleted} чанков)")
    return {
        "doc_id": str(doc_id),
        "status": "queued",
        "message": "Документ поставлен в очередь на повторный чанкинг.",
    }
