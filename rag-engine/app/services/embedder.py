"""
MistralEmbedder — generates embeddings via Mistral API.

SRP: one responsibility — embedding generation.
"""
from __future__ import annotations

import logging

from mistralai import Mistral

from ..domain.interfaces import IEmbedder

logger = logging.getLogger(__name__)


class MistralEmbedder(IEmbedder):
    """
    Wraps Mistral `mistral-embed` model.
    Output: 1024-dimensional vectors.

    Mistral embedding API is synchronous; we wrap it with asyncio.to_thread
    so it doesn't block the event loop.
    """

    _VECTOR_SIZE = 1024

    def __init__(self, api_key: str, model: str = "mistral-embed") -> None:
        self._client = Mistral(api_key=api_key)
        self._model = model

    # ------------------------------------------------------------------
    # IEmbedder
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def vector_size(self) -> int:
        return self._VECTOR_SIZE

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        import asyncio

        response = await asyncio.to_thread(
            self._client.embeddings.create,
            model=self._model,
            inputs=texts,
        )

        vectors = [item.embedding for item in response.data]
        logger.debug("Embedded %d texts with %s", len(texts), self._model)
        return vectors
