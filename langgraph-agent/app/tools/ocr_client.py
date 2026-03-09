"""Tool: OCR Service HTTP client.

Предоставляет три инструмента для langgraph-agent:
- ocr_upload_document   — загрузить файл на OCR-сервис
- ocr_check_status      — проверить статус обработки по doc_id
- ocr_list_documents    — список всех загруженных документов
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from langchain_core.tools import tool

from ..core.config import get_settings

logger = logging.getLogger(__name__)


def _ocr_base_url() -> str:
    s = get_settings()
    return s.ocr_service_url.rstrip("/")


# Module-level singleton — reuses TCP connections across all tool calls.
# Avoids the 20-50ms TCP handshake overhead on every status/list call.
_http_client: httpx.Client | None = None
_http_client_lock = __import__("threading").Lock()


def _client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        with _http_client_lock:
            if _http_client is None:
                _http_client = httpx.Client(
                    timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                )
    return _http_client


@tool
def ocr_upload_document(file_path: str) -> dict[str, Any]:
    """Upload a local file to the OCR service for processing.

    The file will be queued for Surya OCR. After OCR completes,
    bank_knowledge automatically indexes it into Qdrant + Neo4j.

    Args:
        file_path: Absolute path to a PDF, PNG, JPG, TIFF or BMP file.

    Returns:
        dict with keys: doc_id, status, is_duplicate, message
    """
    url = f"{_ocr_base_url()}/api/v1/upload"

    if not os.path.isfile(file_path):
        return {"error": f"File not found: {file_path}"}

    try:
        with open(file_path, "rb") as fh:
            filename = os.path.basename(file_path)
            resp = _client().post(
                url,
                files={"file": (filename, fh)},
                timeout=60.0,
            )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("ocr_upload_document HTTP error: %s", exc)
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
    except Exception as exc:
        logger.error("ocr_upload_document failed: %s", exc)
        return {"error": str(exc)}


@tool
def ocr_check_status(doc_id: str) -> dict[str, Any]:
    """Check the OCR processing status of a document.

    Args:
        doc_id: Document UUID returned by ocr_upload_document.

    Returns:
        dict with keys: doc_id, status (pending/processing/completed/failed),
        filename, result_path, error_message.
    """
    url = f"{_ocr_base_url()}/api/v1/status/{doc_id}"
    try:
        resp = _client().get(url)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"}
    except Exception as exc:
        logger.error("ocr_check_status failed: %s", exc)
        return {"error": str(exc)}


@tool
def ocr_list_documents(
    status: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List documents in the OCR service.

    Args:
        status: Filter by status — pending, processing, completed, failed. Empty = all.
        limit:  Max number of results (1-200).

    Returns:
        List of document records.
    """
    url = f"{_ocr_base_url()}/api/v1/documents"
    params: dict[str, Any] = {"limit": min(limit, 200)}
    if status:
        params["status"] = status

    try:
        resp = _client().get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("documents", data) if isinstance(data, dict) else data
    except Exception as exc:
        logger.error("ocr_list_documents failed: %s", exc)
        return [{"error": str(exc)}]
