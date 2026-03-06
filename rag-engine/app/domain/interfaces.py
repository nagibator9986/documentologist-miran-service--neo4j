"""
Abstract interfaces (Dependency Inversion Principle).
All services depend on these abstractions, not concrete implementations.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import (
    DocumentChunk,
    EmbeddedChunk,
    GraphNode,
    GraphRelationship,
    IncomingDocument,
)


class IChunker(ABC):
    """SRP: splits a document into chunks."""

    @abstractmethod
    def chunk(self, document: IncomingDocument) -> list[DocumentChunk]:
        ...


class IEmbedder(ABC):
    """SRP: generates embedding vectors."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    @abstractmethod
    def vector_size(self) -> int:
        ...


class IVectorStore(ABC):
    """SRP: stores and retrieves embedded chunks."""

    @abstractmethod
    async def ensure_collection(self, collection: str, vector_size: int) -> None:
        ...

    @abstractmethod
    async def upsert(self, chunks: list[EmbeddedChunk], collection: str) -> int:
        ...

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        collection: str,
        limit: int = 10,
        filter_document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    async def delete_document(self, document_id: str, collection: str) -> int:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class IGraphStore(ABC):
    """SRP: stores knowledge graph nodes and relationships."""

    @abstractmethod
    async def upsert_node(self, node: GraphNode) -> None:
        ...

    @abstractmethod
    async def upsert_relationship(self, rel: GraphRelationship) -> None:
        ...

    @abstractmethod
    async def build_document_graph(self, document: IncomingDocument) -> int:
        """Build full graph for a document. Returns number of nodes created."""
        ...

    @abstractmethod
    async def close(self) -> None:
        ...
