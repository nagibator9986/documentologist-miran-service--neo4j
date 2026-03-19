"""
Webhook: notify the bank_knowledge indexer after OCR completes.

After a document is successfully OCR-processed and saved to MinIO,
the worker calls ``notify_indexer()`` to trigger chunking → embedding →
Qdrant + Neo4j indexing pipeline in bank_knowledge service.

Retry strategy:
  - 3 attempts with exponential backoff (2s, 4s, 8s).
  - Handles race conditions where bank_knowledge starts after OCR worker.
  - Failures are logged but never re-raise so the OCR pipeline is never blocked.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Retry configuration
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds: 2, 4, 8


def notify_indexer(
    doc_id: str,
    file_hash: str,
    result_path: str,
    filename: str,
    page_count: int = 0,
) -> bool:
    """POST indexing job to the bank_knowledge indexer service.

    Retries up to 3 times with exponential backoff to handle race conditions
    where bank_knowledge service starts after OCR worker.

    Args:
        doc_id:      OCR document UUID.
        file_hash:   SHA-256 hash — used as MinIO object prefix.
        result_path: Path inside 'analysis-results' bucket.
        filename:    Original uploaded filename.
        page_count:  Number of pages (informational).

    Returns:
        True if indexer acknowledged the request (HTTP 200/202), False otherwise.
    """
    s = get_settings()
    url = s.indexer_webhook_url
    if not url:
        logger.info(
            "notify_indexer: INDEXER_WEBHOOK_URL not set — skipping (indexing disabled)"
        )
        return False

    payload: dict[str, Any] = {
        "doc_id": doc_id,
        "file_hash": file_hash,
        "result_path": result_path,
        "filename": filename,
        "page_count": page_count,
        "bucket_results": s.bucket_results,
    }

    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.post(
                url,
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            logger.info(
                "notify_indexer: sent indexing job for doc_id=%s → %s (%d) [attempt %d]",
                doc_id, url, resp.status_code, attempt,
            )
            return True
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Service not ready yet — retry with backoff
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _BACKOFF_BASE ** attempt
                logger.warning(
                    "notify_indexer: connection failed for doc_id=%s (attempt %d/%d), "
                    "retrying in %.0fs: %s",
                    doc_id, attempt, _MAX_RETRIES, delay, exc,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    "notify_indexer: connection failed for doc_id=%s after %d attempts: %s",
                    doc_id, _MAX_RETRIES, exc,
                )
        except httpx.HTTPStatusError as exc:
            # Server responded with error — don't retry (it received the request)
            logger.warning(
                "notify_indexer: indexer returned %d for doc_id=%s: %s (non-critical)",
                exc.response.status_code, doc_id, exc,
            )
            return False
        except Exception as exc:
            # Unexpected error — log and stop
            last_exc = exc
            logger.warning(
                "notify_indexer: unexpected error for doc_id=%s (attempt %d): %s (non-critical)",
                doc_id, attempt, exc,
            )
            break

    logger.warning(
        "notify_indexer: exhausted %d retries for doc_id=%s, last error: %s",
        _MAX_RETRIES, doc_id, last_exc,
    )
    return False
