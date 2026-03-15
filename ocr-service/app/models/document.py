"""
SQLAlchemy models for the documents table.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    file_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment="SHA-256 hash of file content",
    )
    filename: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Original filename",
    )
    s3_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=True,
        comment="Path to original file in MinIO (source-files bucket)",
    )
    result_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=True,
        comment="Path to Surya JSON result in MinIO (analysis-results bucket)",
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            name="document_status",
            create_type=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        default=DocumentStatus.PENDING,
        nullable=False,
    )
    is_latest: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="True if this is the latest version of the file",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error description if status == failed",
    )
    page_count: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Number of pages in the document",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Composite index for version queries
    # Partial index on status for polling worker (avoids full-table scan)
    __table_args__ = (
        Index("ix_documents_filename_latest", "filename", "is_latest"),
        Index("ix_documents_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Document {self.id} [{self.status.value}] {self.filename}>"
