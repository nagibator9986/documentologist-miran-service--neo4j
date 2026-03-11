"""Generate Agent — legal document generation with DOCX/PDF export.

Pipeline:
  1. _retrieve_legal_context — Qdrant search for applicable norms/templates
  2. _plan_document          — LLM JSON: template type, title, sections, format
  3. _generate_draft         — doc_generate tool: expand plan into full text
  4. _validate_draft         — LLM review: add missing sections, fix wording
  5. _export_document        — DOCX/PDF export + MinIO upload (non-fatal)
  6. _minio_upload           — internal upload helper (non-fatal)
  7. generate_node           — orchestrator
"""
from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_json_llm, get_llm, invoke_with_retry
from ..core.utils import build_final_response, safe_parse_json
from ..graph.state import AgentState
from ..prompts import GENERATE_PLAN, GENERATE_VALIDATE
from ..tools.doc_generate import doc_generate, docx_export, pdf_export
from ..tools.qdrant_search import qdrant_search

logger = logging.getLogger(__name__)


# ── MinIO upload helper ───────────────────────────────────────────────────────

def _minio_upload(local_path: str, object_name: str) -> str | None:
    """Upload a local file to MinIO and return a presigned download URL. Non-fatal.

    Upload uses the internal MINIO_ENDPOINT (works inside Docker).
    The presigned URL uses MINIO_PUBLIC_ENDPOINT (falls back to MINIO_ENDPOINT)
    so external clients can actually reach the download link.
    """
    try:
        from datetime import timedelta
        from minio import Minio
        s = get_settings()

        upload_client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        )
        if not upload_client.bucket_exists(s.minio_bucket):
            upload_client.make_bucket(s.minio_bucket)
        upload_client.fput_object(s.minio_bucket, object_name, local_path)

        public_endpoint = s.minio_public_endpoint or s.minio_endpoint
        url_client = Minio(
            public_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        )
        return url_client.presigned_get_object(
            s.minio_bucket, object_name, expires=timedelta(days=7)
        )
    except Exception as exc:
        logger.warning("MinIO upload failed (non-critical): %s", exc)
        return None


# ── Stage 1: Legal context retrieval ─────────────────────────────────────────

def _retrieve_legal_context(query: str) -> tuple[list[dict], str]:
    """Search Qdrant for templates and applicable norms.

    Returns:
        (hits, legal_context_str)
    """
    hits = qdrant_search.invoke({"query": f"шаблон {query}", "limit": 5})
    legal_context = "\n".join(f"- {h.get('content', '')[:300]}" for h in hits)
    return hits, legal_context


# ── Stage 2: Document structure planning ─────────────────────────────────────

def _plan_document(query: str, legal_context: str) -> dict:
    """Ask the LLM to produce a JSON document plan."""
    llm_json = get_json_llm(num_predict=1024)
    raw = invoke_with_retry(llm_json, [
        SystemMessage(content=GENERATE_PLAN),
        HumanMessage(content=f"Запрос: {query}\n\nПрименимые нормы:\n{legal_context}"),
    ])
    return safe_parse_json(raw, {
        "template_type": "report",
        "title": "Документ",
        "context": {"subject": query},
        "export_format": "text",
        "required_sections": [],
    })


# ── Stage 3: Draft generation ────────────────────────────────────────────────

def _generate_draft(plan: dict, legal_context: str) -> str:
    """Expand the document plan into a full draft using the doc_generate tool."""
    gen_context = plan.get("context", {})
    required_sections = plan.get("required_sections", [])

    if isinstance(gen_context, dict) and legal_context:
        gen_context = {**gen_context, "legal_context": legal_context[:1500]}
    if isinstance(gen_context, dict) and required_sections:
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
        f"Применимые нормы законодательства:\n{legal_context}\n\n"
        f"Обязательные разделы: {', '.join(required_sections) if required_sections else 'стандартные'}\n\n"
        f"Черновик документа:\n{draft}\n\n"
        "Улучши документ: проверь наличие всех обязательных разделов, исправь юридические "
        "формулировки, добавь ссылки на применимые НПА. Верни финальный текст документа."
    )
    llm = get_llm(num_predict=2000)
    validated = invoke_with_retry(llm, [
        SystemMessage(content=GENERATE_VALIDATE),
        HumanMessage(content=prompt),
    ])
    if not validated.strip():
        logger.warning("generate: validation pass returned empty — using draft")
        return draft
    return validated


# ── Stage 5: Export and upload ────────────────────────────────────────────────

def _export_document(doc_text: str, plan: dict) -> tuple[str | None, str]:
    """Export to DOCX/PDF, upload to MinIO.

    Returns:
        (export_path_or_url, export_format)
    """
    export_format = plan.get("export_format", "text")
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


# ── Graph entry point ─────────────────────────────────────────────────────────

def generate_node(state: AgentState) -> AgentState:
    """Document generation node: plan → draft → validate → export."""
    t_start = time.perf_counter()
    query = state["user_query"]

    legal_hits, legal_context = _retrieve_legal_context(query)
    plan = _plan_document(query, legal_context)
    draft = _generate_draft(plan, legal_context)
    doc_text = _validate_draft(plan, draft, legal_context)
    export_path, export_format = _export_document(doc_text, plan)

    summary = f"**{plan.get('title', 'Документ')}**\n\n{doc_text[:800]}{'...' if len(doc_text) > 800 else ''}"
    if export_path:
        summary += f"\n\nФайл: `{export_path}`"

    final_response = build_final_response(summary, state.get("combined_responses") or [])

    elapsed = time.perf_counter() - t_start
    logger.info(
        "generate_node: title='%s' format=%s elapsed=%.2fs",
        plan.get("title", "?"), export_format, elapsed,
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
            "legal_hits": len(legal_hits),
            "doc_length": len(doc_text),
            "export_format": export_format,
            "elapsed_s": round(elapsed, 2),
        },
        "messages": state["messages"] + [AIMessage(content=final_response)],
    }
