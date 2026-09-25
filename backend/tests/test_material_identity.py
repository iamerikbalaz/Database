"""Authenticated identity coordination with explicit durable worker outcomes."""
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import (InternalUser, MaterialAuditEvent, MaterialFileOperation, MaterialIdentityHistory,
                           MaterialNumberReservation, MaterialReviewState, PBRMaterial, PublishedBrand)
from app.identity_client import IdentityClientError, IdentityPlan, IdentityResult
from app.main import create_app
from app.material_review import canonical_hash
from test_application_access import access_case


def plan_payload(request, *, version=1, blocked=False, warning=False):
    old = request["folder_path"].rsplit("/", 1)[-1]; new = request["target_path"].rsplit("/", 1)[-1]
    result = {"schema_version": 1, "planner_version": "identity-plan-1", "source_path": request["folder_path"],
        "target_path": request["target_path"], "source_revision_hash": str(version) * 64,
        "changes": [] if old == new else [{"kind": "file", "source": f"4K/{old}_COL_4K.png", "target": f"4K/{new}_COL_4K.png", "sha256": "a" * 64}],
        "metadata": {"before_hash": None, "after_hash": None, "changed_fields": []},
        "errors": [{"code": "IDENTITY_TARGET_COLLISION", "path": request["target_path"]}] if blocked else [],
        "warnings": [{"code": "SOURCE_METADATA_MISSING", "path": "metadata.txt"}] if warning else [], "ready": not blocked}
    result["plan_hash"] = canonical_hash({"plan": result, "brand_name": request["brand_name"], "material_name": request["material_name"]})
    return result


class IdentityStub:
    def __init__(self):
        self.plans = []; self.executions = []; self.plan_callback = None; self.execute_callback = None
        self.version = 1; self.blocked = False; self.warning = False; self.failure = None; self.outcome = "COMPLETED"

    def plan(self, request):
        self.plans.append(request)
        result = IdentityPlan.model_validate_json(json.dumps(plan_payload(request, version=self.version, blocked=self.blocked, warning=self.warning)))
        if self.plan_callback: self.plan_callback()
        return result

    def execute(self, request):
        self.executions.append(request)
        if self.execute_callback: self.execute_callback()
        if self.failure: raise self.failure
        result = {"operation_id": request["operation_id"], "status": self.outcome, "plan_hash": request["expected_plan_hash"],
            "source_path": request["folder_path"], "target_path": request["target_path"],
            "source_revision_hash": None if self.outcome == "REJECTED" else str(self.version) * 64,
            "target_revision_hash": "f" * 64 if self.outcome == "COMPLETED" else None,
            "failure_code": None if self.outcome == "COMPLETED" else "IDENTITY_PREPARATION_REJECTED" if self.outcome == "REJECTED" else "IDENTITY_OPERATION_FAILED"}
        return IdentityResult.model_validate_json(json.dumps(result))


@pytest.fixture
def identity_case(access_case):
    case = access_case; worker = IdentityStub()
    settings = case.app.state.settings.model_copy(update={"source_mutations_enabled": True})
    case.app = create_app(settings, case.database, case.worker, identity_client=worker)
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.folder_path = "library/" + material.technical_identity
        brand = session.get(PublishedBrand, material.published_brand_id)
        target = PublishedBrand(company_id=brand.company_id, name="Target brand", folder_prefix="NEXT", brand_identifier="target")
        session.add(target); session.commit()
    return case, worker, f"/api/materials/{material.id}", {"target_brand_id": str(target.id), "main_category_code": "G04"}


def prepare(client, path, target):
    response = client.post(path + "/identity-plan", json=target)
    assert response.status_code == 200, response.json()
    plan = response.json()
    return {**target, "idempotency_key": str(uuid4()), "expected_generation": plan["generation"],
            "expected_proposal_hash": plan["proposal_hash"], "reason": "Correct synthetic classification", "warnings_acknowledged": True}


def test_read_only_plan_does_not_reserve_number_or_create_operation(identity_case):
    case, worker, path, target = identity_case
    with case.client("PRODUCTION_LEAD") as client:
        plan = client.post(path + "/identity-plan", json=target).json()
        assert plan["reserves_number"] and plan["target_context"]["technical_identity"] == "NEXT_0001_MATERIAL-1_G04"
        assert plan["generation"] == 0 and plan["worker_plan"]["ready"]
    with case.database.session() as session:
        assert session.get(PublishedBrand, UUID(target["target_brand_id"])).next_sequence_number == 1
        assert session.scalar(select(func.count()).select_from(MaterialFileOperation)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 0
        assert session.get(MaterialReviewState, case.materials[0].id) is None
    assert not worker.executions


@pytest.mark.parametrize("same_brand", [False, True])
def test_confirm_preserves_uuid_and_history_burns_only_rebrand_number(identity_case, same_brand):
    case, worker, path, target = identity_case
    if same_brand: target["target_brand_id"] = str(case.materials[0].published_brand_id)
    with case.client("PRODUCTION_LEAD") as client:
        payload = prepare(client, path, target)
        before = client.get(path).json()
        def inspect_before_source_write():
            with case.database.session() as session:
                operation = session.scalar(select(MaterialFileOperation))
                assert operation.status == "RUNNING"
                assert session.get(PBRMaterial, case.materials[0].id).technical_identity == before["technical_identity"]
                assert session.get(MaterialReviewState, case.materials[0].id).generation == 1
                brand = session.get(PublishedBrand, UUID(target["target_brand_id"]))
                assert brand.next_sequence_number == (3 if same_brand else 2)
        worker.execute_callback = inspect_before_source_write
        response = client.post(path + "/identity-confirm", json=payload)
        assert response.status_code == 200, response.json()
        assert response.json()["status"] == "COMPLETED"
        assert client.post(path + "/identity-confirm", json=payload).json() == response.json()
        after = client.get(path).json()
        for key in ("id", "project_id", "assigned_processor_id", "material_name"):
            assert after[key] == before[key]
        assert after["technical_identity"] == ("SAFE" if same_brand else "NEXT") + "_0001_MATERIAL-1_G04"
        assert len(client.get(path + "/identity-history").json()) == 1
        assert client.post(path + "/identity-confirm", json={**payload, "reason": "Different request"}).status_code == 409
    assert len(worker.executions) == 1
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialIdentityHistory)) == 1
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == (0 if same_brand else 1)


@pytest.mark.parametrize("outcome", ["ROLLED_BACK", "REJECTED", "RECOVERY_REQUIRED"])
def test_failed_source_operation_does_not_change_identity_or_recycle_number(identity_case, outcome):
    case, worker, path, target = identity_case; worker.outcome = outcome
    with case.client("ADMIN") as client:
        payload = prepare(client, path, target)
        result = client.post(path + "/identity-confirm", json=payload)
        assert result.status_code == 200 and result.json()["status"] == outcome
        assert client.get(path).json()["technical_identity"] == "SAFE_0001_G03"
        assert not client.get(path + "/identity-history").json()
        if outcome != "RECOVERY_REQUIRED":
            proposal = client.post(path + "/identity-plan", json=target)
            assert proposal.json()["target_context"]["sequence_number"] == 2
        else:
            assert client.patch(path, json={"material_name": "Not allowed"}).status_code == 409
    with case.database.session() as session:
        assert session.get(PublishedBrand, UUID(target["target_brand_id"])).next_sequence_number == 2
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 1


def test_unknown_worker_outcome_stays_owned_and_resumes_same_operation(identity_case):
    case, worker, path, target = identity_case
    worker.failure = IdentityClientError()
    with case.client("ADMIN") as client:
        payload = prepare(client, path, target)
        result = client.post(path + "/identity-confirm", json=payload).json()
        assert result["status"] == "RUNNING" and result["retry_code"] == "IDENTITY_UNAVAILABLE"
        assert client.post(path + "/identity-confirm", json=payload).json()["id"] == result["id"]
        assert len(worker.executions) == 1
        for suffix, body in [("/inventory/scan", {"expected_generation": 1, "idempotency_key": str(uuid4())}),
            ("/technical-review/run", {"expected_generation": 1, "idempotency_key": str(uuid4())}),
            ("/mark-done", None), ("/folder-preflight", {"folder_path": "library/SAFE_0001_G03"}),
            ("/folder-link", {"folder_path": "library/SAFE_0001_G03"}),
            ("/reopen", {"expected_generation": 1, "idempotency_key": str(uuid4()), "reason": "No"})]:
            response = client.post(path + suffix, json=body)
            assert response.status_code == 409, (suffix, response.json())
            assert response.json()["detail"]["code"] == "MATERIAL_OPERATION_ACTIVE"
        assert client.patch(path, json={"material_name": "No"}).status_code == 409
        for brand_id in (target["target_brand_id"], str(case.materials[0].published_brand_id)):
            assert client.patch("/api/brands/" + brand_id, json={"name": "No"}).status_code == 409
        assert client.get(path + "/review").status_code == 200
        assert client.get(path + "/identity-operations").json()["operations"][0]["id"] == result["id"]
        worker.failure = None
        resumed = client.post(path + "/identity-operations/" + result["id"] + "/resume")
        assert resumed.json()["status"] == "COMPLETED"
        assert [item["operation_id"] for item in worker.executions] == [result["id"], result["id"]]


@pytest.mark.parametrize("role", ["PROCESSOR", "OTHER", "LEADERSHIP"])
def test_only_lead_and_admin_can_plan_confirm_or_resume(identity_case, role):
    case, worker, path, target = identity_case
    with case.client("ADMIN") as client: payload = prepare(client, path, target)
    with case.client(role) as client:
        for suffix, body in [("/identity-plan", target), ("/identity-confirm", payload),
                             (f"/identity-operations/{uuid4()}/resume", None)]:
            assert client.post(path + suffix, json=body).status_code == 403
        assert client.get(path + "/identity-history").status_code == (404 if role == "OTHER" else 200)
    assert len(worker.plans) == 1 and not worker.executions


@pytest.mark.parametrize("change", ["source", "name", "brand", "counter", "generation", "disabled"])
def test_confirmation_rejects_stale_plan_or_revoked_authorization(identity_case, change):
    case, worker, path, target = identity_case
    with case.client("PRODUCTION_LEAD") as client:
        payload = prepare(client, path, target)
        if change == "source": worker.version = 2
        else:
            with case.database.session() as session:
                material = session.get(PBRMaterial, case.materials[0].id)
                if change == "name": material.material_name = "Changed"
                if change == "brand": session.get(PublishedBrand, UUID(target["target_brand_id"])).name = "Changed"
                if change == "counter": session.get(PublishedBrand, UUID(target["target_brand_id"])).next_sequence_number += 1
                if change == "generation": session.add(MaterialReviewState(material_id=material.id, generation=1))
                if change == "disabled": session.get(InternalUser, case.users["PRODUCTION_LEAD"].id).is_active = False
                session.commit()
        result = client.post(path + "/identity-confirm", json=payload)
        assert result.status_code == (401 if change == "disabled" else 409)
    assert not worker.executions


def test_authorized_operation_finalizes_even_if_initiator_is_disabled_during_io(identity_case):
    case, worker, path, target = identity_case
    def disable():
        with case.database.session() as session:
            session.get(InternalUser, case.users["PRODUCTION_LEAD"].id).is_active = False; session.commit()
    worker.execute_callback = disable
    with case.client("PRODUCTION_LEAD") as client:
        payload = prepare(client, path, target)
        result = client.post(path + "/identity-confirm", json=payload)
        assert result.status_code == 200 and result.json()["status"] == "COMPLETED"
        assert client.patch(path, json={"material_name": "No"}).status_code == 401
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.materials[0].id).technical_identity == "NEXT_0001_MATERIAL-1_G04"
        assert session.scalar(select(MaterialIdentityHistory)).actor_id == case.users["PRODUCTION_LEAD"].id


@pytest.mark.parametrize("field,value,code", [("is_published", True, "PUBLISHED_IDENTITY_BLOCKED"),
    ("workflow_status", "DONE", "IDENTITY_REOPEN_REQUIRED"), ("folder_path", None, "IDENTITY_FOLDER_REQUIRED")])
def test_unsupported_material_states_fail_before_worker(identity_case, field, value, code):
    case, worker, path, target = identity_case
    with case.database.session() as session:
        setattr(session.get(PBRMaterial, case.materials[0].id), field, value); session.commit()
    with case.client("ADMIN") as client:
        response = client.post(path + "/identity-plan", json=target)
        assert response.status_code == 409 and response.json()["detail"]["code"] == code
    assert not worker.plans


@pytest.mark.parametrize("finding", ["warning", "blocked"])
def test_blocking_findings_and_unacknowledged_warning_prevent_execution(identity_case, finding):
    case, worker, path, target = identity_case; setattr(worker, finding, True)
    with case.client("ADMIN") as client:
        payload = prepare(client, path, target); payload["warnings_acknowledged"] = False
        assert client.post(path + "/identity-confirm", json=payload).status_code == 409
    assert not worker.executions
