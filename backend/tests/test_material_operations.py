import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    InternalUser,
    PBRMaterial,
    PBRMaterialMetadata,
    PBRMaterialMetadataSnapshot,
)
from app.db.session import Database
from app.main import create_app
from app.worker_client import (
    MAX_WORKER_RESPONSE_BYTES,
    WorkerClient,
    WorkerMaterialPreflight,
    WorkerMaterialPreflightResponse,
    WorkerResponseError,
    WorkerUnavailableError,
)


PROCESSOR_ID = UUID("00000000-0000-0000-0000-000000000077")
RAW_SECRET = "texture size: 12.5x34 cm\nraw-secret-marker"


class StreamingResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        content_length: int | None = None,
        status_code: int = 200,
    ) -> None:
        self._chunks = chunks
        self.status_code = status_code
        self.headers = httpx.Headers()
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.closed = False
        self.iterated = False
        self.yielded_chunks = 0

    def __enter__(self) -> "StreamingResponse":
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.closed = True

    def iter_bytes(self, chunk_size: int) -> Iterator[bytes]:
        assert chunk_size > 0
        self.iterated = True
        for chunk in self._chunks:
            self.yielded_chunks += 1
            yield chunk


def _stream_response(
    response: StreamingResponse,
) -> Callable[..., StreamingResponse]:
    def stream(*args: object, **kwargs: object) -> StreamingResponse:
        del args, kwargs
        return response

    return stream


def _worker_response_bytes(identity: str = "SAFE_0001_G03") -> bytes:
    return json.dumps({
        "schema_version": 1,
        "folder_path": f"library/{identity}",
        "folder_name": identity,
        "master_resolution": "16K",
        "master_last_modified_at": "2026-03-04T00:00:00.000000000+00:00",
        "policy": "CURRENT_ON_OR_AFTER_2026_03_04",
        "metadata": {
            "status": "VALID",
            "source_file_name": "metadata.txt",
            "sha256": "a" * 64,
            "raw_content": RAW_SECRET,
            "hex_color": "#A1B2C3",
            "width_cm": "12.5000",
            "height_cm": "34.0000",
            "warnings": [],
            "errors": [],
        },
        "warnings": [],
        "errors": [],
        "can_continue": True,
    }).encode()


class StubWorker:
    def __init__(self) -> None:
        self.responses: list[WorkerMaterialPreflight | Exception] = []
        self.calls: list[str] = []

    def queue(self, *responses: WorkerMaterialPreflight | Exception) -> None:
        self.responses.extend(responses)

    def preflight(self, folder_path: str) -> WorkerMaterialPreflight:
        self.calls.append(folder_path)
        if not self.responses:
            raise AssertionError("Unexpected worker preflight call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class CallbackWorker:
    def __init__(
        self,
        callback: Callable[[], None],
        response: WorkerMaterialPreflight,
    ) -> None:
        self._callback = callback
        self._response = response

    def preflight(self, folder_path: str) -> WorkerMaterialPreflight:
        del folder_path
        self._callback()
        return self._response


@pytest.fixture
def operation_client() -> Iterator[tuple[TestClient, Database, StubWorker]]:
    database = Database("sqlite+pysqlite:///:memory:")
    with database.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add(
            InternalUser(
                id=PROCESSOR_ID,
                display_name="Folder processor",
                email="folder.processor@example.com",
                role="PROCESSOR",
            )
        )
        session.commit()
    worker = StubWorker()
    application = create_app(Settings(), database, worker)
    with TestClient(application) as client:
        yield client, database, worker


def _create_material(client: TestClient, suffix: str = "") -> dict[str, object]:
    company = client.post("/api/companies", json={"name": f"Operation company{suffix}"})
    project = client.post(
        "/api/projects",
        json={
            "company_id": company.json()["id"],
            "project_number": f"OP-{suffix or '001'}",
            "name": "Operation project",
        },
    )
    brand = client.post(
        "/api/brands",
        json={
            "company_id": company.json()["id"],
            "name": f"Operation brand{suffix}",
            "folder_prefix": f"OP{suffix or 'BASE'}",
            "brand_identifier": f"operation-brand-{suffix or 'base'}",
        },
    )
    response = client.post(
        "/api/materials",
        json={
            "project_id": project.json()["id"],
            "published_brand_id": brand.json()["id"],
            "material_name": "Operation stone",
            "main_category_code": "G03",
            "assigned_processor_id": str(PROCESSOR_ID),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _preflight(
    identity: str,
    *,
    metadata_status: str = "VALID",
    raw_content: str | None = RAW_SECRET,
    warnings: list[dict[str, str | None]] | None = None,
    metadata_errors: list[dict[str, str | None]] | None = None,
    errors: list[dict[str, str | None]] | None = None,
) -> WorkerMaterialPreflight:
    findings = errors or []
    folder_path = f"library/{identity}"

    def wire_findings(
        values: list[dict[str, str | None]] | None,
        default_path: str,
    ) -> list[dict[str, str]]:
        return [
            {
                "code": str(item["code"]),
                "message": str(item["message"]),
                "path": str(item["path"] or default_path),
            }
            for item in values or []
        ]

    wire = WorkerMaterialPreflightResponse.model_validate_json(
        json.dumps({
            "schema_version": 1,
            "folder_path": folder_path,
            "folder_name": identity,
            "master_resolution": "16K",
            "master_last_modified_at": "2026-03-04T00:00:00.000000000+00:00",
            "policy": "CURRENT_ON_OR_AFTER_2026_03_04",
            "metadata": {
                "status": metadata_status,
                "source_file_name": None if metadata_status == "MISSING" else "metadata.txt",
                "sha256": "a" * 64 if raw_content is not None else None,
                "raw_content": raw_content,
                "hex_color": "#A1B2C3" if metadata_status == "VALID" else None,
                "width_cm": "12.5000" if metadata_status == "VALID" else None,
                "height_cm": "34.0000" if metadata_status == "VALID" else None,
                "warnings": wire_findings(warnings, f"{folder_path}/metadata.txt"),
                "errors": wire_findings(metadata_errors, f"{folder_path}/metadata.txt"),
            },
            "warnings": [],
            "errors": wire_findings(findings, folder_path),
            "can_continue": not findings,
        }),
        strict=True,
    )
    return wire.to_internal()


def _link(
    client: TestClient,
    worker: StubWorker,
    material: dict[str, object],
    result: WorkerMaterialPreflight | None = None,
) -> httpx.Response:
    worker.queue(result or _preflight(str(material["technical_identity"])))
    return client.post(
        f"/api/materials/{material['id']}/folder-link",
        json={"folder_path": f"library/{material['technical_identity']}"},
    )


def test_folder_preflight_is_read_only_and_omits_raw_content(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    worker.queue(_preflight(str(material["technical_identity"])))

    response = client.post(
        f"/api/materials/{material['id']}/folder-preflight",
        json={"folder_path": f"library/{material['technical_identity']}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["folder_name"] == material["technical_identity"]
    assert body["identity_matches"] is True
    assert body["can_continue"] is True
    assert "raw_content" not in body
    assert RAW_SECRET not in response.text
    with database.session() as session:
        stored = session.get(PBRMaterial, UUID(str(material["id"])))
        assert stored is not None and stored.folder_path is None


@pytest.mark.parametrize(
    "folder_name",
    [
        "OTHER_0001_G03",
        "OPBASE_0002_G03",
        "OPBASE_0001_G04",
        "COPY_OPBASE_0001_G03_ARCHIVE",
    ],
    ids=["prefix", "number", "category", "substring"],
)
def test_folder_preflight_reports_exact_identity_mismatch_without_change(
    operation_client: tuple[TestClient, Database, StubWorker],
    folder_name: str,
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    material_id = UUID(str(material["id"]))
    expected_identity = str(material["technical_identity"])
    absolute_path = "C:\\private\\material\\metadata.txt"
    worker.queue(
        _preflight(
            folder_name,
            warnings=[
                {
                    "code": "REVIEW",
                    "message": "Review metadata.",
                    "path": absolute_path,
                }
            ],
        )
    )

    response = client.post(
        f"/api/materials/{material_id}/folder-preflight",
        json={"folder_path": f"library/{expected_identity}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["folder_name"] == folder_name
    assert body["identity_matches"] is False
    assert body["can_continue"] is False
    mismatch = body["errors"][-1]
    assert mismatch == {
        "code": "TECHNICAL_IDENTITY_MISMATCH",
        "message": "Worker folder_name does not match material technical_identity.",
        "expected_technical_identity": expected_identity,
        "actual_folder_name": folder_name,
    }
    assert "raw_content" not in body
    assert RAW_SECRET not in response.text
    assert body["warnings"][0]["path"] is None
    assert absolute_path not in response.text
    with database.session() as session:
        stored = session.get(PBRMaterial, material_id)
        current = session.get(PBRMaterialMetadata, material_id)
        snapshot_count = session.scalar(
            select(func.count(PBRMaterialMetadataSnapshot.id)).where(
                PBRMaterialMetadataSnapshot.material_id == material_id
            )
        )
        assert stored is not None and stored.folder_path is None
        assert stored.workflow_status == "IN_PROGRESS"
        assert current is not None and current.current_snapshot_id is None
        assert snapshot_count == 0


def test_folder_link_repeats_preflight_and_allows_metadata_warning(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, _, worker = operation_client
    material = _create_material(client)
    warning = {"code": "SOURCE_METADATA_MISSING", "message": "Missing.", "path": None}
    worker.queue(
        _preflight(str(material["technical_identity"])),
        _preflight(
            str(material["technical_identity"]),
            metadata_status="MISSING",
            raw_content=None,
            warnings=[warning],
        ),
    )
    path = f"library/{material['technical_identity']}"

    assert client.post(
        f"/api/materials/{material['id']}/folder-preflight",
        json={"folder_path": path},
    ).status_code == 200
    response = client.post(
        f"/api/materials/{material['id']}/folder-link",
        json={"folder_path": path},
    )

    assert response.status_code == 200
    assert response.json()["material"]["folder_path"] == path
    assert response.json()["preflight"]["metadata_status"] == "MISSING"
    assert response.json()["preflight"]["source_filename"] is None
    assert worker.calls == [path, path]


def test_folder_link_rejects_folder_name_mismatch_without_change(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)

    response = _link(client, worker, material, _preflight("OTHER_0001_G03"))

    assert response.status_code == 409
    with database.session() as session:
        stored = session.get(PBRMaterial, UUID(str(material["id"])))
        assert stored is not None and stored.folder_path is None


def test_mark_done_rejects_folder_name_mismatch_without_snapshot(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    material_id = UUID(str(material["id"]))
    assert _link(client, worker, material).status_code == 200
    worker.queue(_preflight("OTHER_0001_G03"))

    response = client.post(f"/api/materials/{material_id}/mark-done")

    assert response.status_code == 409
    with database.session() as session:
        stored = session.get(PBRMaterial, material_id)
        current = session.get(PBRMaterialMetadata, material_id)
        snapshot_count = session.scalar(
            select(func.count(PBRMaterialMetadataSnapshot.id)).where(
                PBRMaterialMetadataSnapshot.material_id == material_id
            )
        )
        assert stored is not None and stored.workflow_status == "IN_PROGRESS"
        assert current is not None and current.current_snapshot_id is None
        assert snapshot_count == 0


def test_folder_link_rejects_blocking_preflight_without_change(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    result = _preflight(
        str(material["technical_identity"]),
        metadata_status="INVALID",
        raw_content=None,
        errors=[{"code": "MATERIAL_MISSING", "message": "Missing.", "path": None}],
    )

    response = _link(client, worker, material, result)

    assert response.status_code == 422
    with database.session() as session:
        stored = session.get(PBRMaterial, UUID(str(material["id"])))
        assert stored is not None and stored.folder_path is None


def test_mark_done_requires_link_without_calling_worker(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, _, worker = operation_client
    material = _create_material(client)

    response = client.post(f"/api/materials/{material['id']}/mark-done")

    assert response.status_code == 409
    assert worker.calls == []


def test_material_operation_missing_material_is_404_without_worker_call(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, _, worker = operation_client
    missing_id = "00000000-0000-0000-0000-000000000123"

    for suffix in ("folder-preflight", "folder-link"):
        response = client.post(
            f"/api/materials/{missing_id}/{suffix}",
            json={"folder_path": "library/MISSING_0001_G03"},
        )
        assert response.status_code == 404
    assert client.post(f"/api/materials/{missing_id}/mark-done").status_code == 404
    assert worker.calls == []


@pytest.mark.parametrize(
    ("metadata_status", "raw_content", "warning_code"),
    [
        ("MISSING", None, "SOURCE_METADATA_MISSING"),
        ("INVALID", "not valid metadata", "SOURCE_METADATA_INVALID_FORMAT"),
        ("WARNING", "texture size: 12x34 cm", "HEX_COLOR_MISSING"),
    ],
)
def test_metadata_problem_does_not_block_done(
    operation_client: tuple[TestClient, Database, StubWorker],
    metadata_status: str,
    raw_content: str | None,
    warning_code: str,
) -> None:
    client, _, worker = operation_client
    material = _create_material(client)
    assert _link(client, worker, material).status_code == 200
    warning = {"code": warning_code, "message": "Review metadata.", "path": "metadata.txt"}
    worker.queue(
        _preflight(
            str(material["technical_identity"]),
            metadata_status=metadata_status,
            raw_content=raw_content,
            warnings=None if metadata_status == "INVALID" else [warning],
            metadata_errors=[warning] if metadata_status == "INVALID" else None,
        )
    )

    response = client.post(f"/api/materials/{material['id']}/mark-done")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["material"]["workflow_status"] == "DONE"
    assert body["metadata"]["status"] == metadata_status
    assert body["metadata"]["warnings"][0]["code"] == warning_code
    assert body["preflight"]["warnings"][0]["code"] == warning_code


def test_mark_done_persists_atomic_snapshot_and_current_metadata_without_status_drift(
    operation_client: tuple[TestClient, Database, StubWorker],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    assert _link(client, worker, material).status_code == 200
    worker.queue(_preflight(str(material["technical_identity"])))

    response = client.post(f"/api/materials/{material['id']}/mark-done")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["material"]["workflow_status"] == "DONE"
    assert body["material"]["validation_status"] == "NOT_CHECKED"
    assert body["material"]["publication_status"] == "NOT_PUBLISHED"
    assert body["snapshot"]["sequence_number"] == 1
    assert body["metadata"]["current_snapshot_id"] == body["snapshot"]["id"]
    assert body["metadata"]["hex_color"] == "#A1B2C3"
    assert body["metadata"]["width_cm"] == "12.5000"
    assert body["metadata"]["loaded_at"]
    assert RAW_SECRET not in response.text
    with database.session() as session:
        current = session.get(PBRMaterialMetadata, UUID(str(material["id"])))
        assert current is not None and current.source_content == RAW_SECRET
        assert current.current_snapshot_id == UUID(body["snapshot"]["id"])
        snapshot = session.get(PBRMaterialMetadataSnapshot, current.current_snapshot_id)
        assert snapshot is not None and snapshot.source_content == RAW_SECRET

    assert RAW_SECRET not in client.get(
        f"/api/materials/{material['id']}/metadata"
    ).text
    assert RAW_SECRET not in client.get(
        f"/api/materials/{material['id']}/metadata/snapshots"
    ).text
    assert RAW_SECRET not in caplog.text


def test_worker_timeout_does_not_change_database(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    assert _link(client, worker, material).status_code == 200
    worker.queue(WorkerUnavailableError("Material worker is unavailable."))

    response = client.post(f"/api/materials/{material['id']}/mark-done")

    assert response.status_code == 503
    with database.session() as session:
        stored = session.get(PBRMaterial, UUID(str(material["id"])))
        current = session.get(PBRMaterialMetadata, UUID(str(material["id"])))
        count = session.scalar(
            select(func.count(PBRMaterialMetadataSnapshot.id)).where(
                PBRMaterialMetadataSnapshot.material_id == UUID(str(material["id"]))
            )
        )
        assert stored is not None and stored.workflow_status == "IN_PROGRESS"
        assert current is not None and current.current_snapshot_id is None
        assert count == 0


def test_oversized_worker_response_during_mark_done_does_not_change_database(
    operation_client: tuple[TestClient, Database, StubWorker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    material_id = UUID(str(material["id"]))
    assert _link(client, worker, material).status_code == 200
    response = StreamingResponse([b"x" * MAX_WORKER_RESPONSE_BYTES, b"x"])
    monkeypatch.setattr(httpx, "stream", _stream_response(response))
    worker.preflight = WorkerClient("http://worker:8080").preflight  # type: ignore[method-assign]

    api_response = client.post(f"/api/materials/{material_id}/mark-done")

    assert api_response.status_code == 503
    assert response.closed is True
    with database.session() as session:
        stored = session.get(PBRMaterial, material_id)
        current = session.get(PBRMaterialMetadata, material_id)
        snapshot_count = session.scalar(
            select(func.count(PBRMaterialMetadataSnapshot.id)).where(
                PBRMaterialMetadataSnapshot.material_id == material_id
            )
        )
        assert stored is not None and stored.workflow_status == "IN_PROGRESS"
        assert current is not None and current.current_snapshot_id is None
        assert snapshot_count == 0


def test_blocking_worker_finding_does_not_change_done_state(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    assert _link(client, worker, material).status_code == 200
    worker.queue(
        _preflight(
            str(material["technical_identity"]),
            metadata_status="INVALID",
            raw_content=None,
            errors=[
                {
                    "code": "MATERIAL_PATH_INVALID",
                    "message": "Folder is outside the allowed root.",
                    "path": None,
                }
            ],
        )
    )

    response = client.post(f"/api/materials/{material['id']}/mark-done")

    assert response.status_code == 422
    assert "raw_content" not in response.text
    with database.session() as session:
        stored = session.get(PBRMaterial, UUID(str(material["id"])))
        current = session.get(PBRMaterialMetadata, UUID(str(material["id"])))
        assert stored is not None and stored.workflow_status == "IN_PROGRESS"
        assert current is not None and current.current_snapshot_id is None


def test_material_revision_is_rechecked_after_unlocked_worker_call(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    assert _link(client, worker, material).status_code == 200
    material_id = UUID(str(material["id"]))

    def concurrent_change() -> None:
        with database.session() as session:
            stored = session.get(PBRMaterial, material_id)
            assert stored is not None
            stored.folder_path = f"moved/{stored.technical_identity}"
            session.commit()

    worker.preflight = CallbackWorker(  # type: ignore[method-assign]
        concurrent_change,
        _preflight(str(material["technical_identity"])),
    ).preflight
    response = client.post(f"/api/materials/{material['id']}/mark-done")

    assert response.status_code == 409
    with database.session() as session:
        stored = session.get(PBRMaterial, material_id)
        assert stored is not None and stored.workflow_status == "IN_PROGRESS"
        assert session.scalar(
            select(func.count(PBRMaterialMetadataSnapshot.id)).where(
                PBRMaterialMetadataSnapshot.material_id == material_id
            )
        ) == 0


def test_invalid_worker_response_is_controlled_and_does_not_leak_raw_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_marker = "invalid-worker-raw-secret"

    response = StreamingResponse(
        [json.dumps({
            "schema_version": 1,
            "folder_name": "SAFE_0001_G03",
            "raw_content": raw_marker,
        }).encode()]
    )
    monkeypatch.setattr(httpx, "stream", _stream_response(response))
    client = WorkerClient("http://worker:8080")

    with pytest.raises(WorkerResponseError, match="invalid response") as exc_info:
        client.preflight("library/SAFE_0001_G03")
    assert response.closed is True
    assert exc_info.value.__cause__ is None
    assert raw_marker not in str(exc_info.value)
    assert raw_marker not in caplog.text


def test_worker_client_maps_actual_nested_worker_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _worker_response_bytes()
    response = StreamingResponse([payload], content_length=len(payload))
    monkeypatch.setattr(httpx, "stream", _stream_response(response))

    result = WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")

    assert result.folder_path == "library/SAFE_0001_G03"
    assert result.folder_name == "SAFE_0001_G03"
    assert result.master_last_modified_at == "2026-03-04T00:00:00.000000000+00:00"
    assert result.metadata_status.value == "VALID"
    assert result.source_filename == "metadata.txt"
    assert result.raw_content == RAW_SECRET
    assert result.hex_color == "#A1B2C3"
    assert result.width_cm == Decimal("12.5000")
    assert result.height_cm == Decimal("34.0000")
    assert result.metadata_warnings == []
    assert result.metadata_errors == []


def test_worker_client_maps_metadata_warning_and_error_as_non_blocking_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.loads(_worker_response_bytes())
    body["metadata"]["status"] = "WARNING"
    body["metadata"]["warnings"] = [{
        "code": "HEX_COLOR_MISSING",
        "path": "library/SAFE_0001_G03/metadata.txt",
        "message": "Color is missing",
    }]
    body["metadata"]["errors"] = [{
        "code": "SOURCE_METADATA_INVALID_FORMAT",
        "path": "library/SAFE_0001_G03/metadata.txt",
        "message": "Metadata is invalid",
    }]
    payload = json.dumps(body).encode()
    monkeypatch.setattr(httpx, "stream", _stream_response(StreamingResponse([payload])))

    result = WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")

    assert [item.code for item in result.metadata_warnings] == ["HEX_COLOR_MISSING"]
    assert [item.code for item in result.metadata_errors] == [
        "SOURCE_METADATA_INVALID_FORMAT"
    ]
    assert result.can_continue is True


def test_worker_client_accepts_missing_metadata_and_null_source_file_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.loads(_worker_response_bytes())
    body["metadata"] = {
        "status": "MISSING",
        "source_file_name": None,
        "sha256": None,
        "raw_content": None,
        "hex_color": None,
        "width_cm": None,
        "height_cm": None,
        "warnings": [{
            "code": "SOURCE_METADATA_MISSING",
            "path": "library/SAFE_0001_G03/metadata.txt",
            "message": "Root metadata.txt does not exist",
        }],
        "errors": [],
    }
    payload = json.dumps(body).encode()
    monkeypatch.setattr(httpx, "stream", _stream_response(StreamingResponse([payload])))

    result = WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")

    assert result.metadata_status.value == "MISSING"
    assert result.source_filename is None
    assert result.raw_content is None
    assert result.width_cm is None
    assert result.height_cm is None


def test_worker_client_rejects_original_flat_stub_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.loads(_worker_response_bytes())
    metadata = body.pop("metadata")
    body.update({
        "metadata_status": metadata["status"],
        "source_filename": metadata["source_file_name"],
        "sha256": metadata["sha256"],
        "raw_content": metadata["raw_content"],
        "hex_color": metadata["hex_color"],
        "width_cm": metadata["width_cm"],
        "height_cm": metadata["height_cm"],
    })
    payload = json.dumps(body).encode()
    monkeypatch.setattr(httpx, "stream", _stream_response(StreamingResponse([payload])))

    with pytest.raises(WorkerResponseError, match="invalid response"):
        WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")


def test_worker_client_rejects_unknown_response_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.loads(_worker_response_bytes())
    body["unknown"] = "must be rejected"
    payload = json.dumps(body).encode()
    monkeypatch.setattr(httpx, "stream", _stream_response(StreamingResponse([payload])))

    with pytest.raises(WorkerResponseError, match="invalid response"):
        WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")


def test_worker_client_rejects_response_for_different_folder_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.loads(_worker_response_bytes())
    body["folder_path"] = "library/OTHER_0001_G03"
    payload = json.dumps(body).encode()
    monkeypatch.setattr(httpx, "stream", _stream_response(StreamingResponse([payload])))

    with pytest.raises(WorkerResponseError, match="invalid response"):
        WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")


def test_worker_response_content_length_over_limit_is_rejected_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = StreamingResponse(
        [_worker_response_bytes()],
        content_length=MAX_WORKER_RESPONSE_BYTES + 1,
    )
    monkeypatch.setattr(httpx, "stream", _stream_response(response))

    with pytest.raises(WorkerResponseError, match="invalid response"):
        WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")

    assert response.iterated is False
    assert response.closed is True


def test_chunked_worker_response_over_limit_stops_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = StreamingResponse(
        [b"x" * MAX_WORKER_RESPONSE_BYTES, b"x", b"must-not-be-read"],
    )
    monkeypatch.setattr(httpx, "stream", _stream_response(response))

    with pytest.raises(WorkerResponseError, match="invalid response"):
        WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")

    assert response.yielded_chunks == 2
    assert response.closed is True


def test_worker_response_exactly_at_limit_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _worker_response_bytes()
    payload += b" " * (MAX_WORKER_RESPONSE_BYTES - len(payload))
    response = StreamingResponse(
        [payload[:1_000_000], payload[1_000_000:]],
        content_length=MAX_WORKER_RESPONSE_BYTES,
    )
    monkeypatch.setattr(httpx, "stream", _stream_response(response))

    result = WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")

    assert result.folder_name == "SAFE_0001_G03"
    assert response.closed is True


def test_small_valid_worker_response_is_streamed_and_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _worker_response_bytes()
    response = StreamingResponse([payload], content_length=len(payload))
    monkeypatch.setattr(httpx, "stream", _stream_response(response))

    result = WorkerClient("http://worker:8080").preflight("library/SAFE_0001_G03")

    assert result.can_continue is True
    assert response.iterated is True
    assert response.closed is True


def test_http_timeout_is_mapped_to_controlled_worker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: object, **kwargs: object) -> StreamingResponse:
        del args, kwargs
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "stream", timeout)

    with pytest.raises(WorkerUnavailableError, match="unavailable"):
        WorkerClient("http://worker:8080", timeout_seconds=0.5).preflight(
            "library/SAFE_0001_G03"
        )


def test_invalid_worker_response_maps_to_api_503_without_database_change(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    worker.queue(WorkerResponseError("Material worker returned an invalid response."))

    response = client.post(
        f"/api/materials/{material['id']}/folder-link",
        json={"folder_path": f"library/{material['technical_identity']}"},
    )

    assert response.status_code == 503
    with database.session() as session:
        stored = session.get(PBRMaterial, UUID(str(material["id"])))
        assert stored is not None and stored.folder_path is None


def test_done_is_conflict_and_does_not_create_duplicate_snapshot(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    assert _link(client, worker, material).status_code == 200
    worker.queue(_preflight(str(material["technical_identity"])))
    assert client.post(f"/api/materials/{material['id']}/mark-done").status_code == 200

    second = client.post(f"/api/materials/{material['id']}/mark-done")

    assert second.status_code == 409
    with database.session() as session:
        count = session.scalar(
            select(func.count(PBRMaterialMetadataSnapshot.id)).where(
                PBRMaterialMetadataSnapshot.material_id == UUID(str(material["id"]))
            )
        )
        assert count == 1
    assert len(worker.calls) == 2


def test_snapshot_history_is_append_only_across_new_completion_revision(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, database, worker = operation_client
    material = _create_material(client)
    assert _link(client, worker, material).status_code == 200
    worker.queue(_preflight(str(material["technical_identity"])))
    first = client.post(f"/api/materials/{material['id']}/mark-done").json()
    with database.session() as session:
        stored = session.get(PBRMaterial, UUID(str(material["id"])))
        assert stored is not None
        stored.workflow_status = "IN_PROGRESS"
        session.commit()
    worker.queue(
        _preflight(
            str(material["technical_identity"]),
            metadata_status="WARNING",
            warnings=[{"code": "REVIEW", "message": "Review.", "path": None}],
        )
    )

    second = client.post(f"/api/materials/{material['id']}/mark-done")
    history = client.get(f"/api/materials/{material['id']}/metadata/snapshots")

    assert second.status_code == 200
    assert [item["sequence_number"] for item in history.json()] == [1, 2]
    assert history.json()[0]["id"] == first["snapshot"]["id"]
    assert history.json()[0]["status"] == "VALID"
    assert history.json()[1]["status"] == "WARNING"


@pytest.mark.parametrize(
    "field",
    ["folder_path", "workflow_status", "validation_status", "metadata"],
)
def test_generic_patch_cannot_mutate_specialized_fields(
    operation_client: tuple[TestClient, Database, StubWorker],
    field: str,
) -> None:
    client, _, _ = operation_client
    material = _create_material(client)

    response = client.patch(
        f"/api/materials/{material['id']}",
        json={field: "forbidden"},
    )

    assert response.status_code == 422


def test_material_special_operations_have_no_delete_routes(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, _, _ = operation_client
    material = _create_material(client)

    for suffix in ("folder-preflight", "folder-link", "mark-done"):
        assert client.delete(f"/api/materials/{material['id']}/{suffix}").status_code == 405


@pytest.mark.parametrize(
    "folder_path",
    ["/absolute/path", "../escape", "parent//child", "parent\\child", "C:/drive/path"],
)
def test_folder_path_requires_safe_relative_posix_syntax(
    operation_client: tuple[TestClient, Database, StubWorker],
    folder_path: str,
) -> None:
    client, _, worker = operation_client
    material = _create_material(client)

    response = client.post(
        f"/api/materials/{material['id']}/folder-preflight",
        json={"folder_path": folder_path},
    )

    assert response.status_code == 422
    assert worker.calls == []


def test_mark_done_rejects_client_metadata_values(
    operation_client: tuple[TestClient, Database, StubWorker],
) -> None:
    client, _, worker = operation_client
    material = _create_material(client)
    assert _link(client, worker, material).status_code == 200

    response = client.post(
        f"/api/materials/{material['id']}/mark-done",
        json={"hex_color": "#FFFFFF"},
    )

    assert response.status_code == 422
    assert len(worker.calls) == 1
