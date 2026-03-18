"""Integration tests for the generate agent.

Run:
    python -m pytest tests/integration/test_generate.py -v
"""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_generate_happy_path(client, mock_graph_result):
    """POST /api/v1/chat/ with generate query returns generate_result."""
    c, mock_graph = client
    mock_graph.invoke.return_value = mock_graph_result("generate")
    resp = c.post("/api/v1/chat/", json={
        "query": "Сгенерируй договор купли-продажи",
        "session_id": "test-session",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "generate_result" in body
