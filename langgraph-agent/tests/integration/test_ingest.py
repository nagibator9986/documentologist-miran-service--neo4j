"""Integration tests for the ingest endpoint.

Run:
    python -m pytest tests/integration/test_ingest.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest


@pytest.mark.integration
def test_ingest_no_file(client):
    """POST /api/v1/ingest/ with no file returns 422."""
    c, _mock_graph = client
    resp = c.post("/api/v1/ingest/")
    assert resp.status_code == 422


@pytest.mark.integration
def test_ingest_upload_pdf(client):
    """POST /api/v1/ingest/ with a small PDF proxies to OCR and returns 200."""
    c, _mock_graph = client

    # Minimal valid PDF header (enough for the extension/MIME check)
    pdf_bytes = b"%PDF-1.4 test content"

    mock_response = httpx.Response(
        status_code=200,
        json={"doc_id": "test-123", "status": "processing"},
        request=httpx.Request("POST", "http://localhost:8000/api/v1/upload"),
    )

    with patch("app.api.v1.ingest.httpx.AsyncClient") as mock_client_cls:
        mock_ctx = AsyncMock()
        mock_ctx.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = c.post(
            "/api/v1/ingest/",
            files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["doc_id"] == "test-123"
