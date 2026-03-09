"""
Prefect 3.0 Flow: document_processing_pipeline

Steps:
1. Initialize       — verify resources, set status to 'processing'
2. OCR Analysis     — run Surya detection + recognition
3. Data Structuring — build final JSON with metadata
4. Save & Update    — upload JSON to MinIO, mark 'completed'
5. Notify Indexer   — POST to bank_knowledge → chunk → embed → Qdrant + Neo4j
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


# ─────────────────────────────────────────────────────────────
# Helper: sync DB update
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
    timeout_seconds=settings.ocr_init_timeout_seconds,
    log_prints=True,
)
def task_initialize(doc_id: str, file_hash: str) -> dict:
    """Step 1: Mark document as 'processing' and verify resources."""
    logger.info(f"[Initialize] doc_id={doc_id}, hash={file_hash}")
    _update_document_status(doc_id, DocumentStatus.PROCESSING)

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
    """Step 2: Download file from MinIO → run Surya OCR pipeline."""
    doc_id = ctx["doc_id"]

    logger.info(f"[OCR] Starting analysis for {doc_id}")

    from app.services.storage import get_minio_service
    from app.services.ocr import get_ocr_service
    from app.models.document import Document

    with get_sync_db() as session:
        doc = session.get(Document, uuid.UUID(doc_id))
        if doc is None:
            raise ValueError(f"Document {doc_id} not found in DB")
        s3_path = doc.s3_path
        filename = doc.filename

    minio = get_minio_service()
    file_bytes = minio.download_source_file(s3_path)
    logger.info(f"[OCR] Downloaded {len(file_bytes)} bytes from MinIO")

    with concurrency_context(settings.ocr_concurrency_slot, occupy=1):
        ocr = get_ocr_service()
        result = ocr.process_document(file_bytes, filename)

    logger.info(f"[OCR] Done — {result.get('page_count', 0)} pages processed")
    ctx["ocr_result"] = result
    ctx["filename"] = filename
    return ctx


@task(
    name="data_structuring",
    timeout_seconds=settings.ocr_structure_timeout_seconds,
    log_prints=True,
)
def task_data_structuring(ctx: dict) -> dict:
    """Step 3: Validate and finalise the structured JSON object."""
    result = ctx["ocr_result"]
    doc_id = ctx["doc_id"]

    logger.info(f"[Structure] Finalising JSON for {doc_id}")

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
    timeout_seconds=settings.ocr_save_timeout_seconds,
    log_prints=True,
)
def task_save_and_update(ctx: dict) -> dict:
    """Step 4: Upload result JSON to MinIO, update DB status → completed."""
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
    ctx["result_path"] = result_path
    return ctx


@task(
    name="notify_indexer",
    retries=2,
    retry_delay_seconds=30,
    log_prints=True,
)
def task_notify_indexer(ctx: dict) -> bool:
    """Step 5: Notify bank_knowledge indexer → chunk → embed → Qdrant + Neo4j.

    Non-fatal: OCR is already COMPLETED at this point.
    Failures are logged + retried but never mark the doc as failed.
    """
    from app.services.indexer_webhook import notify_indexer

    ok = notify_indexer(
        doc_id=ctx["doc_id"],
        file_hash=ctx["file_hash"],
        result_path=ctx.get("result_path", ""),
        filename=ctx.get("filename", ""),
        page_count=ctx.get("page_count", 0),
    )
    if ok:
        logger.info(f"[Indexer] ✅ Indexing job accepted for {ctx['doc_id']}")
    else:
        logger.warning(
            f"[Indexer] ⚠️  Indexer not notified for {ctx['doc_id']} "
            "(INDEXER_WEBHOOK_URL not set or service unavailable)"
        )
    return ok


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
    Main pipeline:
    Initialize → OCR Analysis → Data Structuring → Save & Update → Notify Indexer

    On failure before Save → status = 'failed'.
    Notify Indexer failure is non-fatal (OCR already completed).
    """
    try:
        ctx = task_initialize(doc_id, file_hash)
        ctx = task_ocr_analysis(ctx)
        ctx = task_data_structuring(ctx)
        ctx = task_save_and_update(ctx)
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

    # Step 5 outside try/except — OCR already succeeded
    try:
        task_notify_indexer(ctx)
    except Exception as e:
        logger.warning(f"[Indexer] Notify failed for {doc_id} (non-critical): {e}")

    return doc_id
