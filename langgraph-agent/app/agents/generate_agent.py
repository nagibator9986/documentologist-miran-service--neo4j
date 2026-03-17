"""Generate Agent — legal document generation with DOCX/PDF export.

Pipeline:
  1. _retrieve_legal_context — vector + graph enrichment for applicable norms
  2. _plan_document          — LLM JSON: template type, title, sections, format
  3. _generate_draft         — doc_generate tool: expand plan into full text
  4. _validate_draft         — LLM review: add missing sections, fix wording
  5. _export_document        — DOCX/PDF export + MinIO upload (non-fatal)
  6. generate_node           — orchestrator

Design notes:
- Prefix stripping applied before any LLM/retrieval call.
- Legal context: 10 hits × 1000 chars + Neo4j sections × 600 chars.
  Previous 5×300 was insufficient for document generation.
- MinIO clients are module-level singletons (one pair per endpoint).
- Document is shown in full in chat (up to _CHAT_DOC_MAX chars); file link appended.
- "шаблон" prefix removed from Qdrant query — it polluted the embedding vector
  and had no semantic benefit (the collection is all legal docs, not templates).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.json_output import GeneratePlan, parse_with_retry
from ..core.llm import get_draft_llm, invoke_with_retry
from ..core.utils import build_final_response, strip_conversational_prefix
from ..graph.state import AgentState
from ..prompts import GENERATE_PLAN, GENERATE_VALIDATE
from ..tools.doc_generate import doc_generate, docx_export, pdf_export
from ..tools.neo4j_query import graph_obligation_search, graph_section_search
from ..tools.qdrant_search import qdrant_search

logger = logging.getLogger(__name__)

# Maximum characters of document text to display inline in the chat.
# Beyond this threshold we show a truncation notice + download link.
_CHAT_DOC_MAX = 4000

# ── MinIO singleton ───────────────────────────────────────────────────────────
# Two clients per endpoint pair: one for uploads (internal Docker endpoint),
# one for presigned URLs (public-facing endpoint). Lazily created and reused.

_minio_lock = threading.Lock()
_minio_upload_client: Any = None
_minio_url_client: Any = None


def _get_minio_clients() -> tuple[Any, Any]:
    """Return (upload_client, url_client) as module-level singletons.

    Lazily creates clients on first call; thread-safe via double-checked locking.
    Using singletons avoids the overhead of TLS handshake + auth on every export.
    """
    global _minio_upload_client, _minio_url_client
    if _minio_upload_client is None:
        with _minio_lock:
            if _minio_upload_client is None:
                from minio import Minio
                s = get_settings()
                _minio_upload_client = Minio(
                    s.minio_endpoint,
                    access_key=s.minio_access_key,
                    secret_key=s.minio_secret_key,
                    secure=s.minio_secure,
                )
                public_ep = s.minio_public_endpoint or s.minio_endpoint
                _minio_url_client = Minio(
                    public_ep,
                    access_key=s.minio_access_key,
                    secret_key=s.minio_secret_key,
                    secure=s.minio_secure,
                )
                logger.info(
                    "MinIO singletons created: upload=%s public=%s",
                    s.minio_endpoint, public_ep,
                )
    return _minio_upload_client, _minio_url_client


# ── Stage 1: Legal context retrieval ─────────────────────────────────────────

def _retrieve_legal_context(query: str) -> tuple[list[dict], str]:
    """Search Qdrant + Neo4j for applicable norms and return enriched context.

    Improvements over original:
    - Query no longer prefixed with "шаблон" (distorted embedding vector).
    - Limit 5→10 hits; snippet 300→1000 chars — LLM needs real normative text.
    - Neo4j section text 200→600 chars — statutory articles are often longer.
    - Obligations enriched with evidence text for better norm reference.

    Returns:
        (hits, legal_context_str)
    """
    s = get_settings()
    hits = qdrant_search.invoke({"query": query, "limit": 10})
    parts: list[str] = [
        f"- {h.get('content', '')[:1000]}" for h in hits
    ]

    try:
        sections = graph_section_search.invoke({"keywords": query[:80], "limit": 4})
        for sec in sections:
            text = (sec.get("text") or "")[:600]
            source = sec.get("source", "")
            articles = sec.get("articles") or []
            art_refs = ", ".join(
                f"ст. {a.get('number')}" + (f" «{a.get('title')}»" if a.get("title") else "")
                for a in articles if a.get("number")
            )
            if text:
                ref = f"[{source}] {text}" if source else text
                if art_refs:
                    ref += f" [{art_refs}]"
                parts.append(f"- {ref}")
    except Exception as exc:
        logger.debug("generate: graph_section_search skipped: %s", exc)

    try:
        obligations = graph_obligation_search.invoke({"keywords": query[:60], "limit": 4})
        for o in obligations:
            line = " — ".join(
                p for p in [o.get("subject"), o.get("action"), o.get("object")] if p
            )
            if o.get("deadline"):
                line += f" (срок: {o['deadline']})"
            if o.get("evidence"):
                line += f"\n  {o['evidence'][:300]}"
            if line:
                parts.append(f"- Обязательство: {line}")
    except Exception as exc:
        logger.debug("generate: graph_obligation_search skipped: %s", exc)

    legal_context = "\n".join(parts)
    return hits, legal_context


# ── Stage 2: Document structure planning ─────────────────────────────────────

def _plan_document(query: str, legal_context: str) -> dict:
    """Ask the LLM to produce a JSON document plan with schema validation and retry."""
    messages = [
        SystemMessage(content=GENERATE_PLAN),
        HumanMessage(content=f"Запрос: {query}\n\nПрименимые нормы:\n{legal_context[:3000]}"),
    ]

    result, success = parse_with_retry(messages, GeneratePlan, num_predict=2048)

    if success and result is not None:
        return result.model_dump()

    # Fallback: preserve existing behavior
    return {
        "template_type": "report",
        "title": "Документ",
        "context": {"subject": query},
        "export_format": "docx",
        "required_sections": [],
        "_parse_failed": True,
    }


# ── Stage 3: Draft generation ────────────────────────────────────────────────

def _generate_draft(plan: dict, legal_context: str) -> str:
    """Expand the document plan into a full draft using the doc_generate tool.

    Passes the full legal_context (no longer hard-capped at 1500 chars) so
    the template engine has enough normative text to reference.
    """
    gen_context = plan.get("context", {})
    required_sections = plan.get("required_sections", [])

    if isinstance(gen_context, dict):
        if legal_context:
            # Cap at 5000 chars to stay within template engine limits
            gen_context = {**gen_context, "legal_context": legal_context[:5000]}
        if required_sections:
            gen_context["required_sections"] = required_sections

    return doc_generate.invoke({
        "template_type": plan.get("template_type", "report"),
        "context": gen_context,
    })


# ── Stage 4: Legal validation pass ───────────────────────────────────────────

def _validate_draft(plan: dict, draft: str, legal_context: str) -> str:
    """Ask the LLM to review the draft, add missing sections, fix wording."""
    required_sections = plan.get("required_sections", [])
    prompt = (
        f"Тип документа: {plan.get('title', 'Документ')}\n\n"
        f"Применимые нормы законодательства:\n{legal_context[:3000]}\n\n"
        f"Обязательные разделы: {', '.join(required_sections) if required_sections else 'стандартные'}\n\n"
        f"Черновик документа:\n{draft}\n\n"
        "Улучши документ: проверь наличие всех обязательных разделов, исправь юридические "
        "формулировки, добавь ссылки на применимые НПА из раздела 'Применимые нормы'. "
        "Верни ТОЛЬКО финальный текст документа без комментариев."
    )
    llm = get_draft_llm(num_predict=2000)
    validated = invoke_with_retry(llm, [
        SystemMessage(content=GENERATE_VALIDATE),
        HumanMessage(content=prompt),
    ])
    if not validated.strip():
        logger.warning("generate: validation pass returned empty — using draft")
        return draft
    return validated


# ── Stage 5: Export and upload ────────────────────────────────────────────────

def _minio_upload(local_path: str, object_name: str) -> str | None:
    """Upload a local file to MinIO and return a presigned download URL. Non-fatal.

    Uses singleton clients — no new connections per call.
    """
    try:
        from datetime import timedelta
        s = get_settings()
        upload_client, url_client = _get_minio_clients()

        if not upload_client.bucket_exists(s.minio_bucket):
            upload_client.make_bucket(s.minio_bucket)
        upload_client.fput_object(s.minio_bucket, object_name, local_path)

        return url_client.presigned_get_object(
            s.minio_bucket, object_name, expires=timedelta(days=7)
        )
    except Exception as exc:
        logger.warning("MinIO upload failed (non-critical): %s", exc)
        return None


def _export_document(doc_text: str, plan: dict) -> tuple[str | None, str]:
    """Export to DOCX/PDF, upload to MinIO.

    Returns:
        (export_url_or_path, export_format)
    """
    export_format = plan.get("export_format", "docx")
    safe_title = "".join(
        c if c.isalnum() or c in "-_ " else "_"
        for c in plan.get("title", "document")
    ).strip()[:60] or "document"

    export_path: str | None = None

    if export_format == "docx":
        try:
            local_path = docx_export.invoke({"content": doc_text, "filename": f"{safe_title}.docx"})
            export_path = _minio_upload(local_path, f"exports/{safe_title}.docx") or local_path
        except Exception as exc:
            logger.warning("DOCX export failed: %s", exc)
    elif export_format == "pdf":
        try:
            local_path = pdf_export.invoke({"content": doc_text, "filename": f"{safe_title}.pdf"})
            export_path = _minio_upload(local_path, f"exports/{safe_title}.pdf") or local_path
        except Exception as exc:
            logger.warning("PDF export failed: %s", exc)

    return export_path, export_format


# ── Stage 6: Chat summary assembly ────────────────────────────────────────────

def _build_chat_summary(plan: dict, doc_text: str, export_path: str | None) -> str:
    """Build the response the user will see in chat.

    Shows the full document text (up to _CHAT_DOC_MAX chars) so the user
    doesn't have to download the file just to read the output.  If truncated,
    appends a clear notice with the download link.
    """
    title = plan.get("title", "Документ")
    lines: list[str] = [f"**{title}**\n"]

    if export_path:
        lines.append(f"📎 **Скачать файл:** [нажмите здесь]({export_path})\n")

    if len(doc_text) <= _CHAT_DOC_MAX:
        lines.append(doc_text)
    else:
        lines.append(doc_text[:_CHAT_DOC_MAX])
        lines.append(
            f"\n\n*...документ продолжается ({len(doc_text)} символов всего). "
            f"{'Скачайте полную версию по ссылке выше.' if export_path else 'Полный текст недоступен — экспорт не удался.'}*"
        )

    return "\n".join(lines)


# ── Graph entry point ─────────────────────────────────────────────────────────

def generate_node(state: AgentState) -> AgentState:
    """Document generation node: plan → draft → validate → export."""
    t_start = time.perf_counter()
    raw_query = state["user_query"]

    # Strip conversational noise before retrieval and planning
    query = strip_conversational_prefix(raw_query)

    legal_hits, legal_context = _retrieve_legal_context(query)
    plan = _plan_document(query, legal_context)
    draft = _generate_draft(plan, legal_context)
    doc_text = _validate_draft(plan, draft, legal_context)
    export_path, export_format = _export_document(doc_text, plan)

    summary = _build_chat_summary(plan, doc_text, export_path)
    final_response = build_final_response(summary, state.get("combined_responses") or [])

    elapsed = time.perf_counter() - t_start
    logger.info(
        "generate_node: title='%s' format=%s doc_len=%d elapsed=%.2fs",
        plan.get("title", "?"), export_format, len(doc_text), elapsed,
    )

    return {
        **state,
        "generate_result": {
            "title": plan.get("title"),
            "content": doc_text,
            "export_path": export_path,
        },
        "export_path": export_path,
        "final_response": final_response,
        "retrieval_metrics": {
            "node": "generate",
            "intent": state.get("intent", ""),
            "tier": state.get("tier", ""),
            "legal_hits": len(legal_hits),
            "doc_length": len(doc_text),
            "export_format": export_format,
            "json_parse_success": not plan.get("_parse_failed", False),
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
