"""
Core business logic: upload, deduplication, status tracking.
"""

import base64
import json
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus


# ── Cursor helpers ────────────────────────────────────────────

def _encode_cursor(created_at: datetime, doc_id: uuid.UUID) -> str:
    """Encode (created_at, id) pair into a URL-safe base64 cursor string."""
    payload = {"ts": created_at.isoformat(), "id": str(doc_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


class InvalidCursorError(ValueError):
    """Raised when a pagination cursor string is malformed or tampered with."""


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode cursor string back into (created_at, id) pair.

    Raises ``InvalidCursorError`` on any decoding failure.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        return datetime.fromisoformat(payload["ts"]), uuid.UUID(payload["id"])
    except (ValueError, KeyError, json.JSONDecodeError, Exception) as exc:
        raise InvalidCursorError(f"Malformed cursor: {exc}") from exc


class DocumentService:
    """CRUD + deduplication logic for documents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── queries ──────────────────────────────────────────────

    async def find_by_hash(self, file_hash: str) -> Document | None:
        """Return document with the given hash, or None."""
        stmt = (
            select(Document)
            .where(Document.file_hash == file_hash)
            .order_by(Document.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_id(self, doc_id: uuid.UUID) -> Document | None:
        stmt = select(Document).where(Document.id == doc_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # app/services/documents.py (обновленный метод)



    async def list_documents(
        self,
        limit: int = 50,
        cursor: str | None = None,
        latest_only: bool = True,
        status: DocumentStatus | None = None,
        filename: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> tuple[list[Document], int, str | None]:
        """Return (docs, total, next_cursor) using keyset pagination.

        Ordering: created_at DESC, id DESC — stable and index-friendly.
        Cursor encodes the last seen (created_at, id) so the next page starts
        immediately after that position without a full-table offset scan.
        """
        from sqlalchemy import func

        base = select(Document)

        if latest_only:
            base = base.where(Document.is_latest.is_(True))
        if status:
            base = base.where(Document.status == status)
        if filename:
            base = base.where(Document.filename.ilike(f"%{filename}%"))
        if created_after:
            base = base.where(Document.created_at >= created_after)
        if created_before:
            base = base.where(Document.created_at <= created_before)

        # total count (separate query, before keyset filter)
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # apply keyset condition when cursor is provided
        if cursor:
            cursor_dt, cursor_id = _decode_cursor(cursor)
            base = base.where(
                or_(
                    Document.created_at < cursor_dt,
                    and_(
                        Document.created_at == cursor_dt,
                        Document.id < cursor_id,
                    ),
                )
            )

        base = base.order_by(Document.created_at.desc(), Document.id.desc())

        # fetch limit+1 to detect whether a next page exists
        stmt = base.limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > limit
        docs = rows[:limit]
        next_cursor = _encode_cursor(docs[-1].created_at, docs[-1].id) if has_more else None

        return docs, total, next_cursor

    # ── mutations ────────────────────────────────────────────

    async def create_document(
        self,
        file_hash: str,
        filename: str,
        s3_path: str,
    ) -> Document:
        """
        Create a new document record.
        If a document with the same filename already exists, mark
        the old one as is_latest=False (version chain).
        """
        # mark previous versions as not latest
        await self.session.execute(
            update(Document)
            .where(Document.filename == filename, Document.is_latest.is_(True))
            .values(is_latest=False)
        )

        doc = Document(
            file_hash=file_hash,
            filename=filename,
            s3_path=s3_path,
            status=DocumentStatus.PENDING,
            is_latest=True,
        )
        self.session.add(doc)
        await self.session.flush()
        logger.info(f"Created document record {doc.id} for {filename}")
        return doc

    async def mark_duplicate(self, doc: Document) -> Document:
        """Touch updated_at on a duplicate upload."""
        doc.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return doc

    async def update_status(
        self,
        doc_id: uuid.UUID,
        status: DocumentStatus,
        result_path: str | None = None,
        error_message: str | None = None,
        page_count: int | None = None,
    ) -> None:
        values: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if result_path is not None:
            values["result_path"] = result_path
        if error_message is not None:
            values["error_message"] = error_message
        if page_count is not None:
            values["page_count"] = page_count

        await self.session.execute(
            update(Document).where(Document.id == doc_id).values(**values)
        )
        await self.session.flush()
        logger.info(f"Document {doc_id} → {status.value}")

    async def delete_document(self, doc_id: uuid.UUID) -> Document | None:
        """Delete a document by ID. Returns the deleted document or None if not found.

        Does NOT delete MinIO objects — caller is responsible for storage cleanup.
        """
        doc = await self.find_by_id(doc_id)
        if doc is None:
            return None
        await self.session.delete(doc)
        await self.session.flush()
        logger.info(f"Deleted document {doc_id} ({doc.filename})")
        return doc
