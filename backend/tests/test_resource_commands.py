"""Real sessions, atomic ordinary writes and explicit actor-bound receipts."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import (Company, CompanyChangeEvent, InternalUser, MaterialNumberReservation,
    PBRMaterial, Project, PublishedBrand, ResourceChangeEvent, ResourceCommand)
from test_application_access import access_case  # noqa: F401

KINDS = (("COMPANY", "companies"), ("BRAND", "brands"), ("PROJECT", "projects"), ("USER", "internal-users"), ("MATERIAL", "materials"))


def creation_payload(database, material, kind):
    with database.session() as session:
        company_id = str(session.get(Project, material.project_id).company_id)
    marker = uuid4().hex
    return {
        "COMPANY": {"name": "Synthetic command company"},
        "BRAND": {"company_id": company_id, "name": "Synthetic command brand", "folder_prefix": "RC" + marker[:10], "brand_identifier": marker},
        "PROJECT": {"company_id": company_id, "name": "Synthetic command project", "project_number": marker},
        "USER": {"display_name": "Synthetic command user", "email": marker + "@example.invalid", "role": "PROCESSOR"},
        "MATERIAL": {"project_id": str(material.project_id), "published_brand_id": str(material.published_brand_id),
            "assigned_processor_id": str(material.assigned_processor_id), "material_name": "Synthetic command material", "main_category_code": "G03"},
    }[kind]


def send(client, method, path, payload, key):
    return client.request(method, path, json=payload, headers={"Idempotency-Key": str(key)})


def test_receipt_binds_raw_submitted_json_before_validation_normalization(access_case):
    key = uuid4()
    payload = {"name": "  Český kámen 🪨  ", "website": "https://example.invalid", "is_active": True, "country": None}
    with access_case.client("ADMIN") as client:
        first = send(client, "POST", "/api/companies", payload, key)
        assert first.status_code == 201
        receipt = client.get(f"/api/resource-commands/{key}")
        assert receipt.status_code == 200
        assert receipt.json()["request_hash"] == "e07f34e14ab4bc4ebdd71095a460dc2fa38dc56310d5d2d899038c16915a8962"
        reordered = dict(reversed(list(payload.items())))
        assert send(client, "POST", "/api/companies", reordered, key).json() == first.json()
        for changed in (
            {**payload, "name": payload["name"].strip()},
            {**payload, "website": payload["website"] + "/"},
            {field: value for field, value in payload.items() if field != "is_active"},
        ):
            conflict = send(client, "POST", "/api/companies", changed, key)
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "RESOURCE_COMMAND_KEY_REUSED"


@pytest.mark.parametrize("kind,segment", KINDS)
def test_create_update_replay_and_read_recovery_return_original_receipts(access_case, kind, segment):
    case = access_case; create_key, update_key = uuid4(), uuid4()
    payload = creation_payload(case.database, case.materials[0], kind)
    field = "display_name" if kind == "USER" else "material_name" if kind == "MATERIAL" else "name"
    with case.client("ADMIN") as client:
        base = "/api/" + segment
        first = send(client, "POST", base, payload, create_key)
        assert first.status_code == 201
        assert first.headers["Idempotency-Replayed"] == "false"
        original = first.json(); path = base + "/" + original["id"]
        repeat = send(client, "POST", base, payload, create_key)
        assert repeat.status_code == 201 and repeat.json() == original
        assert repeat.headers["Idempotency-Replayed"] == "true"
        updated = send(client, "PATCH", path, {field: "Reviewed change"}, update_key)
        assert updated.status_code == 200
        assert client.patch(path, json={field: "Later change"}).status_code == 200
        repeat = send(client, "PATCH", path, {field: "Reviewed change"}, update_key)
        assert repeat.status_code == 200 and repeat.json() == updated.json()
        assert send(client, "POST", base, payload, create_key).json() == original
        assert client.get(path).json()[field] == "Later change"
        for key, saved in ((create_key, original), (update_key, updated.json())):
            receipt = client.get(f"/api/resource-commands/{key}")
            assert receipt.status_code == 200
            assert receipt.json()["response"] == saved
            assert receipt.json()["resource_id"] == original["id"]
            assert receipt.json()["kind"] == kind
        assert send(client, "PATCH", path, {field: "Different"}, update_key).status_code == 409
        assert send(client, "POST", base, {**payload, field: "Different"}, create_key).status_code == 409
        assert send(client, "PATCH", path, {field: "Reviewed change"}, create_key).status_code == 409
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ResourceCommand)) == 2
        model = CompanyChangeEvent if kind == "COMPANY" else ResourceChangeEvent
        assert session.scalar(select(func.count()).select_from(model).where(getattr(model, {
            "COMPANY": "company_id", "BRAND": "brand_id", "PROJECT": "project_id", "USER": "user_id", "MATERIAL": "material_id"}[kind]) == UUID(original["id"]))) == 3


def test_material_retry_does_not_allocate_again_and_actor_keys_are_independent(access_case):
    case = access_case; key = uuid4(); material = case.materials[0]
    payload = creation_payload(case.database, material, "MATERIAL")
    with case.client("ADMIN") as admin, case.client("PRODUCTION_LEAD") as lead:
        first = send(admin, "POST", "/api/materials", payload, key)
        assert first.status_code == 201
        assert lead.get(f"/api/resource-commands/{key}").status_code == 404
        second = send(lead, "POST", "/api/materials", payload, key)
        assert second.status_code == 201 and second.json()["id"] != first.json()["id"]
        for client, response in ((admin, first), (lead, second)):
            assert send(client, "POST", "/api/materials", payload, key).json() == response.json()
    with case.database.session() as session:
        assert session.get(PublishedBrand, material.published_brand_id).next_sequence_number == 5
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 2


@pytest.mark.parametrize("status", ["assignment", "archive", "role"])
def test_recovery_and_replay_recheck_current_material_authority(access_case, status):
    from test_material_archives import prepare
    case = access_case; material = case.materials[0]; key = uuid4()
    path = f"/api/materials/{material.id}"; payload = {"material_name": "Processor change"}
    with case.client("PROCESSOR") as processor, case.client("ADMIN") as admin:
        assert send(processor, "PATCH", path, payload, key).status_code == 200
        if status == "assignment":
            assert admin.patch(path, json={"assigned_processor_id": str(case.users["OTHER"].id)}).status_code == 200
        elif status == "archive":
            assert admin.post(f"/api/material-archives/{material.id}/commands", json=prepare(admin, material.id)).status_code == 200
        else:
            with case.database.session() as session:
                session.get(InternalUser, case.users["PROCESSOR"].id).role = "LEADERSHIP"; session.commit()
        expected = 403 if status == "role" else 404
        assert processor.get(f"/api/resource-commands/{key}").status_code == expected
        assert send(processor, "PATCH", path, payload, key).status_code == expected


def test_receipt_cannot_restore_a_previous_privilege(access_case):
    case = access_case; material = case.materials[0]; key = uuid4()
    with case.client("ADMIN") as client:
        assert send(client, "PATCH", f"/api/materials/{material.id}", {"assigned_processor_id": str(case.users["ADMIN"].id)}, key).status_code == 409
        assert send(client, "POST", "/api/companies", {"name": "Synthetic"}, key).status_code == 201
        with case.database.session() as session:
            session.get(InternalUser, case.users["ADMIN"].id).role = "PROCESSOR"; session.commit()
        assert client.get(f"/api/resource-commands/{key}").status_code == 403


def test_command_failure_rolls_back_record_number_and_audit(access_case):
    case = access_case; material = case.materials[0]; key = uuid4()
    def reject(*args): raise IntegrityError("synthetic", {}, Exception("synthetic"))
    event.listen(ResourceCommand, "before_insert", reject)
    try:
        with case.client("ADMIN") as client:
            response = send(client, "POST", "/api/materials", creation_payload(case.database, material, "MATERIAL"), key)
            assert response.status_code == 409
            assert client.get(f"/api/resource-commands/{key}").status_code == 404
    finally: event.remove(ResourceCommand, "before_insert", reject)
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 2
        assert session.scalar(select(func.count()).select_from(ResourceChangeEvent)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialNumberReservation)) == 0
        assert session.get(PublishedBrand, material.published_brand_id).next_sequence_number == 3


@pytest.mark.parametrize("key", ["invalid", "00000000-0000-0000-0000-000000000000"])
def test_invalid_keys_fail_before_creating_a_record(access_case, key):
    case = access_case
    with case.client("ADMIN") as client:
        assert send(client, "POST", "/api/companies", {"name": "Synthetic"}, key).status_code == 422
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == 1
        assert session.scalar(select(func.count()).select_from(ResourceCommand)) == 0


def test_missing_keys_preserve_documented_legacy_semantics(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        first = client.post("/api/companies", json={"name": "Synthetic legacy"})
        second = client.post("/api/companies", json={"name": "Synthetic legacy"})
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] != second.json()["id"]
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ResourceCommand)) == 0


@pytest.mark.parametrize("content,content_type", [("not-json", "text/plain"), ("[]", "application/json"), ("null", "application/json")])
def test_invalid_request_bodies_keep_a_bounded_validation_response(access_case, content, content_type):
    with access_case.client("ADMIN") as client:
        response = client.post("/api/companies", content=content, headers={"Content-Type": content_type, "Idempotency-Key": str(uuid4())})
        assert response.status_code == 422
