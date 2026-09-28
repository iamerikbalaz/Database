from datetime import timedelta
from contextlib import nullcontext
from decimal import Decimal
import hashlib
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.local_publication import build_local_publication_router
from app.db.models import MaterialAuditEvent, MaterialContent, OnlineCategory, PBRMaterial, PBRMaterialMetadata, ResourceChangeEvent
from app.local_filesystem import LocalFilesError
from app.main import create_app
from app.metadata_client import SourceMetadata
from test_application_access import access_case, ORIGIN, PASSWORD

PATH = "/api/local-publication"


class ExportStub:
    def __init__(self):
        self.calls = []; self.values = {}; self.requests = {}; self.callback = None
        self.library = SimpleNamespace(metadata=SimpleNamespace(inspect=self.inspect))
        self.color = "#ABCDEF"

    def inspect(self, folder):
        raw = '{"COLOR":{"hex":"' + self.color + '"}}'
        return SourceMetadata(schema_version=1, source_filename="metadata.json", folder_name=folder.split("/")[-1],
            status="VALID", sha256=hashlib.sha256(raw.encode()).hexdigest(), raw_content=raw, hex_color=self.color, width_cm="10.0", height_cm="20")

    def select_destination(self, actor):
        self.calls.append("picker")
        if self.callback: self.callback()
        return {"destination_token": "x" * 43, "destination_path": "C:\\SyntheticExports"}

    def status(self, identifier, actor):
        value = self.values.get(str(identifier))
        if value is None or value["actor_id"] != str(actor): raise LocalFilesError("LOCAL_EXPORT_NOT_FOUND")
        return value

    def start(self, actor, identifier, token, request, verify):
        key = str(identifier)
        if key in self.values:
            if self.requests[key] != request or token != "x" * 43: raise LocalFilesError("LOCAL_EXPORT_REQUEST_CONFLICT")
            return self.values[key]
        with verify(): pass
        self.calls.append("export")
        self.requests[key] = request
        self.values[key] = {"id": key, "actor_id": str(actor), "status": "COMPLETED",
            "material_ids": [item["material_id"] for item in request["materials"]], "preview_hash": request["preview_hash"],
            "output_path": "C:\\SyntheticExports\\Result", "csv_name": "materials.csv", "archives": [
                {"name": "SAFE_0001_G03_1K.zip", "sha256": "b" * 64, "size": 321}], "error_code": None}
        return self.values[key]

    def frozen(self, identifier, actor):
        self.status(identifier, actor)
        return self.requests[str(identifier)]

    def unchanged_sources(self, identifier, actor):
        return nullcontext()


@pytest.fixture
def export_case(access_case):
    case = access_case; adapter = ExportStub()
    case.app = create_app(case.app.state.settings, case.database, case.worker, local_publication=adapter)
    with case.database.session() as session:
        category = OnlineCategory(value="Concrete", normalized_key="concrete", abbreviation="G03")
        session.add(category)
        for entry in case.materials:
            material = session.get(PBRMaterial, entry.id)
            material.folder_path = "Brand/" + material.technical_identity; material.workflow_status = "DONE"
            metadata = session.get(PBRMaterialMetadata, material.id)
            metadata.hex_color = "#ABCDEF"; metadata.width_cm = Decimal("10"); metadata.height_cm = Decimal("20")
            session.add(MaterialContent(material_id=material.id, revision=1, description=None, credits=3, tags=[]))
        session.commit()
    return case, adapter


def preview(client, case):
    response = client.post(PATH + "/preview", json={"material_ids": [str(item.id) for item in case.materials]})
    assert response.status_code == 200, response.text
    return response.json()


def export(client, case):
    result = preview(client, case)
    assert result["can_prepare"], result
    body = {"material_ids": [str(item.id) for item in case.materials], "expected_preview_hash": result["preview_hash"],
        "idempotency_key": str(uuid4()), "destination_token": "x" * 43}
    response = client.post(PATH + "/exports", json=body)
    assert response.status_code == 202, response.text
    return body, response.json()


def test_review_and_export_need_complete_data_but_not_legacy_approvals_and_do_not_publish(export_case):
    case, adapter = export_case
    with case.client("ADMIN") as client:
        view = preview(client, case)
        assert view["can_prepare"] and view["items"][0]["row"]["color"] == "#ABCDEF"
        assert {item["code"] for item in view["items"][0]["warnings"]} == {"CONTENT_DESCRIPTION_EMPTY", "CONTENT_TAGS_EMPTY"}
        body, result = export(client, case)
        assert result["status"] == "COMPLETED" and result["published"] is False
        assert result["archives"][0]["size_bytes"] == 321
        assert client.post(PATH + "/exports", json=body).json() == result
        assert adapter.calls == ["export"]
    with case.database.session() as session:
        assert all(not session.get(PBRMaterial, item.id).is_published for item in case.materials)
    csv = bytes.fromhex(adapter.requests[body["idempotency_key"]]["csv_hex"])
    assert csv.startswith(b"\xef\xbb\xbfidentity_name;name;description;credits;")
    assert b"10x20 cm" in csv


def test_trusted_original_master_observation_is_frozen_into_preview_and_job(export_case):
    case, adapter = export_case
    identifier = str(case.materials[0].id)
    with case.client("ADMIN") as client:
        initial = preview(client, case)
        adapter.original_master_observations = {identifier: "2024-04-22T19:51:34+00:00"}
        revised = preview(client, case)
        assert revised["preview_hash"] != initial["preview_hash"]
        body, _ = export(client, case)
        frozen = adapter.requests[body["idempotency_key"]]
        item = next(item for item in frozen["materials"] if item["material_id"] == identifier)
        assert item["original_master_modified_at"] == "2024-04-22T19:51:34+00:00"


@pytest.mark.parametrize("role,status", [(None, 401), ("PROCESSOR", 403), ("LEADERSHIP", 403), ("OTHER", 403), ("ADMIN", 200), ("PRODUCTION_LEAD", 200)])
def test_export_roles(export_case, role, status):
    case, adapter = export_case
    with case.client(role) as client:
        assert client.post(PATH + "/preview", json={"material_ids": [str(case.materials[0].id)]}).status_code == status
        assert client.post(PATH + "/destination").status_code == status
    assert adapter.calls == (["picker"] if status == 200 else [])


def test_missing_fields_mismatching_source_and_stale_review_block_all_output(export_case):
    case, adapter = export_case
    with case.client("ADMIN") as client:
        first = preview(client, case)
        adapter.color = "#FFFFFF"
        second = preview(client, case)
        assert not second["can_prepare"]
        assert "METADATA_SOURCE_DATABASE_MISMATCH" in second["items"][0]["errors"]
        body = {"material_ids": [str(item.id) for item in case.materials], "expected_preview_hash": first["preview_hash"],
            "idempotency_key": str(uuid4()), "destination_token": "x" * 43}
        assert client.post(PATH + "/exports", json=body).status_code == 409
        adapter.color = "#ABCDEF"
        with case.database.session() as session:
            session.get(MaterialContent, case.materials[1].id).credits = None
            session.commit()
        latest = preview(client, case)
        assert "CONTENT_CREDITS_REQUIRED" in latest["items"][1 if latest["items"][1]["material_id"] == str(case.materials[1].id) else 0]["errors"]
        assert client.post(PATH + "/exports", json={**body, "expected_preview_hash": latest["preview_hash"]}).status_code == 409
    assert not adapter.calls


def test_mark_published_is_atomic_audited_and_replayable(export_case):
    case, adapter = export_case
    with case.client("ADMIN") as client:
        body, result = export(client, case)
        endpoint = PATH + "/exports/" + result["id"] + "/published"
        mark = {"idempotency_key": str(uuid4())}
        first = client.post(endpoint, json=mark)
        assert first.status_code == 200, first.text
        assert first.json()["published"]
        assert client.post(endpoint, json=mark).json() == first.json()
        assert client.get(PATH + "/exports/" + result["id"]).json()["published"] is True
    with case.database.session() as session:
        assert all(session.get(PBRMaterial, item.id).is_published for item in case.materials)
        assert session.scalar(select(func.count()).select_from(ResourceChangeEvent).where(ResourceChangeEvent.kind == "MATERIAL")) == 2
        assert session.scalar(select(func.count()).select_from(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "LOCAL_PUBLICATION_MARKED_PUBLISHED")) == 1


def test_one_changed_member_blocks_every_published_write(export_case):
    case, adapter = export_case
    with case.client("ADMIN") as client:
        _, result = export(client, case)
        with case.database.session() as session:
            item = session.get(PBRMaterial, case.materials[1].id)
            item.note = "Changed after export"; item.updated_at += timedelta(seconds=1); session.commit()
        reply = client.post(PATH + "/exports/" + result["id"] + "/published", json={"idempotency_key": str(uuid4())})
        assert reply.status_code == 409 and reply.json()["detail"]["code"] == "LOCAL_EXPORT_MATERIAL_CHANGED"
    with case.database.session() as session:
        assert all(not session.get(PBRMaterial, item.id).is_published for item in case.materials)


def test_csrf_remote_host_arbitrary_paths_and_foreign_actor_are_rejected(export_case):
    case, adapter = export_case
    with case.client("ADMIN") as client:
        body, result = export(client, case)
        assert client.post(PATH + "/exports", json={**body, "destination_path": "C:/Private"}).status_code == 422
        client.headers.pop("X-CSRF-Token")
        assert client.post(PATH + "/destination").status_code == 403
    with case.client("PRODUCTION_LEAD") as client:
        assert client.get(PATH + "/exports/" + result["id"]).status_code == 404
    with TestClient(case.app, base_url=ORIGIN, client=("198.51.100.1", 3000)) as client:
        login = client.post("/api/auth/login", json={"email": case.users["ADMIN"].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"], "X-Forwarded-For": "127.0.0.1"})
        assert client.post(PATH + "/destination").status_code == 403
    assert adapter.calls == ["export"]
