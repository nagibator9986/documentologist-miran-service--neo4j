"""Ingest endpoint — proxies file uploads to the OCR service.

POST /api/v1/ingest   — accepts a file, forwards to OCR service, returns doc_id
GET  /api/v1/ingest/{doc_id}  — proxy for status check
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from ...core.config import get_settings
from ...core.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _ocr_url() -> str:
    return get_settings().ocr_service_url.rstrip("/")


@router.post(
    "/",
    summary="Upload a document for OCR + indexing",
    response_description="OCR service response with doc_id and status",
)
@limiter.limit("10/minute")
async def upload_document(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    """Upload a PDF/image to the OCR service.

    The OCR service will:
    1. Run Surya OCR on the file
    2. Upload the structured JSON result to MinIO
    3. Notify bank_knowledge indexer → chunk → embed → Qdrant + Neo4j

    Returns the doc_id which can be used to track processing status.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    _ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff", "tif", "bmp", "webp"}
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    # Validate that the declared content_type is consistent with the extension.
    # Rejects mismatches like a .exe renamed to .pdf.
    _MIME_BY_EXT: dict[str, set[str]] = {
        "pdf":  {"application/pdf"},
        "png":  {"image/png"},
        "jpg":  {"image/jpeg"},
        "jpeg": {"image/jpeg"},
        "tiff": {"image/tiff"},
        "tif":  {"image/tiff"},
        "bmp":  {"image/bmp", "image/x-bmp"},
        "webp": {"image/webp"},
    }
    declared_mime = (file.content_type or "").split(";")[0].strip().lower()
    if declared_mime and declared_mime not in _MIME_BY_EXT.get(ext, set()):
        raise HTTPException(
            status_code=415,
            detail=f"Content-Type '{declared_mime}' does not match file extension '.{ext}'",
        )

    content = await file.read()
    s = get_settings()
    if len(content) > s.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large (max {s.max_upload_size_mb} MB)")

    url = f"{_ocr_url()}/api/v1/upload"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                files={"file": (file.filename, content, file.content_type or "application/octet-stream")},
            )
        resp.raise_for_status()
        return JSONResponse(resp.json(), status_code=resp.status_code)
    except httpx.HTTPStatusError as exc:
        logger.error("OCR upload error: %s %s", exc.response.status_code, exc.response.text[:300])
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text[:500],
        ) from exc
    except httpx.RequestError as exc:
        logger.error("OCR service unreachable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="OCR service is unavailable. Please try again later.",
        ) from exc


@router.get(
    "/{doc_id}",
    summary="Check document processing status",
)
async def get_status(doc_id: str) -> JSONResponse:
    """Check OCR + indexing status for a document by its doc_id."""
    url = f"{_ocr_url()}/api/v1/status/{doc_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return JSONResponse(resp.json())
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text[:500],
        ) from exc
    except httpx.RequestError as exc:
        logger.error("OCR service unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="OCR service is unavailable") from exc


@router.get(
    "/",
    summary="List all documents",
)
async def list_documents(status: str = "", limit: int = 20) -> JSONResponse:
    """List documents from the OCR service with optional status filter."""
    params: dict[str, object] = {"limit": min(limit, 200)}
    if status:
        params["status"] = status

    url = f"{_ocr_url()}/api/v1/documents"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
        resp.raise_for_status()
        return JSONResponse(resp.json())
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text[:500],
        ) from exc
    except httpx.RequestError as exc:
        logger.error("OCR service unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="OCR service is unavailable") from exc
