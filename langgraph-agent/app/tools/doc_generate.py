"""Tools: document generation, DOCX export, PDF export."""
from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from ..core.config import get_settings
from ..core.llm import get_llm

logger = logging.getLogger(__name__)

_DEJAVU_SEARCH_PATHS = [
    # Common Linux paths
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    # macOS (Homebrew)
    "/opt/homebrew/share/fonts/dejavu-fonts/DejaVuSans.ttf",
    "/usr/local/share/fonts/DejaVuSans.ttf",
    # Bundled alongside this module (preferred — ship fonts/DejaVuSans.ttf in the image)
    os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf"),
]


def _get_export_dir() -> str:
    """Return the configured export directory, creating it if needed."""
    s = get_settings()
    export_dir = s.export_dir
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def _setup_pdf_font(pdf: "FPDF") -> str:  # type: ignore[name-defined]
    """Register a Cyrillic-capable TTF font or fall back to Helvetica.

    NOTE: If Helvetica is used, Cyrillic text will render as '?'.
    To fix: install fonts-dejavu-core or place DejaVuSans.ttf in app/tools/fonts/.
    """
    for ttf_path in _DEJAVU_SEARCH_PATHS:
        if os.path.isfile(ttf_path):
            # Verify the path is a regular file (not a symlink escaping to unexpected location)
            real_path = os.path.realpath(ttf_path)
            if os.path.isfile(real_path):
                pdf.add_font("DejaVu", "", real_path, uni=True)
                pdf.add_font("DejaVu", "B", real_path, uni=True)
                return "DejaVu"
    raise RuntimeError(
        "DejaVuSans.ttf не найден ни в одном из путей поиска. "
        "PDF с кириллицей невозможно сгенерировать корректно. "
        "Решение: установите пакет fonts-dejavu-core (apt-get install -y fonts-dejavu-core) "
        "или разместите DejaVuSans.ttf в app/tools/fonts/ рядом с этим модулем."
    )


_TEMPLATES: dict[str, str] = {
    "contract":    "Составь юридический договор на основе данных: {context}",
    "letter":      "Составь деловое письмо на основе данных: {context}",
    "report":      "Составь аналитический отчёт на основе данных: {context}",
    "summary":     "Составь краткое резюме документа на основе данных: {context}",
    "law_excerpt": "Изложи выдержку из нормативно-правового акта на основе данных: {context}",
}


@tool
def doc_generate(template_type: str, context: dict[str, Any]) -> str:
    """Generate a structured document using Ollama LLM.

    Args:
        template_type: One of 'contract', 'letter', 'report', 'summary', 'law_excerpt'.
        context: Dict with fields like title, parties, clauses, date, etc.

    Returns:
        Generated document text.

    Raises:
        ValueError: If template_type is not one of the known types.
    """
    if template_type not in _TEMPLATES:
        raise ValueError(
            f"Unknown template_type {template_type!r}. "
            f"Valid types: {sorted(_TEMPLATES)}"
        )
    prompt = _TEMPLATES[template_type].format(context=str(context))

    llm = get_llm()
    result = llm.invoke([HumanMessage(content=prompt)]).content
    logger.info("doc_generate: template=%s content_len=%d", template_type, len(result))
    return result


@tool
def docx_export(content: str, filename: str = "document.docx") -> str:
    """Export text content to a DOCX file.

    Args:
        content: Document text (markdown-style headings supported).
        filename: Output filename.

    Returns:
        Absolute path to the exported file.
    """
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("DOCX export requires `python-docx`. Install: pip install python-docx") from exc

    export_dir = _get_export_dir()
    # Sanitize filename to prevent path traversal
    safe_filename = os.path.basename(filename)
    out_path = os.path.join(export_dir, safe_filename)

    doc = Document()
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            doc.add_heading(stripped[2:], level=1)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
        elif stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=3)
        elif stripped:
            doc.add_paragraph(stripped)

    doc.save(out_path)
    logger.info("DOCX exported to %s", out_path)
    return out_path


@tool
def pdf_export(content: str, filename: str = "document.pdf") -> str:
    """Export text content to a PDF file.

    Args:
        content: Document text.
        filename: Output filename.

    Returns:
        Absolute path to the exported file.
    """
    try:
        from fpdf import FPDF
    except ImportError as exc:
        raise RuntimeError("PDF export requires `fpdf2`. Install: pip install fpdf2") from exc

    export_dir = _get_export_dir()
    # Sanitize filename to prevent path traversal
    safe_filename = os.path.basename(filename)
    out_path = os.path.join(export_dir, safe_filename)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    font_name = _setup_pdf_font(pdf)
    pdf.set_font(font_name, size=11)

    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            pdf.set_font(font_name, "B", 16)
            pdf.cell(0, 10, stripped[2:], ln=True)
            pdf.set_font(font_name, size=11)
        elif stripped.startswith("## "):
            pdf.set_font(font_name, "B", 13)
            pdf.cell(0, 8, stripped[3:], ln=True)
            pdf.set_font(font_name, size=11)
        elif stripped:
            pdf.multi_cell(0, 7, stripped)
        else:
            pdf.ln(3)

    pdf.output(out_path)
    logger.info("PDF exported to %s", out_path)
    return out_path


@tool
def minio_fetch(object_path: str) -> bytes:
    """Fetch a file from MinIO storage.

    Args:
        object_path: Path within the bucket, e.g. 'processed/doc-123.json'.

    Returns:
        File contents as bytes.
    """
    try:
        from minio import Minio
    except ImportError as exc:
        raise RuntimeError("MinIO fetch requires `minio`. Install: pip install minio") from exc

    s = get_settings()
    client = Minio(
        s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key,
        secure=s.minio_secure,
    )
    response = client.get_object(s.minio_bucket, object_path)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
