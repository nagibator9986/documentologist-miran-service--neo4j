"""
SHA-256 hashing for file deduplication.
"""

import hashlib
from typing import BinaryIO

CHUNK_SIZE = 8192  # 8 KB


def compute_sha256(file: BinaryIO) -> str:
    """
    Compute SHA-256 hash of a file-like object.
    Resets file pointer to start after computation.
    """
    sha = hashlib.sha256()
    file.seek(0)
    while chunk := file.read(CHUNK_SIZE):
        sha.update(chunk)
    file.seek(0)
    return sha.hexdigest()


def compute_sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hash of raw bytes."""
    return hashlib.sha256(data).hexdigest()
