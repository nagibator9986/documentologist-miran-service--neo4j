"""
Pydantic schemas for API input / output.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.document import DocumentStatus


# ── Response schemas ─────────────────────────────────────────

class DocumentResponse(BaseModel):
    id: UUID
    file_hash: str
    filename: str
    status: DocumentStatus
    is_latest: bool
    s3_path: str | None = None
    result_path: str | None = None
    error_message: str | None = None
    page_count: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    doc_id: UUID
    status: DocumentStatus
    is_duplicate: bool = False
    message: str


class StatusResponse(BaseModel):
    doc_id: UUID
    status: DocumentStatus
    filename: str
    result_path: str | None = None
    error_message: str | None = None


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentResponse]
    next_cursor: str | None = None


class AskRequest(BaseModel):
    doc_id: UUID
    question: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    doc_id: UUID
    answer: str
    sources: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    database: str = "unknown"
    minio: str = "unknown"
