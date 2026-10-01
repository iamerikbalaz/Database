from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, func

from app.db.models import PBRMaterial, MaterialAuditEvent
from app.db.preview_edit_models import PreviewEditOperation, PreviewEditOwner
from app.local_filesystem import LocalFilesError
from app.main import create_app
from test_application_access import access_case


class Adapter:
    def __init__(self):
        self.current = {"names": ["SPHERE_1.png", "SPHERE_2.png", "OLD_1.png", "keep.txt"], "directory_identity": [1, 2],
            "files": [{"name": name, "size": 10, "sha256": "a" * 64, "identity": [1, i]} for i, name in enumerate(["SPHERE_1.png", "SPHERE_2.png", "OLD_1.png"])]}
        self.calls = []; self.fail = False; self.reject = False
    def snapshot(self, folder): return deepcopy(self.current)
    def execute(self, operation, item):
        self.calls.append((operation, item))
        if self.fail: raise LocalFilesError("PREVIEW_EDIT_IO_INTERRUPTED")
        if self.reject: return {"status": "REJECTED", "error_code": "PREVIEW_SOURCE_CHANGED"}
        return {"status": "COMPLETED", "error_code": None,
            "renamed": sum(change["to"] is not None for change in item["changes"]),
            "deleted": sum(change["to"] is None for change in item["changes"])}


@pytest.fixture
def case(access_case, monkeypatch):
    adapter = Adapter()
    monkeypatch.setattr("app.api.preview_edits.LocalPreviewEdits", lambda library: adapter)
    settings = access_case.app.state.settings.model_copy(update={"source_mutations_enabled": True})
    access_case.app = create_app(settings, access_case.database, access_case.worker, local_library=object())
    with access_case.database.session() as session:
        for item in access_case.materials:
            material = session.get(PBRMaterial, item.id)
            material.folder_path = "SAFE/" + material.technical_identity
            material.automatic_file_check_status = "OK"; material.automatic_file_check_complete = True
            material.is_published = True; material.checked_status = "OK"
        session.commit()
    return access_case, adapter


def selection(client, materials):
    return [{"id": str(item.id), "expected_updated_at": client.get("/api/materials/" + str(item.id)).json()["updated_at"]} for item in materials]


def plan(client, materials, **changes):
    payload = {"materials": selection(client, materials), "action": "BULK", "find": "SPHERE", "replace": "FABRIC", "delete_containing": "OLD", **changes}
    response = client.post("/api/material-preview-edits/plan", json=payload)
    assert response.status_code == 200, response.text
    return payload, response.json()


def apply_body(payload, preview):
    return {**payload, "idempotency_key": str(uuid4()), "confirmed": True, "expected_proposal_hash": preview["proposal_hash"]}


def test_combined_plan_counts_confirm_apply_audit_invalidation_and_exact_replay(case):
    case, adapter = case
    with case.client("ADMIN") as client:
        payload, preview = plan(client, case.materials)
        assert preview["can_apply"] and preview["total_renames"] == 4 and preview["total_deletes"] == 2
        assert len(preview["items"]) == 2 and not adapter.calls
        body = apply_body(payload, preview)
        result = client.post("/api/material-preview-edits", json=body)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "COMPLETED" and result.json()["total_renames"] == 4
        assert client.post("/api/material-preview-edits", json=body).json() == result.json()
        assert len(adapter.calls) == 2
        assert client.post("/api/material-preview-edits", json={**body, "replace": "DIFFERENT"}).status_code == 409
        for item in case.materials:
            row = client.get("/api/materials/" + str(item.id)).json()
            assert row["automatic_file_check_status"] == "NOT_CHECKED" and row["checked_status"] == "no" and not row["is_published"]
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PreviewEditOwner)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialAuditEvent).where(MaterialAuditEvent.event_type == "PREVIEW_FILES_EDITED")) == 2


def test_stale_material_or_file_plan_and_missing_confirmation_never_write(case):
    case, adapter = case
    with case.client("ADMIN") as client:
        payload, preview = plan(client, case.materials[:1]); body = apply_body(payload, preview)
        assert client.post("/api/material-preview-edits", json={**body, "confirmed": False}).status_code == 422
        adapter.current["files"][0]["sha256"] = "b" * 64
        assert client.post("/api/material-preview-edits", json=body).json()["detail"]["code"] == "PREVIEW_PLAN_CHANGED"
        material = case.materials[0]
        changed = client.patch("/api/materials/" + str(material.id) + "/table", headers={"Idempotency-Key": str(uuid4())},
            json={"note": "revision changes", "expected_updated_at": payload["materials"][0]["expected_updated_at"]})
        assert changed.status_code == 200
        assert client.post("/api/material-preview-edits/plan", json=payload).status_code == 409
    assert not adapter.calls
    with case.database.session() as session: assert session.scalar(select(func.count()).select_from(PreviewEditOperation)) == 0


def test_terminal_receipt_remains_readable_after_archive_and_cannot_edit_again(case):
    case, adapter = case
    with case.client("ADMIN") as client:
        payload, preview = plan(client, case.materials[:1]); body = apply_body(payload, preview)
        receipt = client.post("/api/material-preview-edits", json=body).json()
        lifecycle = "/api/material-archives/" + str(case.materials[0].id)
        archival = client.get(lifecycle + "/preview", params={"action": "ARCHIVE"}).json()
        assert archival["can_apply"]
        archived = client.post(lifecycle + "/commands", json={"action": "ARCHIVE", "request_key": str(uuid4()),
            "expected_version": archival["version"], "expected_input_sha256": archival["input_sha256"], "acknowledge": True})
        assert archived.status_code == 200, archived.text
        assert client.get("/api/material-preview-edits/" + receipt["id"]).json() == receipt
        assert client.post("/api/material-preview-edits", json=body).json() == receipt
        assert client.post("/api/material-preview-edits/plan", json=payload).status_code == 404
    assert len(adapter.calls) == 1


def test_no_matching_filenames_do_not_reset_statuses_or_create_operation(case):
    case, adapter = case
    with case.client("ADMIN") as client:
        payload, preview = plan(client, case.materials[:1], find="not-present", delete_containing=None)
        assert not preview["can_apply"] and preview["total_renames"] == 0 and preview["total_deletes"] == 0
        assert client.post("/api/material-preview-edits", json=apply_body(payload, preview)).status_code == 409
        material = client.get("/api/materials/" + str(case.materials[0].id)).json()
        assert material["checked_status"] == "OK" and material["is_published"]
    assert not adapter.calls
    with case.database.session() as session: assert session.scalar(select(func.count()).select_from(PreviewEditOperation)) == 0


def test_interrupted_files_hold_ownership_and_can_be_discovered_and_admin_resumed(case):
    case, adapter = case; adapter.fail = True
    with case.client("PROCESSOR") as client:
        payload, preview = plan(client, case.materials[:1])
        result = client.post("/api/material-preview-edits", json=apply_body(payload, preview)).json()
        assert result["status"] == "RECOVERY_REQUIRED"
        assert client.get("/api/material-preview-edits", params={"material_id": str(case.materials[0].id)}).json()["items"][0]["id"] == result["id"]
        assert client.patch("/api/materials/" + str(case.materials[0].id) + "/table", headers={"Idempotency-Key": str(uuid4())},
            json={"note": "blocked", "expected_updated_at": result["items"][0]["updated_at"]}).status_code == 409
    with case.client("PRODUCTION_LEAD") as client:
        assert client.get("/api/material-preview-edits/" + result["id"]).status_code == 404
    adapter.fail = False
    with case.client("ADMIN") as client:
        resumed = client.post("/api/material-preview-edits/" + result["id"] + "/resume", json={"confirmed": True})
        assert resumed.status_code == 200 and resumed.json()["status"] == "COMPLETED"
        assert client.get("/api/material-preview-edits").json() == {"items": []}


def test_pending_discovery_scopes_selection_before_limit_and_deduplicates_operations(case):
    case, adapter = case; adapter.fail = True
    with case.client("ADMIN") as client:
        payload, preview = plan(client, case.materials)
        receipt = client.post("/api/material-preview-edits", json=apply_body(payload, preview)).json()
        with case.database.session() as session:
            original = session.get(PreviewEditOperation, UUID(receipt["id"]))
            for number in range(31):
                session.add(PreviewEditOperation(actor_id=original.actor_id, request_key=uuid4(),
                    request_hash="a" * 64, proposal_hash="b" * 64, request_payload={}, plan={"items": []},
                    items=[], status="RECOVERY_REQUIRED", created_at=datetime.now(UTC) + timedelta(seconds=number + 1)))
            session.commit()
        assert receipt["id"] not in {row["id"] for row in client.get("/api/material-preview-edits").json()["items"]}
        selected = [("material_ids", str(material.id)) for material in case.materials]
        pending = client.get("/api/material-preview-edits", params=selected)
        assert pending.status_code == 200 and [row["id"] for row in pending.json()["items"]] == [receipt["id"]]
        assert client.get("/api/material-preview-edits", params=selected * 51).status_code == 422


@pytest.mark.parametrize("role,code", [(None,401),("LEADERSHIP",403),("OTHER",404)])
def test_preview_mutation_authorization_and_assignment(case, role, code):
    case, adapter = case
    with case.client("ADMIN") as client: payload, preview = plan(client, case.materials[:1])
    with case.client(role) as client:
        assert client.post("/api/material-preview-edits/plan", json=payload).status_code == code
        assert client.post("/api/material-preview-edits", json=apply_body(payload, preview)).status_code == code
    assert not adapter.calls


def test_csrf_and_collisions_are_rejected_without_io(case):
    case, adapter = case
    with case.client("ADMIN") as client:
        payload, preview = plan(client, case.materials[:1], action="RENAME", filename="SPHERE_1.png", new_name="sphere_2.PNG", find=None, replace=None, delete_containing=None)
        assert not preview["can_apply"] and preview["items"][0]["issues"][0]["code"] == "PREVIEW_NAME_COLLISION"
        assert client.post("/api/material-preview-edits", json=apply_body(payload, preview)).status_code == 409
        client.headers.pop("X-CSRF-Token")
        assert client.post("/api/material-preview-edits/plan", json=payload).status_code == 403
    assert not adapter.calls


def test_explicit_runtime_mutation_switch_is_required_for_plan_apply_and_resume(case):
    case, adapter = case
    with case.client("ADMIN") as client: payload, preview = plan(client, case.materials[:1])
    disabled = case.app.state.settings.model_copy(update={"source_mutations_enabled": False})
    case.app = create_app(disabled, case.database, case.worker, local_library=object())
    with case.client("ADMIN") as client:
        for path, body in (("/plan", payload), ("", apply_body(payload, preview)), ("/" + str(uuid4()) + "/resume", {"confirmed": True})):
            response = client.post("/api/material-preview-edits" + path, json=body)
            assert response.status_code == 403 and response.json()["detail"]["code"] == "SOURCE_MUTATIONS_DISABLED"
    assert not adapter.calls


@pytest.mark.parametrize("name", ["../SPHERE.png", "sub/file.png", "foo\\bar.png", "file.png:stream", "CON.png", "x.jpg", "bad?.png", "x.png "])
def test_only_safe_direct_png_names(case, name):
    case, adapter = case
    with case.client("ADMIN") as client:
        payload = {"materials": selection(client, case.materials[:1]), "action": "DELETE", "filename": name}
        assert client.post("/api/material-preview-edits/plan", json=payload).status_code == 422
    assert not adapter.calls
