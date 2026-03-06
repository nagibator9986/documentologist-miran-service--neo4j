"""Streamlit chat UI for Miran LangGraph Agent."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import requests
import streamlit as st

# ──────────────────────────────────────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Miran — Банковский ИИ Ассистент",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ──────────────────────────────────────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* Remove default padding */
.block-container { padding-top: 1rem; }

/* Intent badges */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin-bottom: 6px;
}
.badge-search   { background: #dbeafe; color: #1e40af; }
.badge-verify   { background: #fef9c3; color: #854d0e; }
.badge-generate { background: #dcfce7; color: #166534; }
.badge-analyze  { background: #f3e8ff; color: #6b21a8; }

/* Citation card */
.citation-card {
    background: #f8fafc;
    border-left: 3px solid #94a3b8;
    padding: 8px 12px;
    border-radius: 0 6px 6px 0;
    margin-bottom: 6px;
    font-size: 0.85rem;
    color: #1e293b !important;
}

/* Document result card */
.doc-card {
    background: #f0f9ff;
    border: 1px solid #bae6fd;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 10px;
}
.doc-filename {
    font-weight: 600;
    color: #0369a1;
    font-size: 0.9rem;
}

/* Entity tag */
.entity-tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.78rem;
    margin: 2px;
    background: #e0e7ff;
    color: #3730a3;
}

/* Chat bubble tweaks */
[data-testid="stChatMessage"] { border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# Session state helpers
# ──────────────────────────────────────────────────────────────────────────────

def _init_state() -> None:
    if "sessions" not in st.session_state:
        st.session_state.sessions = {}          # session_id -> {name, messages}
    if "active_session" not in st.session_state:
        st.session_state.active_session = None
    if "agent_url" not in st.session_state:
        st.session_state.agent_url = "http://localhost:8001"


def _new_session() -> str:
    sid = str(uuid.uuid4())[:8]
    st.session_state.sessions[sid] = {"name": f"Сессия {sid}", "messages": []}
    st.session_state.active_session = sid
    return sid


def _active_messages() -> list[dict]:
    sid = st.session_state.active_session
    if sid and sid in st.session_state.sessions:
        return st.session_state.sessions[sid]["messages"]
    return []


def _push_message(msg: dict) -> None:
    sid = st.session_state.active_session
    if sid:
        st.session_state.sessions[sid]["messages"].append(msg)


def _auto_name_session(query: str) -> None:
    sid = st.session_state.active_session
    if not sid:
        return
    session = st.session_state.sessions[sid]
    if session["name"].startswith("Сессия "):
        session["name"] = query[:40] + ("…" if len(query) > 40 else "")


# ──────────────────────────────────────────────────────────────────────────────
# Agent API
# ──────────────────────────────────────────────────────────────────────────────

def _download_document(filename: str) -> tuple[bytes, str] | None:
    """Fetch document content from the agent API.

    Returns (content_bytes, download_filename) or None if unavailable.
    """
    import urllib.parse
    url = st.session_state.agent_url.rstrip("/") + "/api/v1/documents/" + urllib.parse.quote(filename) + "/download"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            # Determine download filename from Content-Disposition or derive from original
            cd = resp.headers.get("Content-Disposition", "")
            if 'filename="' in cd:
                dl_name = cd.split('filename="')[1].rstrip('"')
            else:
                from pathlib import Path as _Path
                dl_name = _Path(filename).stem + ".txt"
            return resp.content, dl_name
    except Exception:
        pass
    return None


def _call_agent(query: str, session_id: str) -> dict[str, Any]:
    url = st.session_state.agent_url.rstrip("/") + "/api/v1/chat"
    try:
        resp = requests.post(url, json={"query": query, "session_id": session_id}, timeout=200)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return {"error": "Не удалось подключиться к агенту. Проверьте URL и убедитесь, что сервис запущен."}
    except requests.exceptions.Timeout:
        return {"error": "Превышено время ожидания ответа (200 с). Попробуйте ещё раз."}
    except Exception as exc:
        return {"error": str(exc)}


def _check_health() -> bool:
    try:
        resp = requests.get(st.session_state.agent_url.rstrip("/") + "/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _load_sessions_from_db() -> None:
    """Load recent sessions + their message history from PostgreSQL via API."""
    base = st.session_state.agent_url.rstrip("/")
    try:
        resp = requests.get(f"{base}/api/v1/sessions/", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        st.sidebar.error(f"Не удалось загрузить историю: {exc}")
        return

    loaded = 0
    for s in data.get("sessions", []):
        sid = s["session_id"]
        if sid in st.session_state.sessions:
            continue  # already loaded

        # Load messages for this session
        try:
            m_resp = requests.get(f"{base}/api/v1/sessions/{sid}/messages", timeout=10)
            m_resp.raise_for_status()
            msgs_raw = m_resp.json().get("messages", [])
        except Exception:
            msgs_raw = []

        messages = []
        for m in msgs_raw:
            role = m["role"]
            content = m["content"]
            messages.append({"role": role, "content": content, "data": {"intent": "", "response": content}})

        # Session name: use last user query or session_id
        last_query = s.get("last_query") or sid
        name = last_query[:40] + ("…" if len(last_query) > 40 else "")
        st.session_state.sessions[sid] = {"name": name, "messages": messages}
        loaded += 1

    if loaded:
        # Activate the most recent session if none is active
        if not st.session_state.active_session:
            st.session_state.active_session = data["sessions"][0]["session_id"]
        st.sidebar.success(f"Загружено {loaded} сессий из БД")
    else:
        st.sidebar.info("Новых сессий в БД не найдено")


# ──────────────────────────────────────────────────────────────────────────────
# Card renderers
# ──────────────────────────────────────────────────────────────────────────────

def _badge(intent: str) -> str:
    labels = {"search": "Поиск", "verify": "Проверка", "generate": "Генерация", "analyze": "Анализ"}
    label = labels.get(intent, intent)
    return f'<span class="badge badge-{intent}">{label}</span>'


def _cited_indices(response_text: str) -> set[int]:
    """Extract citation numbers referenced in the answer, e.g. [1], [2], [Источник 2]."""
    import re as _re
    nums = _re.findall(r'\[(?:Источник\s*)?(\d+)\]', response_text)
    return {int(n) for n in nums}


def _render_search_card(data: dict, msg_idx: int = 0) -> None:
    st.markdown(_badge("search"), unsafe_allow_html=True)
    st.markdown(data.get("response", ""))

    citations: list[dict] = data.get("citations", [])

    # ── Exact-match highlight blocks ──────────────────────────────────────────
    exact_matches = [c for c in citations if c.get("is_exact_match") and c.get("match_content")]
    if exact_matches:
        st.markdown("---")
        st.markdown("**📍 Найденные фрагменты в документах:**")
        for c in exact_matches:
            fn = c.get("filename") or "неизвестный документ"
            page = c.get("page_number")
            section = c.get("section", "")
            content = c.get("match_content", "")

            # Location line
            loc_parts: list[str] = [f"📄 **{fn}**"]
            if page:
                loc_parts.append(f"стр. {page}")
            if section:
                loc_parts.append(section)
            st.markdown(" · ".join(loc_parts))

            # Highlighted quote block
            st.markdown(
                f'<div style="background:#f0fdf4;border-left:4px solid #22c55e;'
                f'padding:12px 16px;border-radius:0 8px 8px 0;font-size:0.88rem;'
                f'color:#14532d;white-space:pre-wrap;margin-bottom:12px;">'
                f'{content[:2000]}'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("---")

    # Show unique source documents with download buttons
    if citations:
        seen_files: dict[str, int] = {}  # filename → first citation list-position for key uniqueness
        for i, c in enumerate(citations):
            fn = c.get("filename", "")
            if fn and fn not in seen_files:
                seen_files[fn] = i

        if seen_files:
            # Sort: documents actually cited in the answer (e.g. "[2]") come first
            cited_idxs = _cited_indices(data.get("response", ""))
            sorted_files = sorted(
                seen_files.items(),
                key=lambda x: (0 if citations[x[1]].get("index", 999) in cited_idxs else 1, x[1]),
            )
            st.markdown("**📄 Найденные документы:**")
            for fn, idx in sorted_files:
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.markdown(f'<div class="doc-card"><span class="doc-filename">📄 {fn}</span></div>', unsafe_allow_html=True)
                with col2:
                    result = _download_document(fn)
                    if result:
                        file_bytes, dl_name = result
                        st.download_button(
                            label="⬇️",
                            data=file_bytes,
                            file_name=dl_name,
                            mime="text/plain",
                            key=f"dl_search_{msg_idx}_{idx}_{fn}",
                        )
                    else:
                        st.caption("—")

        with st.expander(f"📎 Источники ({len(citations)})", expanded=False):
            for c in citations:
                score = c.get("score", 0)
                section = c.get("section", "")
                filename = c.get("filename", "")
                preview = c.get("content_preview", "")
                # Clamp to [0, 1]: cross-encoder scores can be negative; cosine scores are 0–1
                if score <= 1:
                    score_norm = max(0.0, min(1.0, float(score)))
                else:
                    score_norm = max(0.0, min(1.0, float(score) / 10.0))
                header_parts = [f"[{c.get('index', '?')}]"]
                if filename:
                    header_parts.append(filename)
                if section:
                    header_parts.append(section)
                header = " · ".join(header_parts)
                st.markdown(f'<div class="citation-card"><b>{header}</b><br>{preview}</div>', unsafe_allow_html=True)
                st.progress(score_norm, text=f"Релевантность: {score:.2f}")


def _render_verify_card(data: dict, msg_idx: int = 0) -> None:
    st.markdown(_badge("verify"), unsafe_allow_html=True)

    vr: dict = data.get("verify_result", {})
    compliant = vr.get("compliant")
    risk_score = vr.get("risk_score", 0)

    # Compliance status box
    if compliant is True:
        st.success("✅ Документ соответствует законодательству")
    elif compliant is False:
        st.error("❌ Выявлены нарушения / риски")
    else:
        st.warning("⚠️ Соответствие не определено")

    # Risk score metric + progress
    col1, col2 = st.columns([1, 3])
    with col1:
        st.metric("Риск-оценка", f"{risk_score}/10", delta=None)
    with col2:
        color = "green" if risk_score <= 3 else ("orange" if risk_score <= 6 else "red")
        st.progress(risk_score / 10, text=f"Уровень риска: {'низкий' if risk_score <= 3 else 'средний' if risk_score <= 6 else 'высокий'}")

    # Issues
    issues = vr.get("issues", [])
    if issues and not (len(issues) == 1 and "Не удалось" in issues[0]):
        with st.expander("⚠️ Нарушения и риски", expanded=True):
            for i in issues:
                st.markdown(f"- {i}")

    # Fix hints
    hints = vr.get("fix_hints", [])
    if hints:
        with st.expander("💡 Рекомендации по исправлению", expanded=False):
            for h in hints:
                st.markdown(f"- {h}")

    # Law refs
    law_refs = vr.get("law_refs", [])
    if law_refs:
        with st.expander("📜 Ссылки на НПА", expanded=False):
            for ref in law_refs:
                st.markdown(f"- {ref}")


def _render_generate_card(data: dict, msg_idx: int = 0) -> None:
    st.markdown(_badge("generate"), unsafe_allow_html=True)

    gr: dict = data.get("generate_result", {})
    title = gr.get("title") or "Документ"
    content = gr.get("content", "")
    export_path: str | None = gr.get("export_path")

    st.markdown(f"### 📄 {title}")

    # Preview first 600 chars
    preview = content[:600] + ("…" if len(content) > 600 else "")
    st.markdown(preview)

    if content:
        with st.expander("📖 Полный текст документа", expanded=False):
            st.text(content)
        st.download_button(
            label="⬇️ Скачать .txt",
            data=content.encode("utf-8"),
            file_name=f"{title[:40]}.txt",
            mime="text/plain",
        )

    if export_path:
        st.info(f"📁 Файл сохранён на сервере: `{export_path}`")


def _render_analyze_card(data: dict, msg_idx: int = 0) -> None:
    st.markdown(_badge("analyze"), unsafe_allow_html=True)

    ar: dict = data.get("analyze_result", {})
    task = ar.get("task", "qa")
    confidence = ar.get("confidence", 0.5)

    # Confidence meter
    st.progress(float(confidence), text=f"Уверенность модели: {confidence:.0%}")

    # Main result
    result_text = ar.get("result", data.get("response", ""))
    if result_text:
        st.markdown(result_text)

    # Key points
    key_points = ar.get("key_points", [])
    if key_points:
        with st.expander("🔑 Ключевые тезисы", expanded=True):
            for p in key_points:
                st.markdown(f"- {p}")

    if task == "compare":
        col1, col2 = st.columns(2)
        with col1:
            similarities = ar.get("similarities", [])
            if similarities:
                st.markdown("**Сходства:**")
                for s in similarities:
                    st.markdown(f"- {s}")
        with col2:
            differences = ar.get("differences", [])
            if differences:
                st.markdown("**Различия:**")
                for d in differences:
                    st.markdown(f"- {d}")

        conflicts = ar.get("legal_conflicts", [])
        if conflicts:
            with st.expander("⚖️ Юридические противоречия", expanded=False):
                for c in conflicts:
                    st.markdown(f"- {c}")

    # Entities as tags
    entities = ar.get("entities", [])
    if entities:
        entity_html = " ".join(
            f'<span class="entity-tag">{e.get("value", e.get("name", ""))} [{e.get("type", "")}]</span>'
            for e in entities
            if e.get("value") or e.get("name")
        )
        if entity_html:
            st.markdown(f"**Сущности:** {entity_html}", unsafe_allow_html=True)

    # Recommendations
    recs = ar.get("recommendations", [])
    if recs:
        with st.expander("💡 Рекомендации", expanded=False):
            for r in recs:
                st.markdown(f"- {r}")

    # Citations with filenames + download
    citations = data.get("citations", [])
    if citations:
        seen_files: dict[str, int] = {}
        for i, c in enumerate(citations):
            fn = c.get("filename", "")
            if fn and fn not in seen_files:
                seen_files[fn] = i

        if seen_files:
            st.markdown("**📄 Источники документов:**")
            for fn, idx in seen_files.items():
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.markdown(f'<div class="doc-card"><span class="doc-filename">📄 {fn}</span></div>', unsafe_allow_html=True)
                with col2:
                    result = _download_document(fn)
                    if result:
                        file_bytes, dl_name = result
                        st.download_button(
                            label="⬇️",
                            data=file_bytes,
                            file_name=dl_name,
                            mime="text/plain",
                            key=f"dl_analyze_{msg_idx}_{idx}_{fn}",
                        )
                    else:
                        st.caption("—")

        with st.expander(f"📎 Использованные фрагменты ({len(citations)})", expanded=False):
            for c in citations:
                preview = c.get("content", c.get("content_preview", ""))[:200]
                score = c.get("score", 0)
                filename = c.get("filename", "")
                label = f"[{c.get('index','?')}]" + (f" · {filename}" if filename else "")
                st.markdown(f'<div class="citation-card">{label}<br>{preview}</div>', unsafe_allow_html=True)
                if score:
                    st.progress(min(float(score), 1.0), text=f"Score: {score:.3f}")


def _render_response(data: dict, msg_idx: int = 0) -> None:
    """Dispatch to the correct card renderer based on intent."""
    if "error" in data:
        st.error(f"Ошибка: {data['error']}")
        return

    intent = data.get("intent", "search")
    renderers = {
        "search": _render_search_card,
        "verify": _render_verify_card,
        "generate": _render_generate_card,
        "analyze": _render_analyze_card,
    }
    renderer = renderers.get(intent, _render_search_card)
    renderer(data, msg_idx)


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar
# ──────────────────────────────────────────────────────────────────────────────

def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## Nurbank metodologist")
        st.markdown("---")

        # Agent URL + health
        st.markdown("### ⚙️ Настройки")
        agent_url = st.text_input("URL агента", value=st.session_state.agent_url, key="agent_url_input")
        if agent_url != st.session_state.agent_url:
            st.session_state.agent_url = agent_url

        if st.button("🔍 Проверить подключение"):
            if _check_health():
                st.success("✅ Агент доступен")
            else:
                st.error("❌ Агент недоступен")

        st.markdown("---")
        st.markdown("### 💬 Сессии")

        col_new, col_restore = st.columns(2)
        with col_new:
            if st.button("➕ Новая", use_container_width=True):
                _new_session()
                st.rerun()
        with col_restore:
            if st.button("🔄 История", use_container_width=True):
                _load_sessions_from_db()
                st.rerun()

        sessions = st.session_state.sessions
        if sessions:
            active = st.session_state.active_session
            for sid, info in list(sessions.items()):
                col1, col2 = st.columns([5, 1])
                with col1:
                    label = info["name"]
                    is_active = sid == active
                    btn_label = f"**{label}**" if is_active else label
                    if st.button(btn_label, key=f"sel_{sid}", use_container_width=True):
                        st.session_state.active_session = sid
                        st.rerun()
                with col2:
                    if st.button("🗑", key=f"del_{sid}"):
                        del st.session_state.sessions[sid]
                        if st.session_state.active_session == sid:
                            st.session_state.active_session = next(iter(st.session_state.sessions), None)
                        st.rerun()
        else:
            st.caption("Нет сессий. Нажмите «Новая» или «🔄 История».")

        st.markdown("---")
        st.caption("Miran — банковский ИИ для Казахстана")


# ──────────────────────────────────────────────────────────────────────────────
# Main area
# ──────────────────────────────────────────────────────────────────────────────

def _render_main() -> None:
    active = st.session_state.active_session

    if not active:
        st.markdown("## 👋 Добро пожаловать в Miran")
        st.markdown(
            "Это ИИ-ассистент для банковского и юридического домена Казахстана.\n\n"
            "Нажмите **«Новая сессия»** в боковой панели, чтобы начать."
        )
        return

    session_info = st.session_state.sessions[active]
    st.markdown(f"## 💬 {session_info['name']}")

    # Chat history
    messages = _active_messages()
    for mi, msg in enumerate(messages):
        role = msg["role"]
        with st.chat_message(role):
            if role == "assistant" and isinstance(msg.get("data"), dict):
                _render_response(msg["data"], msg_idx=mi)
            else:
                st.markdown(msg.get("content", ""))

    # Input
    if prompt := st.chat_input("Задайте вопрос по банковскому праву..."):
        # Auto-name session on first message
        if not messages:
            _auto_name_session(prompt)

        # Show user message
        _push_message({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Call agent and show response
        with st.chat_message("assistant"):
            with st.spinner("Анализирую запрос..."):
                result = _call_agent(prompt, active)

            _render_response(result, msg_idx=len(messages))

        # Save assistant message — use `or` to treat None/empty as fallback
        response_text = result.get("response") or result.get("error") or ""
        _push_message({"role": "assistant", "content": response_text, "data": result})
        st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    _init_state()
    _render_sidebar()
    _render_main()


if __name__ == "__main__":
    main()
