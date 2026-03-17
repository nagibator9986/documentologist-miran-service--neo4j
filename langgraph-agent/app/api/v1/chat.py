"""FastAPI v1 chat endpoints — REST, SSE streaming, WebSocket.

SSE streaming improvement:
- Uses graph.astream(stream_mode="updates") to emit a progress event after
  each LangGraph node completes, so the user sees real-time status instead of
  a silent spinner for 60-180 seconds.
- After the graph finishes, the final response is streamed word-by-word.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, field_validator

from ...core.config import get_settings
from ...core.rate_limit import limiter
from ...graph.workflow import graph

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_@.\-]{1,128}$")

# Human-readable labels shown to the user as each node starts
_NODE_LABELS: dict[str, str] = {
    "memory_load":    "Загружаю историю переписки…",
    "supervisor":     "Определяю намерение запроса…",
    "search":         "Ищу в базе знаний (векторный + граф)…",
    "verify":         "Проверяю соответствие законодательству…",
    "generate":       "Генерирую документ (план → черновик → проверка)…",
    "ingest":         "Проверяю статус загрузки документа…",
    "analyze":        "Анализирую документы…",
    "advance_intent": "Перехожу к следующей задаче…",
    "memory_save":    "Сохраняю в память…",
}


# ──────────────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000, description="User message")
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = Field(default="anonymous")
    document_ids: list[str] = Field(default_factory=list)
    stream: bool = Field(default=False)

    @field_validator("query")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        v = v.replace("\x00", "").strip()
        if not v:
            raise ValueError("Query must not be blank after stripping")
        return v

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError("session_id must be alphanumeric with hyphens/underscores only")
        return v

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError("user_id must be alphanumeric with hyphens/underscores/dots/@")
        return v


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    intents: list[str] = []
    response: str
    citations: list[dict] = []
    export_path: str | None = None
    verify_result: dict | None = None
    generate_result: dict | None = None
    analyze_result: dict | None = None
    retrieval_metrics: dict | None = None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_initial_state(req: ChatRequest) -> dict:
    return {
        "messages": [HumanMessage(content=req.query)],
        "user_query": req.query,
        "session_id": req.session_id,
        "user_id": req.user_id,
        "intent": "",
        "intents": [],
        "tier": "",
        "document_ids": req.document_ids,
        "query_expanded": "",
        "vector_hits": [],
        "bm25_hits": [],
        "graph_hits": [],
        "reranked_docs": [],
        "ingest_result": {},
        "search_result": "",
        "verify_result": {},
        "generate_result": {},
        "analyze_result": {},
        "combined_responses": [],
        "final_response": "",
        "citations": [],
        "export_path": None,
        "retrieval_metrics": {},
    }


async def _run_graph_async(initial_state: dict) -> dict:
    """Run LangGraph synchronously in a thread pool to avoid blocking the event loop."""
    s = get_settings()
    loop = asyncio.get_event_loop()
    coro = loop.run_in_executor(None, graph.invoke, initial_state)
    return await asyncio.wait_for(coro, timeout=s.graph_timeout_seconds)


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ──────────────────────────────────────────────────────────────────────────────
# SSE streaming — real node-level progress via graph.astream
# ──────────────────────────────────────────────────────────────────────────────

async def _sse_stream(initial_state: dict) -> AsyncGenerator[str, None]:
    """Yield SSE events with real progress as each LangGraph node completes.

    Event sequence:
      status   — initial "processing" message
      progress — one event per completed node (with human-readable label)
      intent   — after supervisor classifies the intent
      token    — final response streamed word-by-word
      done     — final state payload (citations, verify_result, etc.)
      error    — on timeout or exception
    """
    s = get_settings()
    yield _sse_event("status", {"message": "Обрабатываю запрос…"})

    final_state: dict = {}

    try:
        # graph.astream with stream_mode="updates" yields {node_name: state_delta}
        # after every node completes — giving us real progress without token-level streaming.
        async with asyncio.timeout(s.graph_timeout_seconds):
            async for chunk in graph.astream(initial_state, stream_mode="updates"):
                for node_name, state_update in chunk.items():
                    if node_name == "__end__":
                        continue

                    label = _NODE_LABELS.get(node_name, node_name)
                    yield _sse_event("progress", {"node": node_name, "message": label})

                    # Merge state updates (simple fields only — messages reducer handled by graph)
                    if isinstance(state_update, dict):
                        final_state.update(state_update)

                    # As soon as the supervisor finishes, we know the intent
                    if node_name == "supervisor":
                        intent = final_state.get("intent", "")
                        intents = final_state.get("intents", [intent] if intent else [])
                        if intent:
                            yield _sse_event("intent", {
                                "intent": intent,
                                "intents": intents,
                            })

                    await asyncio.sleep(0)  # yield to event loop between nodes

    except TimeoutError:
        yield _sse_event("error", {
            "message": f"Превышен лимит времени ({s.graph_timeout_seconds}с). Попробуйте ещё раз."
        })
        return
    except Exception as exc:
        logger.exception("Graph error during SSE streaming")
        yield _sse_event("error", {"message": str(exc)})
        return

    # Stream final response word-by-word for smooth UX
    response = final_state.get("final_response", "")
    if response:
        words = response.split(" ")
        chunk_words: list[str] = []
        for word in words:
            chunk_words.append(word)
            if len(chunk_words) >= s.sse_word_chunk_size:
                yield _sse_event("token", {"text": " ".join(chunk_words) + " "})
                chunk_words = []
                await asyncio.sleep(0)
        if chunk_words:
            yield _sse_event("token", {"text": " ".join(chunk_words)})

    intent = final_state.get("intent", initial_state.get("intent", "search"))
    yield _sse_event("done", {
        "session_id":       initial_state["session_id"],
        "intent":           intent,
        "intents":          final_state.get("intents", [intent]),
        "citations":        final_state.get("citations", []),
        "export_path":      final_state.get("export_path"),
        "verify_result":    final_state.get("verify_result") or None,
        "generate_result":  final_state.get("generate_result") or None,
        "analyze_result":   final_state.get("analyze_result") or None,
        "retrieval_metrics": final_state.get("retrieval_metrics") or None,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/", response_model=ChatResponse, summary="Single-turn chat (REST)")
@limiter.limit("30/minute")
async def chat(request: Request, req: ChatRequest) -> ChatResponse:
    """Send a message and receive a complete response."""
    if req.stream:
        raise HTTPException(
            status_code=400,
            detail="Use POST /chat/stream or WebSocket /chat/ws for streaming.",
        )

    initial_state = _build_initial_state(req)

    try:
        result = await _run_graph_async(initial_state)
    except asyncio.TimeoutError:
        s = get_settings()
        logger.error("Graph execution timed out after %ds", s.graph_timeout_seconds)
        raise HTTPException(
            status_code=504,
            detail=f"Запрос превысил лимит времени ({s.graph_timeout_seconds}с). Попробуйте ещё раз.",
        )
    except Exception as exc:
        logger.exception("Graph execution failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    intent = result.get("intent", "search")
    return ChatResponse(
        session_id=req.session_id,
        intent=intent,
        intents=result.get("intents", [intent]),
        response=result.get("final_response", ""),
        citations=result.get("citations", []),
        export_path=result.get("export_path"),
        verify_result=result.get("verify_result") or None,
        generate_result=result.get("generate_result") or None,
        analyze_result=result.get("analyze_result") or None,
        retrieval_metrics=result.get("retrieval_metrics") or None,
    )


@router.post("/stream", summary="Streaming chat via SSE")
@limiter.limit("30/minute")
async def chat_stream(request: Request, req: ChatRequest) -> StreamingResponse:
    """Send a message and receive a streamed SSE response with node-level progress."""
    initial_state = _build_initial_state(req)
    return StreamingResponse(
        _sse_stream(initial_state),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws")
async def chat_websocket(ws: WebSocket) -> None:
    """WebSocket endpoint for bidirectional real-time chat."""
    await ws.accept()
    logger.info("WebSocket connected: %s", ws.client)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"event": "error", "message": "Invalid JSON"})
                continue

            try:
                req = ChatRequest(**payload)
            except Exception as exc:
                await ws.send_json({"event": "error", "message": f"Invalid request: {exc}"})
                continue

            initial_state = _build_initial_state(req)
            await ws.send_json({"event": "status", "message": "Обрабатываю…"})

            try:
                result = await _run_graph_async(initial_state)
            except asyncio.TimeoutError:
                s = get_settings()
                await ws.send_json({
                    "event": "error",
                    "message": f"Превышен лимит времени ({s.graph_timeout_seconds}с)",
                })
                continue
            except Exception as exc:
                logger.exception("Graph error in WebSocket")
                await ws.send_json({"event": "error", "message": str(exc)})
                continue

            intent = result.get("intent", "search")
            await ws.send_json({
                "event": "done",
                "session_id": req.session_id,
                "intent": intent,
                "intents": result.get("intents", [intent]),
                "response": result.get("final_response", ""),
                "citations": result.get("citations", []),
                "export_path": result.get("export_path"),
                "verify_result": result.get("verify_result") or None,
                "retrieval_metrics": result.get("retrieval_metrics") or None,
            })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: %s", ws.client)
