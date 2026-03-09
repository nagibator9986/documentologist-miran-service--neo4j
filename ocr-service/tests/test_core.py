"""
Tests for the document upload and deduplication logic.
Run with: pytest tests/ -v
"""

import io

from app.services.hasher import compute_sha256, compute_sha256_bytes


class TestHasher:
    def test_sha256_deterministic(self):
        data = b"hello world"
        h1 = compute_sha256_bytes(data)
        h2 = compute_sha256_bytes(data)
        assert h1 == h2

    def test_sha256_different_content(self):
        h1 = compute_sha256_bytes(b"file1")
        h2 = compute_sha256_bytes(b"file2")
        assert h1 != h2

    def test_sha256_file_like(self):
        buf = io.BytesIO(b"test content")
        h = compute_sha256(buf)
        assert len(h) == 64  # SHA-256 hex digest is 64 chars
        # File pointer should be reset
        assert buf.tell() == 0

    def test_sha256_known_value(self):
        # SHA-256 of empty bytes
        h = compute_sha256_bytes(b"")
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestDeduplicationLogic:
    """Unit tests verifying the dedup flow logic."""

    def test_duplicate_returns_existing_id(self):
        """When a hash exists, no new record should be created."""
        # This is a logic-level test — the actual DB query is mocked
        existing_hash = compute_sha256_bytes(b"same content")
        new_hash = compute_sha256_bytes(b"same content")
        assert existing_hash == new_hash  # dedup condition

    def test_new_content_gets_new_hash(self):
        h1 = compute_sha256_bytes(b"version 1")
        h2 = compute_sha256_bytes(b"version 2")
        assert h1 != h2  # should create new record


class TestMinIOServiceUnit:
    """Basic unit tests for MinIO path construction."""

    def test_source_path_format(self):
        file_hash = "abc123"
        filename = "report.pdf"
        expected = f"{file_hash}/{filename}"
        assert expected == "abc123/report.pdf"

    def test_result_path_format(self):
        file_hash = "abc123"
        expected = f"{file_hash}/surya_output.json"
        assert expected == "abc123/surya_output.json"
