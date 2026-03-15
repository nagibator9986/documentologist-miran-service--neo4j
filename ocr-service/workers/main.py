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
import uuid as _uuid

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger

from app.core.config import get_settings
from app.core.database import get_sync_db

settings = get_settings()


def _mark_pending_as_failed(doc_id: str, reason: str) -> None:
    """Последний рубеж: если pipeline упал ДО task_initialize, документ застрянет
    в PENDING навсегда. Эта функция принудительно переводит его в FAILED.

    Используется только когда doc.status == PENDING (WHERE-условие в UPDATE),
    так что она безопасна для вызова даже если task_initialize уже отработал.
    """
    from datetime import datetime, timezone

    from sqlalchemy import update as sa_update

    from app.models.document import Document, DocumentStatus

    try:
        with get_sync_db() as session:
            session.execute(
                sa_update(Document)
                .where(Document.id == _uuid.UUID(doc_id))
                .where(Document.status == DocumentStatus.PENDING)
                .values(
                    status=DocumentStatus.FAILED,
                    error_message=f"Pipeline прерван до старта: {reason[:500]}",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
    except Exception as db_err:
        logger.error(
            f"[Worker] Не удалось обновить PENDING→FAILED для {doc_id}: {db_err}"
        )


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
    """Load Surya models into memory once at startup.

    Previously this also ran a dummy inference to trigger PyTorch JIT
    compilation, but that spiked peak RAM to ~2.7 GB and caused the
    Linux OOM killer to terminate the process before any document could
    be processed (exit 137, restart loop).

    Loading the models alone is sufficient: they stay resident between
    documents, eliminating the 10–30 s load overhead on every request.
    The JIT overhead on the very first real document (~5–10 s extra) is
    an acceptable trade-off.
    """
    from app.services.ocr import get_ocr_service

    logger.info("[Warmup] Загрузка OCR-моделей в память (без dummy inference)…")
    get_ocr_service()
    logger.info("[Warmup] ✅ Модели загружены и готовы к работе")


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
                # Pipeline should have marked doc as FAILED internally via its own
                # try/except (pipeline.py). But if it crashed BEFORE task_initialize
                # ran (e.g. Prefect API down, import error), the doc stays PENDING
                # forever. _mark_pending_as_failed covers that gap — it only acts if
                # status is still PENDING (WHERE condition), so it's safe in all cases.
                logger.error(f"[Worker] Pipeline error для {doc_id}: {exc}")
                _mark_pending_as_failed(doc_id, str(exc))
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
