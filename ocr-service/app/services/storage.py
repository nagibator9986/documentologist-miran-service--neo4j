"""
MinIO (S3-compatible) storage operations.
"""

import io
import re
from pathlib import Path
from typing import BinaryIO

from loguru import logger
from minio import Minio
from minio.error import S3Error
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core.config import get_settings


class MinIOService:
    """Wrapper around the MinIO client."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket_source = settings.bucket_source
        self.bucket_results = settings.bucket_results

    # ── helpers ──────────────────────────────────────────────

    def _ensure_bucket(self, bucket: str) -> None:
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
            logger.info(f"Created bucket: {bucket}")

    # ── public API ───────────────────────────────────────────

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Strip directory components and dangerous characters from a filename.

        Prevents path-traversal attacks such as '../../admin/secret.pdf'
        from poisoning MinIO object paths.
        """
        # Take only the basename — removes any leading path like ../../
        name = Path(filename).name
        # Replace every character that is not alphanumeric, dash, dot,
        # underscore, or space with an underscore.
        name = re.sub(r"[^\w\-_. ]", "_", name)
        # Strip leading/trailing dots and spaces that could confuse storage.
        name = name.strip(". ")
        return name or "unnamed_file"

    @staticmethod
    def build_source_path(file_hash: str, filename: str) -> str:
        safe_name = MinIOService.sanitize_filename(filename)
        return f"{file_hash}/{safe_name}"

    @retry(
        retry=retry_if_exception_type(S3Error),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def upload_source_file(
        self, file_hash: str, filename: str, data: BinaryIO, size: int
    ) -> str:
        """Upload original file. Returns the S3 path.

        Retries up to 3 times with exponential backoff (1s, 2s, 4s) on S3Error.
        """
        self._ensure_bucket(self.bucket_source)
        object_name = self.build_source_path(file_hash=file_hash, filename=filename)
        self.client.put_object(
            bucket_name=self.bucket_source,
            object_name=object_name,
            data=data,
            length=size,
        )
        logger.info(f"Uploaded source file: {self.bucket_source}/{object_name}")
        return object_name

    @retry(
        retry=retry_if_exception_type(S3Error),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def upload_result_json(self, file_hash: str, json_bytes: bytes) -> str:
        """Upload Surya analysis JSON. Returns the S3 path.

        Retries up to 3 times with exponential backoff (1s, 2s, 4s) on S3Error.
        """
        self._ensure_bucket(self.bucket_results)
        object_name = f"{file_hash}/surya_output.json"
        self.client.put_object(
            bucket_name=self.bucket_results,
            object_name=object_name,
            data=io.BytesIO(json_bytes),
            length=len(json_bytes),
            content_type="application/json",
        )
        logger.info(f"Uploaded result JSON: {self.bucket_results}/{object_name}")
        return object_name

    def download_source_file(self, s3_path: str) -> bytes:
        """Download original file from MinIO."""
        response = self.client.get_object(self.bucket_source, s3_path)
        data = response.read()
        response.close()
        response.release_conn()
        return data

    def download_result_json(self, result_path: str) -> bytes:
        """Download Surya result JSON from MinIO."""
        response = self.client.get_object(self.bucket_results, result_path)
        data = response.read()
        response.close()
        response.release_conn()
        return data

    def delete_source_file(self, s3_path: str) -> None:
        """Delete original file from MinIO if present."""
        try:
            self.client.remove_object(self.bucket_source, s3_path)
            logger.info(f"Deleted source file: {self.bucket_source}/{s3_path}")
        except S3Error as e:
            logger.warning(f"Failed to delete source file {s3_path}: {e}")

    def delete_result_json(self, result_path: str) -> None:
        """Delete Surya result JSON from MinIO if present."""
        try:
            self.client.remove_object(self.bucket_results, result_path)
            logger.info(f"Deleted result JSON: {self.bucket_results}/{result_path}")
        except S3Error as e:
            logger.warning(f"Failed to delete result JSON {result_path}: {e}")

    def health_check(self) -> bool:
        """Return True if MinIO is reachable."""
        try:
            self.client.list_buckets()
            return True
        except S3Error:
            return False


# Singleton
_minio_service: MinIOService | None = None


def get_minio_service() -> MinIOService:
    global _minio_service
    if _minio_service is None:
        _minio_service = MinIOService()
    return _minio_service
