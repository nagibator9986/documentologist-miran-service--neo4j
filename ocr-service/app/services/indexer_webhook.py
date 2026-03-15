"""
Webhook: notify the bank_knowledge indexer after OCR completes.

After a document is successfully OCR-processed and saved to MinIO,
the worker calls `notify_indexer()` to trigger chunking → embedding →
Qdrant + Neo4j indexing pipeline in bank_knowledge service.

The call is fire-and-forget with a short timeout; failures are logged
but never re-raise so the OCR pipeline is never blocked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _sign_payload(body: bytes, secret: str) -> str:
    """Return HMAC-SHA256 hex digest of body signed with secret."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def notify_indexer(
    doc_id: str,
    file_hash: str,
    result_path: str,
    filename: str,
    page_count: int = 0,
) -> bool:
    """POST indexing job to the bank_knowledge indexer service.

    Args:
        doc_id:      OCR document UUID.
        file_hash:   SHA-256 hash — used as MinIO object prefix.
        result_path: Path inside 'analysis-results' bucket, e.g. 'abc123/surya_output.json'.
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

    # NOTE: MinIO credentials are NOT sent in the payload — the indexer service
    # reads them from its own environment variables (MINIO_ACCESS_KEY, MINIO_SECRET_KEY).
    # Sending secrets in HTTP request bodies is a security anti-pattern.
    payload: dict[str, Any] = {
        "doc_id": doc_id,
        "file_hash": file_hash,
        "result_path": result_path,
        "filename": filename,
        "page_count": page_count,
        "bucket_results": s.bucket_results,
    }

    body = json.dumps(payload, separators=(",", ":")).encode()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if s.webhook_secret:
        headers["X-Webhook-Signature"] = _sign_payload(body, s.webhook_secret)
    else:
        logger.warning(
            "notify_indexer: WEBHOOK_SECRET not set — sending unsigned request to %s", url
        )

    try:
        resp = httpx.post(
            url,
            content=body,
            headers=headers,
            timeout=10.0,  # short — fire-and-forget
        )
        resp.raise_for_status()
        logger.info(
            "notify_indexer: sent indexing job for doc_id=%s → %s (%d)",
            doc_id, url, resp.status_code,
        )
        return True
    except Exception as exc:
        logger.warning(
            "notify_indexer: failed to notify %s for doc_id=%s: %s (non-critical)",
            url, doc_id, exc,
        )
        return False
