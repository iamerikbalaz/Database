import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import MaterialAuditEvent, MaterialMetadataOperation, PBRMaterial, PBRMaterialMetadata, PBRMaterialMetadataSnapshot
from app.main import create_app
from app.metadata_client import MetadataClientError, MetadataObservation, MetadataResult, MetadataValues
from test_application_access import access_case


VALUES = {"hex_color": "#ABCDEF", "width_cm": "12.5", "height_cm": "34"}


def source(folder):
    raw = json.dumps({"COLOR": {"hex": VALUES["hex_color"]}, "TEXTURE_SIZE": {"cm": {"width": 12.5, "height": 34}}})
    return {"schema_version": 1, "folder_name": folder.rsplit("/", 1)[-1], "status": "VALID",
            "sha256": hashlib.sha256(raw.encode()).hexdigest(), "raw_content": raw, **VALUES}


class MetadataStub:
    unavailable = False
    failed = False
    rejected = False
    callback = None
    inspect_callback = None
    def __init__(self): self.requests = []
    def inspect(self, folder):
        if self.inspect_callback: self.inspect_callback()
        if self.unavailable: raise MetadataClientError()
        return MetadataObservation.model_validate({**source(folder), "editable": True, "writes_enabled": True})
    def execute(self, request):
        self.requests.append(request)
        if self.callback: self.callback()
        if self.failed: raise MetadataClientError()
        return MetadataResult.model_validate({"operation_id": request["operation_id"],
            "status": "REJECTED" if self.rejected else "COMPLETED",
            "failure_code": "METADATA_SOURCE_CHANGED" if self.rejected else None,
            "metadata": None if self.rejected else source(request["folder_path"])})


@pytest.fixture
def metadata_case(access_case):
    case = access_case; worker = MetadataStub()
    settings = case.app.state.settings.model_copy(update={"source_mutations_enabled": True})
    case.app = create_app(settings, case.database, case.worker, metadata_client=worker)
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.folder_path = "library/" + material.technical_identity
        session.commit()
    return case, worker, f"/api/materials/{material.id}"


def request_payload(client, path):
    observed = client.get(path + "/source-metadata").json()
    return {"idempotency_key": str(uuid4()), "expected_updated_at": observed["expected_updated_at"],
            "expected_sha256": observed["sha256"], "values": VALUES}


def test_save_records_authorization_before_source_and_one_snapshot(metadata_case):
    case, worker, path = metadata_case
    def inspect_authorization():
        with case.database.session() as session:
            operation = session.scalar(select(MaterialMetadataOperation))
            assert operation.status == "RUNNING"
            assert session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "SOURCE_METADATA_STARTED"))
    worker.callback = inspect_authorization
    with case.client("PRODUCTION_LEAD") as client:
        payload = request_payload(client, path)
        first = client.post(path + "/source-metadata", json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "COMPLETED"
        assert client.post(path + "/source-metadata", json=payload).json() == first.json()
    assert len(worker.requests) == 1
    with case.database.session() as session:
        current = session.get(PBRMaterialMetadata, case.materials[0].id)
        assert current.hex_color == VALUES["hex_color"] and current.source_filename == "metadata.txt"
        assert session.scalar(select(func.count()).select_from(PBRMaterialMetadataSnapshot)) == 1


def test_unknown_outcome_owns_material_and_exact_resume(metadata_case):
    case, worker, path = metadata_case; worker.failed = True
    with case.client("PRODUCTION_LEAD") as client:
        payload = request_payload(client, path)
        first = client.post(path + "/source-metadata", json=payload).json()
        assert first["status"] == "RUNNING"
        assert client.get(path + "/source-metadata").json()["active_operation"]["id"] == first["id"]
        another = {**payload, "idempotency_key": str(uuid4())}
        assert client.post(path + "/source-metadata", json=another).status_code == 409
        assert client.post(path + "/source-metadata", json=payload).json() == first
        assert len(worker.requests) == 1
        worker.failed = False
        assert client.post(path + "/source-metadata/" + first["id"] + "/resume").json()["status"] == "COMPLETED"
    assert worker.requests[0] == worker.requests[1]


def test_gate_unavailable_readonly_and_role(metadata_case):
    case, worker, path = metadata_case
    with case.client("LEADERSHIP") as client:
        payload = request_payload(client, path)
        assert client.post(path + "/source-metadata", json=payload).status_code == 403
    worker.unavailable = True
    with case.client("PRODUCTION_LEAD") as client:
        result = client.get(path + "/source-metadata").json()
        assert result["available"] is False and result["writes_enabled"] is False
        assert client.post(path + "/source-metadata", json=payload).json()["detail"]["code"] == "SOURCE_METADATA_UNAVAILABLE"
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialMetadataOperation)) == 0
    settings = case.app.state.settings.model_copy(update={"source_mutations_enabled": False})
    case.app = create_app(settings, case.database, case.worker, metadata_client=worker)
    with case.client("PRODUCTION_LEAD") as client:
        assert client.post(path + "/source-metadata", json=payload).status_code == 503
    assert not worker.requests


def test_request_conflict_and_strict_identity_keys(metadata_case):
    case, worker, path = metadata_case
    with case.client("PRODUCTION_LEAD") as client:
        payload = request_payload(client, path)
        assert client.post(path + "/source-metadata", json={**payload, "values": {**VALUES, "FOLDER": "new"}}).status_code == 422
        assert client.post(path + "/source-metadata", json=payload).status_code == 200
        assert client.post(path + "/source-metadata", json={**payload, "values": {**VALUES, "width_cm": "3"}}).status_code == 409


@pytest.mark.parametrize("value", ["0", "-1", "1e5", "100000000", "0.00001", "9" * 32, True, 1.2])
def test_decimal_input_is_bounded_without_rounding(value):
    with pytest.raises(ValueError): MetadataValues(**{**VALUES, "width_cm": value})


def test_hash_proof_must_match_source_bytes():
    result = {**source("TEST_0001_G01"), "sha256": "f" * 64, "editable": True, "writes_enabled": True}
    with pytest.raises(ValueError): MetadataObservation.model_validate(result)


def test_preinspection_reauthorizes_after_assignment_changed(metadata_case):
    case, worker, path = metadata_case
    with case.client("PROCESSOR") as client:
        payload = request_payload(client, path)
        def reassign():
            with case.database.session() as session:
                material = session.get(PBRMaterial, case.materials[0].id)
                material.assigned_processor_id = case.materials[1].assigned_processor_id
                session.commit()
        worker.inspect_callback = reassign
        assert client.post(path + "/source-metadata", json=payload).status_code == 404
    assert not worker.requests
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialMetadataOperation)) == 0


def test_missing_live_file_keeps_historical_values_without_claiming_source_proof(metadata_case):
    from app.metadata_saves import persist_metadata_snapshot
    case, worker, path = metadata_case
    with case.database.session() as session:
        persist_metadata_snapshot(session, case.materials[0].id, VALUES, warnings=[
            {"code": "HISTORICAL_WORKBOOK", "message": "Imported historical values.", "path": None}])
        session.commit()
    worker.inspect = lambda folder: MetadataObservation.model_validate({"schema_version": 1,
        "folder_name": folder.rsplit("/", 1)[-1], "status": "MISSING", "sha256": None,
        "raw_content": None, "hex_color": None, "width_cm": None, "height_cm": None,
        "editable": True, "writes_enabled": False})
    with case.client("ADMIN") as client:
        result = client.get(path + "/source-metadata").json()
        assert result["available"] and result["source_status"] == "MISSING"
        assert result["sha256"] is None and not result["writes_enabled"]
        assert result["values"]["hex_color"] == VALUES["hex_color"]
        assert float(result["values"]["width_cm"]) == float(VALUES["width_cm"])
    assert not worker.requests
