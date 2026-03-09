"""Ingest Agent — document upload status tracking and guidance.

Handles the "ingest" intent: user wants to upload a document or check
the processing status of an already uploaded document.

Flow:
  User uploads file via POST /api/v1/ingest
    → OCR Service (Surya) processes it (status: pending → processing → completed)
    → bank_knowledge Indexer chunks + embeds → Qdrant + Neo4j
    → langgraph-agent can now search that document

This agent helps the user:
1. Understand the current status of a document being processed.
2. Know when the document is ready to be queried.
3. Get a brief summary of what was indexed (page count, doc_id).
"""
from __future__ import annotations

import logging
import re
import time

from langchain_core.messages import AIMessage

from ..graph.state import AgentState
from ..tools.ocr_client import ocr_check_status, ocr_list_documents

logger = logging.getLogger(__name__)

# UUID4 pattern to extract doc_id from user query
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_STATUS_LABELS = {
    "pending":    "⏳ В очереди на OCR-обработку",
    "processing": "🔄 Выполняется OCR (Surya)",
    "completed":  "✅ OCR завершён, идёт индексация в базу знаний",
    "failed":     "❌ Ошибка обработки",
}

_INDEXING_LABELS = {
    "queued":     "📥 Индексация поставлена в очередь",
    "processing": "🔄 Идёт чанкинг и загрузка в Qdrant/Neo4j",
    "completed":  "✅ Документ проиндексирован — можно задавать вопросы!",
    "failed":     "⚠️  Ошибка индексации (OCR результат сохранён)",
    "unknown":    "❓ Статус индексации неизвестен",
}


def _extract_doc_id(query: str) -> str | None:
    """Extract UUID from user query if present."""
    m = _UUID_RE.search(query)
    return m.group(0) if m else None


def _format_status_response(status_data: dict) -> str:
    """Build human-readable status message."""
    if "error" in status_data:
        return f"⚠️ Не удалось получить статус: {status_data['error']}"

    doc_id   = status_data.get("doc_id", "—")
    status   = status_data.get("status", "unknown")
    filename = status_data.get("filename", "—")
    err_msg  = status_data.get("error_message", "")

    label = _STATUS_LABELS.get(status, f"Статус: {status}")
    parts = [
        f"**Документ:** `{filename}`",
        f"**ID:** `{doc_id}`",
        f"**Статус OCR:** {label}",
    ]

    if status == "completed":
        parts.append(
            "\n💡 После завершения OCR запускается индексация в Qdrant и Neo4j. "
            "Документ станет доступен для поиска через несколько минут. "
            "Вы можете задать вопрос прямо сейчас — если документ уже проиндексирован, "
            "я отвечу на него."
        )
    elif status == "failed" and err_msg:
        parts.append(f"**Ошибка:** {err_msg[:300]}")
    elif status in ("pending", "processing"):
        parts.append(
            "\n⏱ Обработка займёт от 30 секунд до нескольких минут "
            "в зависимости от размера документа."
        )

    return "\n".join(parts)


def ingest_node(state: AgentState) -> AgentState:
    """Check document processing status and guide the user."""
    t_start = time.perf_counter()
    query = state["user_query"]

    # Try to find a doc_id in the query or document_ids list
    doc_id = _extract_doc_id(query)
    if not doc_id and state.get("document_ids"):
        doc_id = state["document_ids"][0]

    if doc_id:
        # Check specific document status
        logger.info("ingest_node: checking status for doc_id=%s", doc_id)
        status_data = ocr_check_status.invoke({"doc_id": doc_id})
        response = _format_status_response(status_data)
    else:
        # No doc_id — show recent documents or upload instructions
        logger.info("ingest_node: no doc_id found, listing recent documents")
        docs = ocr_list_documents.invoke({"limit": 10})

        if isinstance(docs, list) and docs and "error" not in docs[0]:
            lines = ["**Последние загруженные документы:**\n"]
            for doc in docs[:10]:
                status = doc.get("status", "unknown")
                fname  = doc.get("filename", "—")
                did    = doc.get("doc_id", "—")
                label  = _STATUS_LABELS.get(status, status)
                lines.append(f"• `{fname}` — {label}\n  ID: `{did}`")
            response = "\n".join(lines)
            response += (
                "\n\n💡 **Как загрузить новый документ:**\n"
                "```\nPOST /api/v1/ingest\nContent-Type: multipart/form-data\nfile: <ваш PDF/PNG/JPG>\n```\n"
                "После загрузки вы получите `doc_id` — передайте его сюда для проверки статуса."
            )
        else:
            response = (
                "📤 **Загрузка документов**\n\n"
                "Для загрузки нового документа используйте:\n"
                "```\nPOST /api/v1/ingest\nContent-Type: multipart/form-data\nfile: <ваш файл>\n```\n\n"
                "Поддерживаемые форматы: **PDF, PNG, JPG, JPEG, TIFF, BMP, WEBP** (до 50 МБ).\n\n"
                "**Цепочка обработки:**\n"
                "1. 📄 **OCR** (Surya) — распознавание текста\n"
                "2. ✂️ **Chunking** — нарезка на чанки\n"
                "3. 🔢 **Embedding** (bge-m3) — векторизация\n"
                "4. 🗃 **Qdrant** — сохранение векторов\n"
                "5. 🕸 **Neo4j** — построение графа знаний\n\n"
                "После завершения индексации вы сможете задавать вопросы по документу."
            )

    elapsed = time.perf_counter() - t_start
    logger.info("ingest_node: elapsed=%.2fs doc_id=%s", elapsed, doc_id or "none")

    combined_prev = state.get("combined_responses") or []
    final_response = (
        "\n\n---\n\n".join(combined_prev) + "\n\n---\n\n" + response
        if combined_prev else response
    )

    return {
        **state,
        "ingest_result": {
            "doc_id": doc_id,
            "query": query,
        },
        "final_response": final_response,
        "retrieval_metrics": {
            "node": "ingest",
            "doc_id": doc_id,
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
