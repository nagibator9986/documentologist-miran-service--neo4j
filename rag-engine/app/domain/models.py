"""
Domain models — pure data, no business logic.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    IMAGE = "image"
    LAW = "law"
    ARTICLE = "article"
    UNKNOWN = "unknown"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    CHUNKED = "chunked"
    EMBEDDED = "embedded"
    INDEXED = "indexed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Input from Service 1 (OCR Pipeline)
# ---------------------------------------------------------------------------


class Section(BaseModel):
    title: str
    content: str
    level: int = 1
    page_number: int | None = None


class Table(BaseModel):
    title: str | None = None
    headers: list[str] = []
    rows: list[list[str]] = []
    page_number: int | None = None


class Entity(BaseModel):
    text: str
    label: str  # PERSON | ORG | DATE | LAW_REF | …
    start: int
    end: int


class IncomingDocument(BaseModel):
    """Structured JSON output from Service 1."""

    id: str
    title: str
    type: DocumentType = DocumentType.UNKNOWN
    source: str | None = None
    sections: list[Section] = []
    tables: list[Table] = []
    entities: list[Entity] = []
    metadata: dict[str, Any] = {}
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Pipeline models
# ---------------------------------------------------------------------------


class DocumentChunk(BaseModel):
    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str
    content: str
    token_count: int
    chunk_index: int
    section_title: str | None = None
    page_number: int | None = None
    metadata: dict[str, Any] = {}


class EmbeddedChunk(BaseModel):
    chunk: DocumentChunk
    embedding: list[float]
    model: str


# ---------------------------------------------------------------------------
# Graph models
# ---------------------------------------------------------------------------


class GraphNode(BaseModel):
    node_id: str
    label: str  # Document | Law | Article | Section | Entity
    properties: dict[str, Any] = {}


class GraphRelationship(BaseModel):
    from_node_id: str
    to_node_id: str
    # REFERENCES | CONTRADICTS | BASED_ON | AMENDED_BY | CONTAINS | MENTIONS
    relationship_type: str
    properties: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    document: IncomingDocument
    collection: str = "documents"
    reindex: bool = False


class IngestResult(BaseModel):
    document_id: str
    chunks_total: int
    chunks_embedded: int
    chunks_stored: int
    graph_nodes: int
    status: DocumentStatus
    error: str | None = None


class SearchRequest(BaseModel):
    query: str
    collection: str = "documents"
    limit: int = 10
    filter_document_id: str | None = None


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    score: float
    section_title: str | None = None
    metadata: dict[str, Any] = {}
