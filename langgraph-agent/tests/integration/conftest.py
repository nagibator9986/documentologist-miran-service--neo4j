"""Shared fixtures for integration tests.

Run:
    python -m pytest tests/integration/ -v
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.core.config import Settings, get_settings


# ---------------------------------------------------------------------------
# mock_graph_result — factory for graph.invoke return values
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_graph_result():
    """Return a factory that builds a dict matching graph.invoke output."""

    def _make(intent: str = "search", response: str = "Тестовый ответ") -> dict[str, Any]:
        return {
            "intent": intent,
            "intents": [intent],
            "final_response": response,
            "citations": [{"filename": "test.pdf", "snippet": "..."}],
            "verify_result": {"compliant": True} if intent == "verify" else {},
            "generate_result": {"plan": "test plan"} if intent == "generate" else {},
            "analyze_result": {"summary": "test"} if intent == "analyze" else {},
            "retrieval_metrics": {"node": intent, "merged_hits": 5},
            "export_path": None,
            "session_id": "test-session",
        }

    return _make


# ---------------------------------------------------------------------------
# client — sync TestClient with mocked graph + settings
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(mock_graph_result):
    """Sync TestClient with mocked LangGraph graph and test settings.

    CRITICAL: get_settings.cache_clear() is called first to avoid lru_cache
    poisoning across tests.
    """
    get_settings.cache_clear()

    test_settings = Settings(
        mlflow_enabled=False,
        ollama_url="http://localhost:11434",
        redis_url="redis://localhost:6379",
        qdrant_url="http://localhost:6333",
        neo4j_uri="bolt://localhost:7687",
        neo4j_password="password",
        postgres_dsn="postgresql://postgres:postgres@localhost:5432/agent_memory",
    )

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = mock_graph_result("search")

    # astream must be an async generator
    async def _astream(state, *, stream_mode="updates"):
        result = mock_graph_result("search")
        yield {"supervisor": {"intent": result["intent"], "intents": result["intents"]}}
        yield {"search": result}

    mock_graph.astream = _astream

    with (
        patch("app.core.config.get_settings", return_value=test_settings),
        patch("app.api.v1.chat.graph", mock_graph),
        patch("app.core.tracing.setup_mlflow"),
        patch("app.core.tracing.register_all_prompts"),
        patch("app.core.utils.close_all_clients"),
        patch("app.core.utils.ensure_neo4j_fulltext_index"),
        patch("app.tools.session_memory.pg_ensure_schema_sync"),
        patch("app.tools.session_memory.pg_shutdown", new_callable=AsyncMock),
    ):
        from app.main import create_app

        with TestClient(create_app()) as c:
            yield c, mock_graph
