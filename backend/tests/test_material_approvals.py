import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import MaterialApproval, MaterialTechnicalCheck, MaterialReviewState, PBRMaterial, UserCredential
from app.inventory_client import InventoryClientError
from app.main import create_app
from app.technical_client import TechnicalReport
from test_application_access import access_case
from test_material_review import InventoryStub, scan
from test_technical_client import technical_payload
from test_inventory_client import rehash


class TechnicalStub:
    def __init__(self): self.calls = []; self.callback = None; self.failure = None; self.version = 1; self.warnings = []; self.errors = []
    def validate(self, path):
        self.calls.append(path)
        if self.callback: self.callback()
        if self.failure: raise self.failure
        value = technical_payload(path.rsplit("/", 1)[-1])
        value["inventory"]["entries"][1]["sha256"] = value["images"][0]["sha256"] = str(self.version) * 64
        rehash(value["inventory"])
        value.update(warnings=self.warnings, errors=self.errors, can_approve=not self.errors)
        return TechnicalReport.model_validate_json(json.dumps(value))


@pytest.fixture
def approval_case(access_case):
    case = access_case; worker = TechnicalStub(); inventory = InventoryStub()
    case.app = create_app(case.app.state.settings, case.database, case.worker, inventory, worker)
    with case.database.session() as session:
        for item in case.materials:
            material = session.get(PBRMaterial, item.id)
            material.folder_path = "library/" + material.technical_identity
            material.workflow_status = "DONE"
        session.commit()
    return case, worker, f"/api/materials/{case.materials[0].id}"


def run(client, path, key=None, generation=None):
    if generation is None: generation = client.get(path + "/review").json()["generation"]
    return client.post(path + "/technical-review/run", json={"idempotency_key": str(key or uuid4()), "expected_generation": generation})


def approval_payload(view, kind="TECHNICAL", **extra):
    return {"idempotency_key": str(uuid4()), "expected_generation": view["review"]["generation"],
            "expected_revision_hash": view["review"]["revision_hash"], "technical_check_id": view["validation"]["id"], "kind": kind, **extra}


def test_two_approvals_require_fresh_checks_and_persist_exact_revision(approval_case):
    case, worker, path = approval_case
    with case.client("PRODUCTION_LEAD") as lead:
        checked = run(lead, path); assert checked.status_code == 200
        payload = approval_payload(checked.json())
        approved = lead.post(path + "/approvals", json=payload)
        assert approved.status_code == 200 and len(worker.calls) == 2
        assert lead.post(path + "/approvals", json=payload).json() == approved.json() and len(worker.calls) == 2
        view = approved.json()
        assert [item["kind"] for item in view["approvals"]] == ["TECHNICAL"]
        assert view["approvals"][0]["revision_hash"] == view["review"]["revision_hash"]
    with case.client("LEADERSHIP") as leadership:
        published = leadership.post(path + "/approvals", json=approval_payload(view, "PUBLICATION"))
        assert published.status_code == 200 and len(worker.calls) == 3
        approvals = published.json()["approvals"]
        assert {item["kind"] for item in approvals} == {"TECHNICAL", "PUBLICATION"}
        assert next(item for item in approvals if item["kind"] == "PUBLICATION")["technical_approval_id"] == view["approvals"][0]["id"]
        material = leadership.get(path).json()
        assert material["is_published"] is False and material["workflow_status"] == "DONE"


def test_validation_replay_is_exact_and_reused_key_cannot_change_input(approval_case):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        key = uuid4(); first = run(client, path, key, 0)
        assert run(client, path, key, 0).json() == first.json() and len(worker.calls) == 1
        assert run(client, path, key, 1).status_code == 409
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialTechnicalCheck)) == 1


@pytest.mark.parametrize("role,technical,publication,check", [("ADMIN", 200, 200, 200), ("PRODUCTION_LEAD", 200, 403, 200),
    ("LEADERSHIP", 403, 200, 403), ("PROCESSOR", 403, 403, 200), ("OTHER", 403, 403, 404)])
def test_approval_roles_are_enforced_by_server(approval_case, role, technical, publication, check):
    case, worker, path = approval_case
    with case.client("ADMIN") as admin:
        initial = run(admin, path).json()
    with case.client(role) as client:
        response = client.post(path + "/approvals", json=approval_payload(initial))
        assert response.status_code == technical
        assert client.get(path + "/technical-review").status_code == (404 if role == "OTHER" else 200)
    with case.client("ADMIN") as admin:
        if technical != 200: assert admin.post(path + "/approvals", json=approval_payload(initial)).status_code == 200
        current = admin.get(path + "/technical-review").json()
    with case.client(role) as client:
        assert client.post(path + "/approvals", json=approval_payload(current, "PUBLICATION")).status_code == publication
        assert run(client, path, generation=current["review"]["generation"]).status_code == check


def test_warnings_require_acknowledgment_and_note_but_errors_block(approval_case):
    case, worker, path = approval_case
    worker.warnings = [{"code": "SOURCE_METADATA_MISSING", "path": "metadata.txt"}]
    with case.client("ADMIN") as client:
        current = run(client, path).json()
        for extra in ({}, {"warnings_acknowledged": True}, {"note": "Accepted missing metadata"}):
            assert client.post(path + "/approvals", json=approval_payload(current, **extra)).status_code == 422
        assert len(worker.calls) == 1
        assert client.post(path + "/approvals", json=approval_payload(current, warnings_acknowledged=True, note="Accepted missing metadata")).status_code == 200
        worker.errors = [{"code": "IMAGE_UNREADABLE", "path": ""}]
        current = run(client, path).json()
        assert current["approvals"] == [] and current["validation"]["report"]["can_approve"] is False
        assert client.post(path + "/approvals", json=approval_payload(current, warnings_acknowledged=True, note="Cannot override errors")).status_code == 409


@pytest.mark.parametrize("change", ["source", "findings", "failure"])
def test_fresh_approval_recheck_invalidates_stale_approval_on_change(approval_case, change):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        initial = run(client, path).json()
        approved = client.post(path + "/approvals", json=approval_payload(initial)).json()
        if change == "source": worker.version = 2
        elif change == "findings": worker.warnings = [{"code": "PREVIEW_MISSING", "path": ""}]
        else: worker.failure = InventoryClientError("INVENTORY_SOURCE_CHANGED")
        payload = approval_payload(approved, "PUBLICATION")
        result = client.post(path + "/approvals", json=payload)
        assert result.status_code == (422 if change == "failure" else 409)
        assert client.post(path + "/approvals", json=payload).json() == result.json()
        current = client.get(path + "/technical-review").json()
        assert current["approvals"] == [] and current["review"]["generation"] > approved["review"]["generation"]
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialApproval)) == 1


def test_reopen_and_edit_cannot_revive_old_approvals_with_same_files(approval_case):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        initial = run(client, path).json()
        approved = client.post(path + "/approvals", json=approval_payload(initial)).json()
        assert client.post(path + "/reopen", json={"idempotency_key": str(uuid4()), "expected_generation": approved["review"]["generation"], "reason": "Rework"}).status_code == 200
        assert client.get(path + "/technical-review").json()["approvals"] == []
        current = run(client, path).json()
        assert current["review"]["revision_hash"] == approved["review"]["revision_hash"]
        assert current["approvals"] == []
        assert client.post(path + "/approvals", json=approval_payload(current)).status_code == 409
        assert client.patch(path, json={"material_name": "Updated product name"}).status_code == 200
        assert client.get(path + "/technical-review").json()["validation"] is None


def test_publication_requires_technical_approval_and_current_report(approval_case):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        initial = run(client, path).json()
        assert client.post(path + "/approvals", json=approval_payload(initial, "PUBLICATION")).status_code == 409
        latest = run(client, path).json()
        assert client.post(path + "/approvals", json=approval_payload(initial)).status_code == 409
        assert client.post(path + "/approvals", json=approval_payload(latest)).status_code == 200


def test_unchanged_inventory_and_revalidation_keep_approval_but_reject_duplicate_decision(approval_case):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        first = run(client, path).json()
        approved = client.post(path + "/approvals", json=approval_payload(first)).json()
        assert scan(client, path).status_code == 200
        repeated = run(client, path).json()
        assert repeated["review"]["generation"] == approved["review"]["generation"]
        assert repeated["approvals"] == approved["approvals"]
        assert client.post(path + "/approvals", json=approval_payload(repeated)).status_code == 409
        assert client.post(path + "/approvals", json=approval_payload(repeated, "PUBLICATION")).status_code == 200


@pytest.mark.parametrize("change", ["name", "generation", "role", "credentials"])
def test_approval_rechecks_state_and_authorization_after_worker(approval_case, change):
    case, worker, path = approval_case
    with case.client("PRODUCTION_LEAD") as client:
        initial = run(client, path).json()
        def mutate():
            with case.database.session() as session:
                material = session.get(PBRMaterial, case.materials[0].id)
                if change == "name": material.material_name = "Concurrent change"
                elif change == "generation":
                    from app.material_review import invalidate_review
                    invalidate_review(session, material, case.users["PRODUCTION_LEAD"].id, "TEST_INVALIDATION")
                elif change == "role": session.get(type(case.users["PRODUCTION_LEAD"]), case.users["PRODUCTION_LEAD"].id).role = "PROCESSOR"
                else: session.get(UserCredential, case.users["PRODUCTION_LEAD"].id).must_change_password = True
                session.commit()
        worker.callback = mutate
        assert client.post(path + "/approvals", json=approval_payload(initial)).status_code == (403 if change in {"role", "credentials"} else 409)
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialApproval)) == 0
