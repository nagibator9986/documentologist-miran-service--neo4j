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