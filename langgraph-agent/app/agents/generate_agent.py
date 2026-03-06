"""Generate Agent — document generation, DOCX/PDF export, law-based drafting.

Improvements:
- Two-pass generation: plan -> draft -> legal validation pass
- Uses s.history_turns from config
- Uses invoke_with_retry for resilience
- Multi-intent: prepends combined_responses from prior agents
"""
from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..core.config import get_settings
from ..core.llm import get_json_llm, get_llm, invoke_with_retry
from ..core.utils import safe_parse_json
from ..graph.state import AgentState
from ..tools.doc_generate import doc_generate, docx_export, pdf_export
from ..tools.qdrant_search import qdrant_search

logger = logging.getLogger(__name__)


def _minio_upload(local_path: str, object_name: str) -> str | None:
    """Upload a local file to MinIO and return the object URL. Non-fatal."""
    try:
        from minio import Minio
        from ..core.config import get_settings as _gs
        s = _gs()
        client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        )
        if not client.bucket_exists(s.minio_bucket):
            client.make_bucket(s.minio_bucket)
        client.fput_object(s.minio_bucket, object_name, local_path)
        scheme = "https" if s.minio_secure else "http"
        return f"{scheme}://{s.minio_endpoint}/{s.minio_bucket}/{object_name}"
    except Exception as exc:
        logger.warning("MinIO upload failed (non-critical): %s", exc)
        return None


_PLAN_SYSTEM = """Ты — юридический ассистент по банковскому праву Казахстана.
Сгенерируй план документа на основе запроса пользователя.
КРИТИЧНО: ВСЕ текстовые значения должны быть ТОЛЬКО на русском языке.
Никаких китайских, английских или других иностранных слов в тексте документа.
Для стороны 1: "Кредитор" или "Банк"; для стороны 2: "Заёмщик" или "Клиент".

Выдай JSON:
{
  "template_type": "contract",
  "title": "название документа на русском",
  "context": {
    "parties": "стороны на русском",
    "subject": "предмет на русском",
    "clauses": ["пункт 1 на русском", "пункт 2 на русском"],
    "date": "дата",
    "references": ["НПА 1", "НПА 2"]
  },
  "export_format": "docx",
  "required_sections": ["Преамбула", "Стороны", "Предмет", "Обязательства", "Ответственность", "Реквизиты"]
}"""

_VALIDATE_SYSTEM = """Ты — юридический эксперт по банковскому праву Казахстана.
Проверь сгенерированный документ на полноту и соответствие нормам.
Если чего-то не хватает — добавь это в документ (дополни текст).
Верни ТОЛЬКО улучшенный финальный текст документа, без комментариев и объяснений.
КРИТИЧНО: Документ должен быть ТОЛЬКО на русском языке.
Никаких китайских иероглифов, английских слов или иностранных символов.
Все имена, адреса и реквизиты — используй российско-казахстанские шаблоны (г. Алматы, ул. Абая и т.п.).
"""


def generate_node(state: AgentState) -> AgentState:
    """Generate a legal document using two-pass approach: plan -> draft -> validate."""
    t_start = time.perf_counter()
    s = get_settings()
    query = state["user_query"]

    # ── Pass 1: Retrieve legal context ───────────────────────────────────────
    legal_hits = qdrant_search.invoke({"query": f"шаблон {query}", "limit": 5})
    legal_context = "\n".join(f"- {h.get('content', '')[:300]}" for h in legal_hits)

    # ── Pass 2: Plan document structure (JSON) ────────────────────────────────
    llm_json = get_json_llm(num_predict=1024)
    raw_plan = invoke_with_retry(llm_json, [
        SystemMessage(content=_PLAN_SYSTEM),
        HumanMessage(content=f"Запрос: {query}\n\nПрименимые нормы:\n{legal_context}"),
    ])

    plan = safe_parse_json(raw_plan, {
        "template_type": "report",
        "title": "Документ",
        "context": {"subject": query},
        "export_format": "text",
        "required_sections": [],
    })

    # ── Pass 3: Generate document draft ──────────────────────────────────────
    gen_context = plan.get("context", {"subject": query})
    required_sections = plan.get("required_sections", [])
    if isinstance(gen_context, dict) and legal_context:
        gen_context = {**gen_context, "legal_context": legal_context[:1500]}
    if isinstance(gen_context, dict) and required_sections:
        gen_context["required_sections"] = required_sections

    doc_draft: str = doc_generate.invoke({
        "template_type": plan.get("template_type", "report"),
        "context": gen_context,
    })

    # ── Pass 4: Legal validation & enrichment pass ────────────────────────────
    # Ask LLM to review the draft and add missing sections / correct wording.
    validation_prompt = (
        f"Тип документа: {plan.get('title', 'Документ')}\n\n"
        f"Применимые нормы законодательства:\n{legal_context}\n\n"
        f"Обязательные разделы: {', '.join(required_sections) if required_sections else 'стандартные'}\n\n"
        f"Черновик документа:\n{doc_draft}\n\n"
        "Улучши документ: проверь наличие всех обязательных разделов, исправь юридические "
        "формулировки, добавь ссылки на применимые НПА. Верни финальный текст документа."
    )

    llm = get_llm(num_predict=2000)
    doc_text = invoke_with_retry(llm, [
        SystemMessage(content=_VALIDATE_SYSTEM),
        HumanMessage(content=validation_prompt),
    ])

    # Fallback: if validation pass returned empty, use the draft
    if not doc_text.strip():
        doc_text = doc_draft
        logger.warning("generate_node: validation pass returned empty — using draft")

    # ── Pass 5: Export ────────────────────────────────────────────────────────
    export_path: str | None = None
    export_format = plan.get("export_format", "text")
    safe_title = "".join(
        c if c.isalnum() or c in "-_ " else "_" for c in plan.get("title", "document")
    ).strip()[:60] or "document"

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

    summary = f"**{plan.get('title', 'Документ')}**\n\n{doc_text[:800]}{'...' if len(doc_text) > 800 else ''}"
    if export_path:
        summary += f"\n\nФайл: `{export_path}`"

    # Multi-intent: prepend previous agents' responses
    combined_prev = state.get("combined_responses") or []
    if combined_prev:
        final_response = "\n\n---\n\n".join(combined_prev) + "\n\n---\n\n" + summary
    else:
        final_response = summary

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
