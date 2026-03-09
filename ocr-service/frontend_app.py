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
        # Cursor stack for keyset pagination.
        # cursor_stack[i] is the cursor value needed to fetch page i+1.
        # cursor_stack[0] is always None (first page needs no cursor).
        "cursor_stack": [None],
        # next_cursor returned by the last API call; used by go_next().
        "last_next_cursor": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

def reset_pagination():
    st.session_state.registry_page = 1
    st.session_state.cursor_stack = [None]
    st.session_state.last_next_cursor = None

def go_first():
    st.session_state.registry_page = 1
    st.session_state.cursor_stack = [None]
    st.session_state.last_next_cursor = None

def go_prev():
    st.session_state.registry_page = max(1, st.session_state.registry_page - 1)

def go_next():
    next_cursor = st.session_state.get("last_next_cursor")
    current_page = st.session_state.registry_page
    # Extend the stack only if this page hasn't been visited yet
    if next_cursor and len(st.session_state.cursor_stack) <= current_page:
        st.session_state.cursor_stack.append(next_cursor)
    st.session_state.registry_page = current_page + 1

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

def render_pagination_controls(total_items, has_next: bool):
    page_size = st.session_state.page_size
    current_page = st.session_state.registry_page
    total_pages = math.ceil(total_items / page_size) if total_items > 0 else 1

    st.write("")
    col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 2])

    with col1:
        st.button("⏪", on_click=go_first,
                  disabled=(current_page == 1), use_container_width=True, help="Первая страница")
    with col2:
        st.button("◀", on_click=go_prev,
                  disabled=(current_page == 1), use_container_width=True, help="Предыдущая")
    with col3:
        st.markdown(
            f"<div class='pagination-container'>Стр. {current_page} из {total_pages}</div>",
            unsafe_allow_html=True
        )
    with col4:
        st.button("▶", on_click=go_next,
                  disabled=not has_next, use_container_width=True, help="Следующая")
    with col5:
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
    current_page = st.session_state.registry_page
    cursor_stack = st.session_state.cursor_stack
    # Resolve cursor for the current page (stack may be shorter on fresh load)
    current_cursor = cursor_stack[current_page - 1] if current_page - 1 < len(cursor_stack) else None

    params_dict = {"limit": st.session_state.page_size, "latest_only": True}
    if current_cursor:      params_dict["cursor"]         = current_cursor
    if f_filename:          params_dict["filename"]       = f_filename
    if f_status != "Все":  params_dict["status"]          = f_status
    if f_date_from:         params_dict["created_after"]  = f"{f_date_from}T00:00:00Z"
    if f_date_to:           params_dict["created_before"] = f"{f_date_to}T23:59:59Z"

    # dict → tuple для корректного хэширования в кэше
    params_tuple = tuple(sorted(params_dict.items()))

    # --- Данные ---
    data       = fetch_documents(params_tuple)
    docs       = data.get("documents", [])
    total_docs = data.get("total", 0)
    next_cursor = data.get("next_cursor")

    # Store next_cursor so go_next() can append it to the stack
    st.session_state.last_next_cursor = next_cursor

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

    render_pagination_controls(total_docs, has_next=next_cursor is not None)


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
                            st.text_area(
                                "Распознанный текст",
                                full_text,
                                height=500,
                            )
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
