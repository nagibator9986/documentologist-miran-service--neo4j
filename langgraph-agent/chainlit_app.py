"""Chainlit UI for Miran LangGraph Agent.

Connects to the FastAPI SSE stream and renders:
  - Real-time node progress (supervisor → search → verify → generate → analyze)
  - Token-by-token response streaming
  - Citations with relevance scores
  - Verify compliance card (risk score, issues, law refs)
  - Generate document card (download)
  - Analyze card (compare table, entities, key points)
  - File upload → ingest pipeline
  - Retrieval metrics (hits, elapsed, rerank score)

Run:
    chainlit run chainlit_app.py --port 8501

Environment variables:
    AGENT_URL   — FastAPI base URL (default: http://localhost:8001)
"""
from __future__ import annotations

import json
import os
import urllib.parse
import uuid
from typing import Any

import httpx
import chainlit as cl

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8001")

# Human-readable node labels — mirrors _NODE_LABELS in app/api/v1/chat.py
_NODE_LABELS: dict[str, str] = {
    "memory_load":    "📂 Загружаю историю переписки…",
    "supervisor":     "🧠 Определяю намерение запроса…",
    "search":         "🔍 Ищу в базе знаний…",
    "verify":         "✅ Проверяю соответствие законодательству…",
    "generate":       "📝 Генерирую документ…",
    "ingest":         "📥 Проверяю статус загрузки…",
    "analyze":        "🔬 Анализирую документы…",
    "advance_intent": "➡️ Перехожу к следующей задаче…",
    "memory_save":    "💾 Сохраняю в память…",
}

_INTENT_LABELS = {
    "search":   "🔍 Поиск",
    "verify":   "✅ Проверка",
    "generate": "📝 Генерация",
    "analyze":  "🔬 Анализ",
    "ingest":   "📥 Загрузка",
}


# ── Session lifecycle ─────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_start() -> None:
    """Generate a new session ID and store it for the conversation."""
    session_id = str(uuid.uuid4())[:12]
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("user_id", "anonymous")

    await cl.Message(
        content=(
            "**Добро пожаловать в Miran — банковский ИИ-ассистент**\n\n"
            "Задайте вопрос по банковскому праву, загрузите документ "
            "или попросите сгенерировать договор.\n\n"
            f"*Сессия: `{session_id}`*"
        )
    ).send()


# ── Main message handler ──────────────────────────────────────────────────────

@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Handle user messages — file uploads and/or text queries."""
    # File upload: handle files first, then still process text if present
    if message.elements:
        await _handle_file_upload(message)
        # If the message has no text body, stop here
        if not message.content.strip():
            return

    session_id = cl.user_session.get("session_id")
    user_id    = cl.user_session.get("user_id", "anonymous")

    payload = {
        "query":      message.content,
        "session_id": session_id,
        "user_id":    user_id,
    }

    # Open empty message that will receive streamed tokens
    response_msg  = cl.Message(content="")
    await response_msg.send()

    done_data:     dict[str, Any] = {}
    active_steps:  dict[str, cl.Step] = {}
    current_event: str = ""  # SSE event type buffer

    try:
        async with httpx.AsyncClient(timeout=250) as client:
            async with client.stream(
                "POST",
                f"{AGENT_URL}/api/v1/chat/stream",
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as resp:
                resp.raise_for_status()

                async for line in resp.aiter_lines():
                    # "event: <type>" sets the type for the following data line
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                        continue

                    if not line.startswith("data: "):
                        continue

                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    event_type    = current_event
                    current_event = ""  # reset after consuming

                    # ── status ────────────────────────────────────────────────
                    if event_type == "status":
                        pass  # initial event — no UI action needed

                    # ── progress — one event per completed LangGraph node ─────
                    elif event_type == "progress":
                        node  = data.get("node", "")
                        label = _NODE_LABELS.get(node, node)

                        # Close all previously open steps
                        await _close_steps(active_steps)

                        step = cl.Step(name=label, show_input=False)
                        await step.send()
                        active_steps[node] = step

                    # ── intent — supervisor classified the request ─────────────
                    elif event_type == "intent":
                        intent  = data.get("intent", "")
                        intents = data.get("intents", [intent])
                        label   = " + ".join(_INTENT_LABELS.get(i, i) for i in intents if i)
                        if label:
                            await cl.Message(content=f"**Тип запроса:** {label}").send()

                    # ── token — word-by-word final response ───────────────────
                    elif event_type == "token":
                        await response_msg.stream_token(data.get("text", ""))

                    # ── done — full result payload ────────────────────────────
                    elif event_type == "done":
                        done_data = data
                        await _close_steps(active_steps)

                    # ── error ─────────────────────────────────────────────────
                    elif event_type == "error":
                        await _close_steps(active_steps)
                        await cl.Message(
                            content=f"❌ **Ошибка:** {data.get('message', 'Неизвестная ошибка')}",
                        ).send()
                        return

    except httpx.ConnectError:
        await cl.Message(
            content=(
                "❌ Не удалось подключиться к агенту. "
                f"Убедитесь что сервис запущен на `{AGENT_URL}`."
            ),
        ).send()
        return
    except Exception as exc:
        await cl.Message(content=f"❌ Ошибка соединения: {exc}").send()
        return

    await response_msg.update()

    # ── Rich cards — rendered for each intent in the intents list ────────────
    intents = done_data.get("intents") or [done_data.get("intent", "search")]

    for intent in intents:
        if intent == "verify" and done_data.get("verify_result"):
            await _render_verify_card(done_data["verify_result"])

        if intent == "generate":
            await _render_generate_card(done_data)

        if intent == "analyze" and done_data.get("analyze_result"):
            await _render_analyze_card(done_data["analyze_result"])

    if done_data.get("citations"):
        await _render_citations(done_data["citations"])

    if done_data.get("retrieval_metrics"):
        await _render_metrics(done_data["retrieval_metrics"])


# ── File upload → ingest ──────────────────────────────────────────────────────

async def _handle_file_upload(message: cl.Message) -> None:
    """Send uploaded files to the ingest endpoint."""
    session_id = cl.user_session.get("session_id")

    for element in message.elements:
        if not isinstance(element, cl.File):
            continue

        await cl.Message(content=f"📥 Загружаю `{element.name}`…").send()

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                with open(element.path, "rb") as f:
                    resp = await client.post(
                        f"{AGENT_URL}/api/v1/ingest/upload",
                        files={"file": (element.name, f, element.mime or "application/octet-stream")},
                        data={"session_id": session_id},
                    )
                resp.raise_for_status()

            await cl.Message(
                content=(
                    f"✅ **`{element.name}`** успешно загружен и проиндексирован.\n\n"
                    "Теперь можно задавать вопросы по этому документу."
                )
            ).send()

        except Exception as exc:
            await cl.Message(
                content=f"❌ Ошибка при загрузке `{element.name}`: {exc}"
            ).send()


# ── Verify card ───────────────────────────────────────────────────────────────

async def _render_verify_card(vr: dict) -> None:
    """Compliance check result — risk score, issues, fix hints, law refs."""
    compliant  = vr.get("compliant")
    risk_score = float(vr.get("risk_score", 0))

    if compliant is True:
        status_line = "✅ Документ соответствует законодательству"
    elif compliant is False:
        status_line = "❌ Выявлены нарушения / риски"
    else:
        status_line = "⚠️ Соответствие не определено"

    risk_bar   = "🟥" * int(risk_score) + "⬜" * (10 - int(risk_score))
    risk_level = "низкий" if risk_score <= 3 else ("средний" if risk_score <= 6 else "высокий")

    parts = [
        f"### {status_line}",
        f"**Риск-оценка:** {risk_score:.0f}/10  {risk_bar}  _{risk_level}_",
    ]

    issues = vr.get("issues", [])
    if issues and not (len(issues) == 1 and "Не удалось" in issues[0]):
        parts.append("**⚠️ Нарушения и риски:**\n" + "\n".join(f"- {i}" for i in issues))

    hints = vr.get("fix_hints", [])
    if hints:
        parts.append("**💡 Рекомендации по исправлению:**\n" + "\n".join(f"- {h}" for h in hints))

    law_refs = vr.get("law_refs", [])
    if law_refs:
        parts.append("**📜 Ссылки на НПА:**\n" + "\n".join(f"- {r}" for r in law_refs))

    await cl.Message(content="\n\n".join(parts)).send()


# ── Generate card ─────────────────────────────────────────────────────────────

async def _render_generate_card(done_data: dict) -> None:
    """Offer download for the generated document."""
    export_path = done_data.get("export_path") or ""
    gr          = done_data.get("generate_result") or {}
    title       = gr.get("title") or "Документ"

    if not export_path:
        return

    filename = export_path.split("/")[-1] if "/" in export_path else export_path

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{AGENT_URL}/api/v1/documents/{urllib.parse.quote(filename)}/download"
            )
            if resp.status_code == 200:
                await cl.Message(
                    content=f"📄 **{title}** готов к скачиванию",
                    elements=[cl.File(name=filename, content=resp.content)],
                ).send()
                return
    except Exception:
        pass

    await cl.Message(content=f"📁 Документ сохранён на сервере: `{export_path}`").send()


# ── Analyze card ──────────────────────────────────────────────────────────────

async def _render_analyze_card(ar: dict) -> None:
    """Render analysis result — key points, compare table, entities."""
    task       = ar.get("task", "qa")
    confidence = float(ar.get("confidence", 0.5))
    conf_bar   = _score_bar(confidence)

    parts = [f"**Уверенность:** {conf_bar} `{confidence:.0%}`"]

    key_points = ar.get("key_points", [])
    if key_points:
        parts.append("**🔑 Ключевые тезисы:**\n" + "\n".join(f"- {p}" for p in key_points))

    if task == "compare":
        similarities = ar.get("similarities", [])
        differences  = ar.get("differences", [])
        if similarities:
            parts.append("**🟰 Сходства:**\n" + "\n".join(f"- {s}" for s in similarities))
        if differences:
            parts.append("**↔️ Различия:**\n" + "\n".join(f"- {d}" for d in differences))
        conflicts = ar.get("legal_conflicts", [])
        if conflicts:
            parts.append("**⚖️ Юридические противоречия:**\n" + "\n".join(f"- {c}" for c in conflicts))

    entities = ar.get("entities", [])
    if entities:
        ent_lines = [
            f"`{e.get('value', e.get('name', ''))}` [{e.get('type', '')}]"
            for e in entities if e.get("value") or e.get("name")
        ]
        if ent_lines:
            parts.append("**🏷 Сущности:** " + "  ".join(ent_lines))

    recommendations = ar.get("recommendations", [])
    if recommendations:
        parts.append("**💡 Рекомендации:**\n" + "\n".join(f"- {r}" for r in recommendations))

    await cl.Message(content="\n\n".join(parts)).send()


# ── Citations ─────────────────────────────────────────────────────────────────

async def _render_citations(citations: list[dict]) -> None:
    """Source citations with relevance scores and exact-match markers."""
    if not citations:
        return

    lines = ["### 📎 Источники"]

    for c in citations:
        fn         = c.get("filename", "")
        score      = float(c.get("score", 0))
        preview    = c.get("content_preview", c.get("content", ""))[:150]
        section    = c.get("section", "")
        page       = c.get("page_number")
        idx        = c.get("index", "?")
        is_exact   = c.get("is_exact_match", False)
        exact_mark = " 🎯" if is_exact else ""

        location = " · ".join(
            p for p in [fn, f"стр.{page}" if page else None, section] if p
        )
        score_bar = _score_bar(score)

        lines.append(
            f"**[{idx}]{exact_mark}** {location}\n"
            f"{score_bar} `{score:.2f}`\n"
            f"> {preview}"
        )

    await cl.Message(content="\n\n".join(lines)).send()


# ── Retrieval metrics ─────────────────────────────────────────────────────────

async def _render_metrics(metrics: dict) -> None:
    """Compact pipeline stats line."""
    parts: list[str] = []

    if (elapsed := metrics.get("elapsed_s")) is not None:
        parts.append(f"⏱ `{elapsed}с`")
    if (score := metrics.get("best_rerank_score")) is not None:
        parts.append(f"🎯 rerank `{score:.2f}`")
    if (hits := metrics.get("merged_hits")) is not None:
        parts.append(f"📄 hits `{hits}`")
    if (graph_hits := metrics.get("graph_hits")) is not None and graph_hits:
        parts.append(f"🕸 graph `{graph_hits}`")
    if metrics.get("is_exact_search"):
        parts.append("🔎 точный поиск")
    if metrics.get("query_expanded"):
        parts.append("✨ запрос расширен")

    if parts:
        await cl.Message(content="*" + " · ".join(parts) + "*").send()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _score_bar(score: float, width: int = 10) -> str:
    """Convert 0–1 score to an emoji progress bar."""
    filled = round(min(max(score, 0.0), 1.0) * width)
    return "🟦" * filled + "⬜" * (width - filled)


async def _close_steps(active_steps: dict[str, cl.Step]) -> None:
    """Mark all open steps as finished and clear the registry."""
    for step in active_steps.values():
        await step.update()
    active_steps.clear()
