"""OpenAI-compatible Chat Completions endpoint.

POST /v1/chat/completions  (streaming and non-streaming)

Maps the OpenAI ChatCompletion API to the internal LangGraph agent so any
OpenAI-compatible client (openai SDK, OpenWebUI, Chainlit, etc.) can connect
without touching the custom REST/SSE endpoints.

Session handling (priority order):
  1. X-Session-ID request header
  2. system message with prefix  "session_id:<value>"
  3. Auto-generated short UUID

Metadata sentinel:
  After the text response the assistant content includes a hidden JSON block:
    \\n\\n⟦MIRAN:{...}⟧
  Aware clients (Chainlit) split on this sentinel to render rich cards.
  Unaware clients (OpenWebUI, curl) simply display it as-is or can strip it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from ...core.config import get_settings
from ...core.rate_limit import limiter
from ...graph.workflow import graph

logger = logging.getLogger(__name__)

router = APIRouter(tags=["openai-compat"])

# Sentinel wrapping agent metadata appended after the text response.
# Chainlit and other aware clients split on _META_START to extract rich data.
_META_START = "\n\n⟦MIRAN:"
_META_END = "⟧"


# ── Schemas ───────────────────────────────────────────────────────────────────

class CompletionMessage(BaseModel):
    role: str
    content: str = ""
    name: str | None = None


class CompletionRequest(BaseModel):
    model: str = Field(default="miran")
    messages: list[CompletionMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    user: str | None = None  # mapped to user_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_session(
    messages: list[CompletionMessage],
    header_session: str | None,
) -> str:
    if header_session:
        return header_session[:128]
    for msg in messages:
        if msg.role == "system" and msg.content.startswith("session_id:"):
            return msg.content.split(":", 1)[1].strip()[:128]
    return str(uuid.uuid4())[:12]


def _extract_query(messages: list[CompletionMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user" and msg.content.strip():
            return msg.content.strip()
    raise HTTPException(status_code=400, detail="No user message found in messages list.")


def _build_state(query: str, session_id: str, user_id: str) -> dict:
    return {
        "messages":          [HumanMessage(content=query)],
        "user_query":        query,
        "session_id":        session_id,
        "user_id":           user_id,
        "intent":            "",
        "intents":           [],
        "document_ids":      [],
        "vector_hits":       [],
        "bm25_hits":         [],
        "graph_hits":        [],
        "reranked_docs":     [],
        "ingest_result":     {},
        "search_result":     "",
        "verify_result":     {},
        "generate_result":   {},
        "analyze_result":    {},
        "combined_responses": [],
        "final_response":    "",
        "citations":         [],
        "export_path":       None,
        "retrieval_metrics": {},
    }


def _make_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:12]


def _meta_payload(session_id: str, state: dict) -> dict[str, Any]:
    return {
        "session_id":        session_id,
        "intent":            state.get("intent", "search"),
        "intents":           state.get("intents", []),
        "citations":         state.get("citations", []),
        "export_path":       state.get("export_path"),
        "verify_result":     state.get("verify_result") or None,
        "generate_result":   state.get("generate_result") or None,
        "analyze_result":    state.get("analyze_result") or None,
        "retrieval_metrics": state.get("retrieval_metrics") or None,
    }


def _oai_chunk(cid: str, model: str, delta: dict, finish: str | None = None) -> str:
    payload = {
        "id":      cid,
        "object":  "chat.completion.chunk",
        "created": int(time.time()),
        "model":   model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── Streaming generator ───────────────────────────────────────────────────────

async def _stream_completion(
    req: CompletionRequest,
    session_id: str,
) -> AsyncGenerator[str, None]:
    s = get_settings()
    cid = _make_id()
    query   = _extract_query(req.messages)
    user_id = req.user or "anonymous"
    state   = _build_state(query, session_id, user_id)

    # Opening delta — announce role
    yield _oai_chunk(cid, req.model, {"role": "assistant", "content": ""})

    final_state: dict = {}

    try:
        async with asyncio.timeout(s.graph_timeout_seconds):
            async for chunk in graph.astream(state, stream_mode="updates"):
                for node_name, update in chunk.items():
                    if node_name == "__end__":
                        continue
                    if isinstance(update, dict):
                        final_state.update(update)
                    await asyncio.sleep(0)

    except TimeoutError:
        msg = f"Превышен лимит времени ({s.graph_timeout_seconds}с). Попробуйте ещё раз."
        yield _oai_chunk(cid, req.model, {"content": msg}, finish="stop")
        yield "data: [DONE]\n\n"
        return
    except Exception as exc:
        logger.exception("Graph error in completions stream")
        yield _oai_chunk(cid, req.model, {"content": str(exc)}, finish="stop")
        yield "data: [DONE]\n\n"
        return

    # Stream final response word-by-word
    response = final_state.get("final_response", "")
    if response:
        words: list[str] = []
        for word in response.split(" "):
            words.append(word)
            if len(words) >= s.sse_word_chunk_size:
                yield _oai_chunk(cid, req.model, {"content": " ".join(words) + " "})
                words = []
                await asyncio.sleep(0)
        if words:
            yield _oai_chunk(cid, req.model, {"content": " ".join(words)})

    # Append metadata sentinel for aware clients (Chainlit)
    meta_str = _META_START + json.dumps(
        _meta_payload(session_id, final_state), ensure_ascii=False
    ) + _META_END
    yield _oai_chunk(cid, req.model, {"content": meta_str}, finish="stop")
    yield "data: [DONE]\n\n"


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post(
    "/chat/completions",
    summary="OpenAI-compatible Chat Completions",
    response_model=None,
)
@limiter.limit("30/minute")
async def chat_completions(
    request: Request,
    req: CompletionRequest,
    x_session_id: str | None = Header(default=None),
) -> StreamingResponse | JSONResponse:
    """OpenAI Chat Completions API — streaming and non-streaming.

    Pass session_id via the X-Session-ID header or as a system message:
        {"role": "system", "content": "session_id:my-session-123"}
    """
    session_id = _extract_session(req.messages, x_session_id)
    query      = _extract_query(req.messages)
    user_id    = req.user or "anonymous"

    if req.stream:
        return StreamingResponse(
            _stream_completion(req, session_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Non-streaming ─────────────────────────────────────────────────────────
    s = get_settings()
    initial_state = _build_state(query, session_id, user_id)

    try:
        loop   = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, graph.invoke, initial_state),
            timeout=s.graph_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Превышен лимит времени ({s.graph_timeout_seconds}с).",
        )
    except Exception as exc:
        logger.exception("Graph error in completions (non-streaming)")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response_text = result.get("final_response", "")
    meta_str = _META_START + json.dumps(
        _meta_payload(session_id, result), ensure_ascii=False
    ) + _META_END

    return JSONResponse({
        "id":      _make_id(),
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   req.model,
        "choices": [{
            "index":         0,
            "message":       {"role": "assistant", "content": response_text + meta_str},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    })
