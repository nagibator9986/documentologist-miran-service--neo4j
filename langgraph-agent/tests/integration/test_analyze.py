"""Integration tests for the analyze agent.

Run:
    python -m pytest tests/integration/test_analyze.py -v
"""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_analyze_qa_happy_path(client, mock_graph_result):
    """POST /api/v1/chat/ with analyze query returns analyze intent."""
    c, mock_graph = client
    mock_graph.invoke.return_value = mock_graph_result("analyze")
    resp = c.post("/api/v1/chat/", json={
        "query": "Проанализируй договор",
        "session_id": "test-session",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "analyze"


@pytest.mark.integration
def test_analyze_compare_happy_path(client, mock_graph_result):
    """POST /api/v1/chat/ with compare query returns analyze intent."""
    c, mock_graph = client
    mock_graph.invoke.return_value = mock_graph_result("analyze")
    resp = c.post("/api/v1/chat/", json={
        "query": "Сравни два документа",
        "session_id": "test-session",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "analyze"
