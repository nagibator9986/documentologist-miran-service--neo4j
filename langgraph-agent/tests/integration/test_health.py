"""Integration tests for health endpoints.

Run:
    python -m pytest tests/integration/test_health.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.integration
def test_health_liveness(client):
    """GET /health returns 200 with status ok."""
    c, _mock_graph = client
    resp = c.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


@pytest.mark.integration
def test_health_detailed_all_ok(client):
    """GET /health/detailed returns 200 with all services ok (including ollama)."""
    c, _mock_graph = client

    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = []

    mock_neo4j = MagicMock()
    mock_neo4j.verify_connectivity.return_value = None

    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    mock_httpx_resp = MagicMock()
    mock_httpx_resp.raise_for_status.return_value = None

    with (
        patch("app.core.utils.get_qdrant_client", return_value=mock_qdrant),
        patch("app.core.utils.get_neo4j_driver", return_value=mock_neo4j),
        patch("redis.from_url", return_value=mock_redis),
        patch("httpx.get", return_value=mock_httpx_resp),
    ):
        resp = c.get("/health/detailed")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["qdrant"] == "ok"
    assert body["neo4j"] == "ok"
    assert body["redis"] == "ok"
    assert body["ollama"] == "ok"


@pytest.mark.integration
def test_health_detailed_ollama_down(client):
    """GET /health/detailed with Ollama unreachable reports error for ollama key."""
    c, _mock_graph = client

    mock_qdrant = MagicMock()
    mock_qdrant.get_collections.return_value = []

    mock_neo4j = MagicMock()
    mock_neo4j.verify_connectivity.return_value = None

    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    with (
        patch("app.core.utils.get_qdrant_client", return_value=mock_qdrant),
        patch("app.core.utils.get_neo4j_driver", return_value=mock_neo4j),
        patch("redis.from_url", return_value=mock_redis),
        patch("httpx.get", side_effect=ConnectionError("Ollama is down")),
    ):
        resp = c.get("/health/detailed")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ollama"].startswith("error:")
