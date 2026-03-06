"""
SemanticChunker — splits documents into token-bounded chunks.

SRP: one responsibility — document chunking.
OCP: extend by subclassing IChunker, don't modify this class.
"""
from __future__ import annotations

import tiktoken

from ..domain.interfaces import IChunker
from ..domain.models import DocumentChunk, IncomingDocument, Section


class SemanticChunker(IChunker):
    """
    Splits a document by sections first, then applies token-based sliding
    window with configurable overlap.

    Strategy:
        1. Iterate sections from the OCR JSON output.
        2. For each section: encode → slide window of `max_tokens` tokens
           with `overlap_tokens` overlap.
        3. Attempt to trim each window to a sentence boundary (keeps semantic
           coherence without breaking mid-sentence).
        4. If no sections exist, fall back to full-document text.
    """

    def __init__(
        self,
        max_tokens: int = 512,
        overlap_ratio: float = 0.20,
        min_tokens: int = 50,
        encoding_name: str = "cl100k_base",
    ) -> None:
        self._max_tokens = max_tokens
        self._overlap_tokens = max(1, int(max_tokens * overlap_ratio))
        self._min_tokens = min_tokens
        self._enc = tiktoken.get_encoding(encoding_name)

    # ------------------------------------------------------------------
    # IChunker
    # ------------------------------------------------------------------

    def chunk(self, document: IncomingDocument) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        idx = 0

        if document.sections:
            for section in document.sections:
                section_chunks = self._chunk_section(section, document.id, idx)
                chunks.extend(section_chunks)
                idx += len(section_chunks)
        else:
            fallback_text = self._full_text(document)
            for text, token_count in self._sliding_window(fallback_text):
                chunks.append(
                    DocumentChunk(
                        document_id=document.id,
                        content=text,
                        token_count=token_count,
                        chunk_index=idx,
                        metadata={"document_title": document.title},
                    )
                )
                idx += 1

        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chunk_section(
        self,
        section: Section,
        document_id: str,
        start_index: int,
    ) -> list[DocumentChunk]:
        result: list[DocumentChunk] = []
        for i, (text, token_count) in enumerate(self._sliding_window(section.content)):
            result.append(
                DocumentChunk(
                    document_id=document_id,
                    content=text,
                    token_count=token_count,
                    chunk_index=start_index + i,
                    section_title=section.title,
                    page_number=section.page_number,
                    metadata={"section_level": section.level},
                )
            )
        return result

    def _sliding_window(self, text: str) -> list[tuple[str, int]]:
        """Return (chunk_text, token_count) pairs using a sliding window."""
        tokens = self._enc.encode(text)
        if not tokens:
            return []

        if len(tokens) <= self._max_tokens:
            return [(text, len(tokens))]

        result: list[tuple[str, int]] = []
        step = max(1, self._max_tokens - self._overlap_tokens)
        start = 0

        while start < len(tokens):
            end = min(start + self._max_tokens, len(tokens))
            window_tokens = tokens[start:end]
            window_text = self._enc.decode(window_tokens)

            # Try to snap to a sentence boundary inside the last 30% of the chunk
            window_text = self._snap_to_sentence(window_text)
            actual_count = len(self._enc.encode(window_text))

            if actual_count >= self._min_tokens:
                result.append((window_text.strip(), actual_count))

            start += step

        return result

    @staticmethod
    def _snap_to_sentence(text: str) -> str:
        """Trim the text to end at the last sentence boundary in its final third."""
        threshold = int(len(text) * 0.70)
        for sep in (". ", ".\n", "! ", "? ", "!\n", "?\n"):
            pos = text.rfind(sep, threshold)
            if pos != -1:
                return text[: pos + len(sep)]
        return text

    @staticmethod
    def _full_text(document: IncomingDocument) -> str:
        parts = [document.title]
        for s in document.sections:
            if s.title:
                parts.append(f"\n## {s.title}\n")
            parts.append(s.content)
        return "\n".join(parts)
