"""Integration tests for the verify agent.

Run:
    python -m pytest tests/integration/test_verify.py -v
"""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_verify_happy_path(client, mock_graph_result):
    """POST /api/v1/chat/ with verify query returns verify_result."""
    c, mock_graph = client
    mock_graph.invoke.return_value = mock_graph_result("verify")
    resp = c.post("/api/v1/chat/", json={
        "query": "Проверь договор на соответствие",
        "session_id": "test-session",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "verify_result" in body
