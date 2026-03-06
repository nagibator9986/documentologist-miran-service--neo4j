"""Documents API — list source documents indexed in Qdrant, download originals."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ...core.config import get_settings
from ...core.utils import get_qdrant_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


def _get_upload_dir() -> Path:
    """Return the bank_knowledge uploads directory.

    Uses bank_knowledge_upload_dir from config if set; otherwise falls back to
    the conventional relative path for local development.
    """
    s = get_settings()
    if s.bank_knowledge_upload_dir:
        return Path(s.bank_knowledge_upload_dir)
    return Path(__file__).resolve().parents[4] / "bank_knowledge" / "app" / "uploads"


def _extract_meta(payload: dict) -> dict:
    meta_json_raw = payload.get("meta_json")
    if meta_json_raw:
        try:
            inner = json.loads(meta_json_raw) if isinstance(meta_json_raw, str) else meta_json_raw
            if isinstance(inner, dict):
                return inner
        except Exception:
            pass
    return {}


@router.get("/", summary="List all indexed documents")
def list_documents() -> dict:
    """Return list of unique source documents indexed in Qdrant."""
    s = get_settings()
    client = get_qdrant_client()

    docs: dict[str, dict] = {}  # doc_id → info
    offset = None

    try:
        while True:
            records, next_offset = client.scroll(
                collection_name=s.qdrant_collection,
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for r in records:
                payload = r.payload or {}
                meta = _extract_meta(payload)
                doc_id = meta.get("doc_id") or payload.get("source_row_id", "")
                filename = meta.get("filename") or payload.get("title", "")
                if not doc_id and not filename:
                    continue
                key = doc_id or filename
                if key not in docs:
                    docs[key] = {
                        "doc_id": doc_id,
                        "filename": filename,
                        "page_count": meta.get("page_count"),
                        "source_type": meta.get("source_type") or payload.get("source_type", ""),
                        "processed_at": meta.get("processed_at"),
                        "chunk_count": 1,
                    }
                else:
                    docs[key]["chunk_count"] = docs[key].get("chunk_count", 0) + 1

            if not records or next_offset is None:
                break
            offset = next_offset
    except Exception as exc:
        logger.error("list_documents failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Qdrant error: {exc}") from exc

    return {
        "collection": s.qdrant_collection,
        "total_documents": len(docs),
        "documents": sorted(docs.values(), key=lambda d: d.get("filename", "")),
    }


@router.get("/search", summary="Find documents containing exact text")
def search_documents(text: str, limit: int = 5) -> dict:
    """Return documents (with page/chunk info) that contain the exact text substring."""
    from ...tools.qdrant_search import qdrant_text_search

    if not text or len(text.strip()) < 3:
        raise HTTPException(status_code=400, detail="text must be at least 3 characters")

    hits = qdrant_text_search.invoke({"text": text, "limit": limit})
    results = []
    for h in hits:
        meta = _extract_meta(h.get("metadata", {}))
        results.append({
            "doc_id": meta.get("doc_id", ""),
            "filename": meta.get("filename") or h.get("metadata", {}).get("title", ""),
            "page_number": meta.get("page_number"),
            "chunk_index": meta.get("chunk_index"),
            "content_preview": (h.get("content", ""))[:300],
        })

    return {
        "query": text,
        "total_found": len(results),
        "results": results,
    }


def _normalize(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", s)


def _extract_text_from_uploads(filename: str) -> str | None:
    """Search all uploaded Surya JSON files for a document matching filename.

    Returns the full concatenated page text, or None if not found.
    """
    if not _get_upload_dir().exists():
        return None

    for json_file in sorted(_get_upload_dir().iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
        if json_file.suffix.lower() != ".json":
            continue
        try:
            with json_file.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            docs = raw.get("documents", [raw]) if isinstance(raw, dict) else raw
            if not isinstance(docs, list):
                docs = [docs]
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                if _normalize(doc.get("filename", "")) != _normalize(filename):
                    continue
                # Found — extract full text from all pages
                pages = doc.get("pages") or []
                parts: list[str] = []
                for page in pages:
                    text = str(page.get("full_text") or "").strip()
                    if text:
                        pnum = page.get("page_number", "")
                        parts.append(f"=== Страница {pnum} ===\n{text}")
                if parts:
                    return "\n\n".join(parts)
                # Fallback: full_text at doc level
                doc_text = str(doc.get("full_text") or "").strip()
                if doc_text:
                    return doc_text
        except Exception:
            continue
    return None


@router.get("/{filename}/download", summary="Download document as extracted text")
def download_document(filename: str):
    """Download document content extracted from Surya OCR.

    Since only Surya JSON files are stored (not original PDFs), this endpoint
    extracts the full text from the JSON and returns it as a .txt file.
    """
    from fastapi.responses import Response

    # Security: prevent path traversal
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not _get_upload_dir().exists():
        raise HTTPException(status_code=404, detail="Uploads directory not found")

    # 1. Try exact file match (e.g. if someone stored the PDF directly)
    exact = _get_upload_dir() / safe_name
    if exact.exists():
        return FileResponse(path=str(exact), filename=safe_name)

    candidates = [f for f in _get_upload_dir().iterdir() if f.is_file() and f.name.endswith(safe_name)]
    if candidates:
        best = max(candidates, key=lambda f: f.stat().st_mtime)
        return FileResponse(path=str(best), filename=safe_name)

    # 2. Extract text from Surya JSON — this is the normal case
    text = _extract_text_from_uploads(safe_name)
    if text:
        import urllib.parse
        stem = Path(safe_name).stem
        txt_name = f"{stem}.txt"
        # RFC 5987 encoding — supports non-ASCII (Cyrillic) filenames in headers
        encoded_name = urllib.parse.quote(txt_name, safe="")
        return Response(
            content=text.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
        )

    raise HTTPException(
        status_code=404,
        detail=f"Документ '{filename}' не найден. Оригинальный PDF не хранится — загрузите документ через bank_knowledge UI.",
    )
