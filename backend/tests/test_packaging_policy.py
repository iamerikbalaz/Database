import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from app.db.models import MaterialPackagingPolicy, MaterialInventory, MaterialReviewState, MaterialTechnicalCheck, PBRMaterial
from app.material_review import canonical_hash
from app.technical_client import TechnicalReport
from test_application_access import access_case
from test_material_approvals import approval_case, approval_payload, run
from test_inventory_client import rehash

LEGACY = "LEGACY_BEFORE_2026_03_04"
CURRENT = "CURRENT_ON_OR_AFTER_2026_03_04"


def selection(client, path, **changes):
    review = client.get(path + "/review").json()
    return {"idempotency_key": str(uuid4()), "expected_generation": review["generation"],
        "expected_revision_hash": review["revision_hash"], "expected_inventory_id": review["inventory_id"],
        "reason": "Freeze the observed historical archive rule", **changes}


def choose(client, path):
    response = client.post(path + "/packaging-policy/select", json=selection(client, path))
    assert response.status_code == 200, response.json()
    return response.json()["current"]


def override_body(client, path, current, **changes):
    proposal = {"expected_policy_id": current["id"], "policy": LEGACY if current["policy"] == CURRENT else CURRENT}
    preview = client.post(path + "/packaging-policy/override-preview", json=proposal)
    assert preview.status_code == 200, preview.json()
    return {**proposal, "idempotency_key": str(uuid4()), "expected_preview_hash": preview.json()["preview_hash"],
        "reason": "Historical classification reviewed by administrator", **changes}


def test_first_policy_is_frozen_across_new_inventory_reopen_and_exact_replay(approval_case, monkeypatch):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        assert client.get(path + "/packaging-policy").json() == {"current": None}
        run(client, path); body = selection(client, path)
        response = client.post(path + "/packaging-policy/select", json=body)
        assert response.status_code == 200
        saved = response.json()["current"]
        assert saved["policy"] == CURRENT and saved["revision"] == 1 and saved["kind"] == "INITIAL"
        assert saved["evidence_hash"] == canonical_hash(saved["evidence"])
        assert saved["evidence"]["policy_boundary"] == "2026-03-04T00:00:00+01:00"
        original = worker.validate
        def changed(source):
            value = original(source).model_dump(mode="json")
            value["inventory"]["policy"] = LEGACY
            value["inventory"]["master_last_modified_at"] = "2026-03-03T22:59:59.999999999+00:00"
            rehash(value["inventory"])
            return TechnicalReport.model_validate_json(json.dumps(value))
        monkeypatch.setattr(worker, "validate", changed)
        run(client, path)
        assert choose(client, path) == saved
        review = client.get(path + "/review").json()
        assert client.post(path + "/reopen", json={"idempotency_key": str(uuid4()),
            "expected_generation": review["generation"], "reason": "Revise images"}).status_code == 200
        # Once selected, read/reuse never silently falls back to current mtimes.
        assert client.post(path + "/packaging-policy/select", json={**body, "idempotency_key": str(uuid4())}).json() == response.json()
        assert client.post(path + "/packaging-policy/select", json=body).json() == response.json()
        assert client.post(path + "/packaging-policy/select", json={**body, "reason": "Changed request"}).status_code == 409
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialPackagingPolicy)) == 1


@pytest.mark.parametrize("stamp,zone,policy", [
    ("2026-03-03T22:59:59.999999999+00:00", "Europe/Prague", LEGACY),
    ("2026-03-03T23:00:00+00:00", "Europe/Prague", CURRENT),
    ("2026-03-04T00:00:00+01:00", "Europe/Prague", CURRENT),
    ("2026-03-03T23:30:00+00:00", "UTC", LEGACY),
])
def test_exact_boundary_timezone_is_persisted(approval_case, monkeypatch, stamp, zone, policy):
    from app.main import create_app
    case, worker, path = approval_case
    case.app = create_app(case.app.state.settings.model_copy(update={"zip_policy_timezone": zone}), case.database, case.worker, technical_client=worker)
    original = worker.validate
    def adjusted(source):
        value = original(source).model_dump(mode="json")
        value["inventory"].update(master_last_modified_at=stamp, policy=policy)
        rehash(value["inventory"])
        return TechnicalReport.model_validate_json(json.dumps(value))
    monkeypatch.setattr(worker, "validate", adjusted)
    with case.client("ADMIN") as client:
        run(client, path)
        saved = choose(client, path)
        assert saved["policy"] == policy and saved["storage_timezone"] == zone
        assert saved["evidence"]["master_last_modified_at"] == stamp


@pytest.mark.parametrize("change", ["generation", "inventory", "revision", "workflow", "missing-check", "bad-check-hash", "bad-inventory", "timezone"])
def test_first_selection_rejects_stale_or_unverified_source_without_partial_history(approval_case, change):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        run(client, path); body = selection(client, path)
        with case.database.session() as session:
            state = session.get(MaterialReviewState, case.materials[0].id)
            if change == "generation": body["expected_generation"] += 1
            elif change == "inventory": body["expected_inventory_id"] = str(uuid4())
            elif change == "revision": body["expected_revision_hash"] = "a" * 64
            elif change == "workflow": session.get(PBRMaterial, state.material_id).workflow_status = "IN_PROGRESS"
            elif change == "missing-check": state.technical_check_id = None
            elif change == "bad-check-hash":
                session.execute(update(MaterialTechnicalCheck).where(MaterialTechnicalCheck.id == state.technical_check_id).values(report_hash="a" * 64))
            else:
                stored = session.get(MaterialInventory, state.inventory_id)
                value = json.loads(json.dumps(stored.source_inventory))
                if change == "bad-inventory": value["source_revision_hash"] = "b" * 64
                else: value["master_last_modified_at"] = "2026-03-03T20:00:00+00:00"
                session.execute(update(MaterialInventory).where(MaterialInventory.id == stored.id).values(source_inventory=value))
            session.commit()
        response = client.post(path + "/packaging-policy/select", json=body)
        assert response.status_code == 409
        assert client.get(path + "/packaging-policy").json() == {"current": None}
        assert client.get(path + "/packaging-policy/history").json()["items"] == []


def test_admin_preview_override_invalidates_approvals_preserves_history_and_published_flag(approval_case):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        view = run(client, path).json()
        view = client.post(path + "/approvals", json=approval_payload(view)).json()
        client.post(path + "/approvals", json=approval_payload(view, "PUBLICATION"))
        current = choose(client, path)
        with case.database.session() as session:
            material = session.get(PBRMaterial, case.materials[0].id)
            material.is_published = True; material.publication_status = "PUBLISHED_CURRENT"; session.commit()
        body = override_body(client, path, current)
        previous_generation = client.get(path + "/review").json()["generation"]
        response = client.post(path + "/packaging-policy/override", json=body)
        assert response.status_code == 200
        new = response.json()["current"]
        assert new["revision"] == 2 and new["previous_id"] == current["id"] and new["policy"] == LEGACY
        assert new["evidence_hash"] == canonical_hash(new["evidence"])
        view = client.get(path + "/technical-review").json()
        assert view["review"]["generation"] == previous_generation + 1 and view["review"]["revision_hash"] is None
        assert view["approvals"] == []
        material = client.get(path).json()
        assert material["is_published"] is True and material["publication_status"] == "PUBLISHED_UPDATE_REQUIRED"
        assert client.post(path + "/packaging-policy/override", json=body).json() == response.json()
        assert client.post(path + "/packaging-policy/override", json={**body, "reason": "Different"}).status_code == 409
        assert client.post(path + "/packaging-policy/override", json={**body, "idempotency_key": str(uuid4())}).status_code == 409
        history = client.get(path + "/packaging-policy/history", params={"limit": 1}).json()
        assert history["items"] == [new] and history["next_before"] == 2
        assert client.get(path + "/packaging-policy/history", params={"before": 2}).json()["items"] == [current]
        assert client.get(path + "/packaging-policy/history", params={"limit": 51}).status_code == 422
        audit = client.get(path + "/audit").json()
        assert sum(item["event_type"] == "PACKAGING_POLICY_OVERRIDDEN" for item in audit) == 1


@pytest.mark.parametrize("role,read,select_code,override_code", [
    (None, 401, 401, 401), ("ADMIN", 200, 200, 200), ("LEADERSHIP", 200, 200, 403),
    ("PRODUCTION_LEAD", 200, 403, 403), ("PROCESSOR", 200, 403, 403), ("OTHER", 404, 403, 403),
])
def test_policy_roles_and_assignment_are_enforced(approval_case, role, read, select_code, override_code):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        run(client, path); body = selection(client, path); current = choose(client, path)
        override = override_body(client, path, current)
    with case.client(role) as client:
        for suffix in ("", "/history"):
            assert client.get(path + "/packaging-policy" + suffix).status_code == read
        assert client.post(path + "/packaging-policy/select", json=body).status_code == select_code
        assert client.post(path + "/packaging-policy/override-preview", json={
            "policy": override["policy"], "expected_policy_id": current["id"]}).status_code == override_code
        assert client.post(path + "/packaging-policy/override", json=override).status_code == override_code


def test_override_needs_current_preview_reason_and_csrf(approval_case):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        run(client, path); current = choose(client, path)
        body = override_body(client, path, current)
        assert client.post(path + "/packaging-policy/override", json={**body, "reason": " "}).status_code == 422
        assert client.post(path + "/packaging-policy/override", json={**body, "expected_preview_hash": "a" * 64}).status_code == 409
        assert client.post(path + "/packaging-policy/override-preview", json={
            "policy": current["policy"], "expected_policy_id": current["id"]}).status_code == 409
        assert client.patch(path, json={"material_name": "Changed after preview"}).status_code == 200
        assert client.post(path + "/packaging-policy/override", json=body).json()["detail"]["code"] == "PACKAGING_POLICY_PREVIEW_CHANGED"
        client.headers.pop("X-CSRF-Token")
        assert client.post(path + "/packaging-policy/override", json=body).status_code == 403


def test_policy_orm_history_is_immutable(approval_case):
    case, worker, path = approval_case
    with case.client("ADMIN") as client:
        run(client, path); current = choose(client, path)
    for delete in (False, True):
        with case.database.session() as session:
            decision = session.get(MaterialPackagingPolicy, UUID(current["id"]))
            if delete: session.delete(decision)
            else: decision.reason = "Rewrite"
            with pytest.raises(Exception, match="append-only"): session.commit()


@pytest.mark.parametrize("zone", ["../Europe/Prague", "Missing/Timezone", "", "x" * 101])
def test_policy_timezone_configuration_fails_closed(zone):
    from app.core.config import Settings
    with pytest.raises(ValueError): Settings(_env_file=None, zip_policy_timezone=zone)
