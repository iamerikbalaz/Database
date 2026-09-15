import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import MaterialAuditEvent, MaterialInventory, MaterialReviewState, PBRMaterial, PBRMaterialMetadataSnapshot
from app.inventory_client import InventoryClientError, SourceInventory
from app.main import create_app
from test_application_access import access_case  # real-session fixture
from test_inventory_client import inventory_payload, rehash
from test_material_operations import _preflight


class InventoryStub:
    def __init__(self): self.calls = []; self.callback = None; self.failure = None; self.version = 1
    def inventory(self, path):
        self.calls.append(path)
        if self.callback: self.callback()
        if self.failure: raise self.failure
        value = inventory_payload(path.rsplit("/", 1)[-1])
        value["entries"][1]["sha256"] = str(self.version) * 64
        return SourceInventory.model_validate_json(json.dumps(rehash(value)))


@pytest.fixture
def review_case(access_case):
    case = access_case
    inventory = InventoryStub()
    case.app = create_app(case.app.state.settings, case.database, case.worker, inventory)
    with case.database.session() as session:
        for item in case.materials: session.get(PBRMaterial, item.id).folder_path = "library/" + item.technical_identity
        session.commit()
    return case, inventory, f"/api/materials/{case.materials[0].id}"


def scan(client, path, key=None, generation=None):
    if generation is None: generation = client.get(path + "/review").json()["generation"]
    return client.post(path + "/inventory/scan", json={"idempotency_key": str(key or uuid4()), "expected_generation": generation})


def test_inventory_persists_and_idempotent_replay_does_not_rescan(review_case):
    case, worker, path = review_case
    with case.client("PROCESSOR") as client:
        assert client.get(path + "/review").json()["generation"] == 0
        key = uuid4(); first = scan(client, path, key, 0)
        assert first.status_code == 200 and first.json()["generation"] == 1
        assert scan(client, path, key, 0).json() == first.json()
        assert len(worker.calls) == 1
        current = client.get(path + "/inventory").json()
        assert current["review"] == first.json() and len(current["inventory"]["entries"]) == 2
        assert "raw_content" not in json.dumps(current)
        assert scan(client, path, key, 1).status_code == 409
        assert len(worker.calls) == 1
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialInventory)) == 1
        assert session.scalar(select(func.count()).select_from(MaterialAuditEvent)) == 1


def test_same_content_retains_generation_and_changed_content_invalidates_it(review_case):
    case, worker, path = review_case
    with case.client("ADMIN") as client:
        first = scan(client, path).json(); same = scan(client, path).json()
        assert same["generation"] == first["generation"] and same["revision_hash"] == first["revision_hash"]
        worker.version = 2
        changed = scan(client, path).json()
        assert changed["generation"] == first["generation"] + 1
        assert changed["revision_hash"] != first["revision_hash"]
        assert scan(client, path, generation=0).status_code == 409
        assert len(worker.calls) == 3


def test_failed_scan_invalidates_review_and_keeps_published_flag(review_case):
    case, worker, path = review_case
    with case.client("ADMIN") as client:
        first = scan(client, path).json()
        with case.database.session() as session:
            material = session.get(PBRMaterial, case.materials[0].id)
            material.is_published = True; material.publication_status = "PUBLISHED_CURRENT"; session.commit()
        worker.failure = InventoryClientError("INVENTORY_SOURCE_CHANGED")
        failed = scan(client, path)
        assert failed.status_code == 422 and failed.json()["detail"]["code"] == "INVENTORY_SOURCE_CHANGED"
        review = client.get(path + "/review").json()
        assert review["revision_hash"] is None and review["inventory_id"] is None
        assert review["generation"] == first["generation"] + 1
        material = client.get(path).json()
        assert material["is_published"] is True and material["publication_status"] == "PUBLISHED_UPDATE_REQUIRED"
        assert client.get(path + "/inventory").json()["inventory"] is None


def test_material_edit_and_folder_change_clear_current_inventory(review_case):
    case, _, path = review_case
    with case.client("ADMIN") as client:
        first = scan(client, path).json()
        assert client.patch(path, json={"material_name": "Reviewed rename"}).status_code == 200
        state = client.get(path + "/review").json()
        assert state["revision_hash"] is None and state["generation"] == first["generation"] + 1
        scan(client, path)
        case.worker.queue(_preflight("SAFE_0001_G03").model_copy(update={"folder_path": "alternate/SAFE_0001_G03"}))
        assert client.post(path + "/folder-link", json={"folder_path": "alternate/SAFE_0001_G03"}).status_code == 200
        assert client.get(path + "/review").json()["failure_code"] == "MATERIAL_FOLDER_CHANGED"


def test_reopen_is_audited_preserves_snapshots_and_clears_current_metadata(review_case):
    case, _, path = review_case
    with case.client("ADMIN") as client:
        case.worker.queue(_preflight("SAFE_0001_G03"))
        done = client.post(path + "/mark-done")
        assert done.status_code == 200
        snapshot_id = done.json()["snapshot"]["id"]
        current = scan(client, path).json()
        payload = {"idempotency_key": str(uuid4()), "expected_generation": current["generation"], "reason": "Correct the master maps"}
        reopened = client.post(path + "/reopen", json=payload)
        assert reopened.status_code == 200
        assert client.post(path + "/reopen", json=payload).json() == reopened.json()
        assert client.get(path).json()["workflow_status"] == "IN_PROGRESS"
        metadata = client.get(path + "/metadata").json()
        assert metadata["status"] == "NOT_SCANNED" and metadata["current_snapshot_id"] is None
        history = client.get(path + "/metadata/snapshots").json()
        assert len(history) == 1 and history[0]["id"] == snapshot_id
        assert client.get(path + "/review").json()["revision_hash"] is None
        case.worker.queue(_preflight("SAFE_0001_G03"))
        assert client.post(path + "/mark-done").status_code == 200
        assert len(client.get(path + "/metadata/snapshots").json()) == 2
        events = client.get(path + "/audit").json()
        assert sum(item["event_type"] == "REOPENED" for item in events) == 1
        assert next(item for item in events if item["event_type"] == "REOPENED")["details"]["reason"] == payload["reason"]


@pytest.mark.parametrize("role,read,write", [("ADMIN", True, True), ("PRODUCTION_LEAD", True, True), ("LEADERSHIP", True, False), ("PROCESSOR", True, True), ("OTHER", False, False)])
def test_review_permissions_match_assignment_and_least_privilege(review_case, role, read, write):
    case, worker, path = review_case
    with case.client(role) as client:
        for suffix in ("/review", "/inventory", "/audit"):
            assert client.get(path + suffix).status_code == (200 if read else 404)
        result = scan(client, path, generation=0)
        assert result.status_code == (200 if write else 403 if role == "LEADERSHIP" else 404)
        if not write: assert not worker.calls
        if role in {"PROCESSOR", "OTHER", "LEADERSHIP"}:
            assert client.post(path + "/reopen", json={"idempotency_key": str(uuid4()), "expected_generation": 0, "reason": "Not allowed"}).status_code == 403


@pytest.mark.parametrize("change", ["assignment", "name", "reopen-generation"])
def test_stale_worker_result_cannot_overwrite_a_concurrent_change(review_case, change):
    case, worker, path = review_case
    with case.client("PROCESSOR") as client:
        scan(client, path)
        def mutate():
            with case.database.session() as session:
                material = session.get(PBRMaterial, case.materials[0].id)
                if change == "assignment": material.assigned_processor_id = case.users["OTHER"].id
                elif change == "name": material.material_name = "Changed during scan"
                else: session.get(MaterialReviewState, material.id).generation += 1
                session.commit()
        worker.callback = mutate
        assert scan(client, path).status_code == (404 if change == "assignment" else 409)
        with case.database.session() as session:
            assert session.scalar(select(func.count()).select_from(MaterialInventory)) == 1


def test_reopen_requires_done_and_reason_and_idempotency_key(review_case):
    case, _, path = review_case
    with case.client("ADMIN") as client:
        assert client.post(path + "/reopen", json={}).status_code == 422
        assert client.post(path + "/reopen", json={"idempotency_key": str(uuid4()), "expected_generation": 0, "reason": " "}).status_code == 422
        assert client.post(path + "/reopen", json={"idempotency_key": str(uuid4()), "expected_generation": 0, "reason": "Valid reason"}).status_code == 409
        assert client.post(path + "/inventory/scan", json={}).status_code == 422
