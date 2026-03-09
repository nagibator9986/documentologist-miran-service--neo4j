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
from app.services.metrics import uploads_total
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
        uploads_total.labels(is_duplicate="true").inc()
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
        uploads_total.labels(is_duplicate="true").inc()
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

    uploads_total.labels(is_duplicate="false").inc()
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
    cursor: str | None = Query(None, description="Курсор для следующей страницы (из поля next_cursor предыдущего ответа)"),
    limit: int = Query(50, ge=1, le=200),
    latest_only: bool = Query(True),
    status: DocumentStatus | None = Query(None, description="Фильтр по статусу"),
    filename: str | None = Query(None, description="Поиск по имени файла"),
    created_after: datetime | None = Query(None, description="Дата создания от"),
    created_before: datetime | None = Query(None, description="Дата создания до"),
    db: AsyncSession = Depends(get_db),
):
    """List uploaded documents with keyset (cursor-based) pagination.

    Pass the `next_cursor` value from the previous response to get the next page.
    Omit `cursor` (or set to null) to start from the first page.
    """
    svc = DocumentService(db)
    docs, total, next_cursor = await svc.list_documents(
        cursor=cursor,
        limit=limit,
        latest_only=latest_only,
        status=status,
        filename=filename,
        created_after=created_after,
        created_before=created_before,
    )
    return DocumentListResponse(
        total=total,
        documents=[DocumentResponse.model_validate(d) for d in docs],
        next_cursor=next_cursor,
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