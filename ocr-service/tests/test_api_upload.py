import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import app.api.routes as routes_module
from app.core.database import get_db
from app.models.document import DocumentStatus


class DummyDBSession:
    fail_commit = False
    last_instance = None

    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        DummyDBSession.last_instance = self

    async def commit(self) -> None:
        self.commit_calls += 1
        if DummyDBSession.fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rollback_calls += 1


async def override_get_db():
    yield DummyDBSession()


@pytest.fixture
def client():
    DummyDBSession.fail_commit = False
    DummyDBSession.last_instance = None
    app = FastAPI()
    app.include_router(routes_module.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_upload_duplicate_returns_existing_document(client, monkeypatch):
    doc_id = uuid.uuid4()
    existing_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.COMPLETED)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=existing_doc)
    service.mark_duplicate = AsyncMock(return_value=existing_doc)
    service.create_document = AsyncMock()

    minio = MagicMock()
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"same-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is True
    service.create_document.assert_not_called()
    minio.upload_source_file.assert_not_called()
    trigger_flow.assert_not_called()


def test_upload_new_document_stores_file_and_triggers_flow(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is False
    minio.upload_source_file.assert_called_once()
    service.create_document.assert_awaited_once()
    trigger_flow.assert_called_once()


def test_upload_rolls_back_when_storage_upload_fails(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.build_source_path = MagicMock(return_value="hash123/contract.pdf")
    minio.upload_source_file = MagicMock(side_effect=RuntimeError("minio down"))
    minio.delete_source_file = MagicMock()
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "compute_sha256", lambda _: "hash123")
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )

    assert response.status_code == 502
    assert DummyDBSession.last_instance is not None
    assert DummyDBSession.last_instance.rollback_calls >= 1
    minio.delete_source_file.assert_not_called()
    trigger_flow.assert_not_called()


def test_upload_deletes_source_when_commit_fails(client, monkeypatch):
    doc_id = uuid.uuid4()
    created_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PENDING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(return_value=None)
    service.mark_duplicate = AsyncMock()
    service.create_document = AsyncMock(return_value=created_doc)

    minio = MagicMock()
    minio.build_source_path = MagicMock(return_value="hash123/contract.pdf")
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    minio.delete_source_file = MagicMock()
    trigger_flow = MagicMock()

    DummyDBSession.fail_commit = True
    monkeypatch.setattr(routes_module, "compute_sha256", lambda _: "hash123")
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"new-content", "application/pdf")},
    )
    DummyDBSession.fail_commit = False

    assert response.status_code == 500
    minio.delete_source_file.assert_called_once_with("hash123/contract.pdf")
    trigger_flow.assert_not_called()


def test_upload_rejects_file_when_size_exceeds_limit(client, monkeypatch):
    monkeypatch.setattr(routes_module.settings, "max_upload_size_mb", 1)

    service = MagicMock()
    service.find_by_hash = AsyncMock()
    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("large.bin", b"x" * (1024 * 1024 + 1), "application/octet-stream")},
    )

    assert response.status_code == 413
    service.find_by_hash.assert_not_called()


def test_upload_handles_unique_hash_race_as_duplicate(client, monkeypatch):
    doc_id = uuid.uuid4()
    existing_doc = SimpleNamespace(id=doc_id, status=DocumentStatus.PROCESSING)

    service = MagicMock()
    service.find_by_hash = AsyncMock(side_effect=[None, existing_doc])
    service.mark_duplicate = AsyncMock(return_value=existing_doc)
    service.create_document = AsyncMock(
        side_effect=IntegrityError("stmt", {"file_hash": "hash123"}, Exception("orig"))
    )

    minio = MagicMock()
    minio.upload_source_file = MagicMock(return_value="hash123/contract.pdf")
    trigger_flow = MagicMock()

    monkeypatch.setattr(routes_module, "DocumentService", lambda db: service)
    monkeypatch.setattr(routes_module, "get_minio_service", lambda: minio)
    monkeypatch.setattr(routes_module, "_trigger_prefect_flow", trigger_flow)

    response = client.post(
        "/api/v1/upload",
        files={"file": ("contract.pdf", b"race-content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["doc_id"] == str(doc_id)
    assert body["is_duplicate"] is True
    service.mark_duplicate.assert_awaited_once()
    trigger_flow.assert_not_called()
