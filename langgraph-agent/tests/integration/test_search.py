"""Integration tests for the search agent.

Run:
    python -m pytest tests/integration/test_search.py -v
"""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_search_happy_path(client):
    """POST /api/v1/chat/ with a search query returns 200 with intent + response."""
    c, _mock_graph = client
    resp = c.post("/api/v1/chat/", json={
        "query": "Что такое овердрафт?",
        "session_id": "test-session",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "intent" in body
    assert len(body["response"]) > 0
    assert isinstance(body["citations"], list)


@pytest.mark.integration
def test_search_empty_query(client):
    """POST /api/v1/chat/ with empty query returns 422 validation error."""
    c, _mock_graph = client
    resp = c.post("/api/v1/chat/", json={
        "query": "",
        "session_id": "test-session",
    })
    assert resp.status_code == 422
