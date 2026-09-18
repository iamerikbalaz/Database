"""Audited ordinary resource edits with real authentication and synthetic data."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.models import Company, ImmutableAuditSnapshotError, ResourceChangeEvent
from app.resource_history import KINDS, append_resource_change, resource_snapshot
from test_application_access import access_case


def existing(case, kind):
    with case.database.session() as session:
        if kind == "USER": return case.users["OTHER"].id
        return session.scalar(select(KINDS[kind][0]).order_by(KINDS[kind][0].id)).id


def endpoint(kind, identifier): return f"/api/{KINDS[kind][2]}/{identifier}"


def creation(case, kind):
    with case.database.session() as session:
        company = session.scalar(select(Company)).id
    if kind == "BRAND": return {"company_id": str(company), "name": "Audited brand", "folder_prefix": "AUDIT", "brand_identifier": "audit"}
    if kind == "PROJECT": return {"company_id": str(company), "project_number": "AUDIT", "name": "Audited project"}
    if kind == "USER": return {"display_name": "Audited user", "email": "audited@example.invalid", "role": "PROCESSOR"}
    material = case.materials[0]
    return {"project_id": str(material.project_id), "published_brand_id": str(material.published_brand_id),
        "assigned_processor_id": str(case.users["PROCESSOR"].id), "material_name": "Audited material", "main_category_code": "G03"}


def update(kind):
    return {"BRAND": {"name": "Changed brand"}, "PROJECT": {"due_date": "2026-10-01", "notes": "Reviewed project note"},
        "USER": {"display_name": "Changed user"}, "MATERIAL": {"material_name": "Changed material"}}[kind]


@pytest.mark.parametrize("kind", KINDS)
def test_create_edit_and_noop_keep_exact_whitelisted_ordered_history(access_case, kind):
    case = access_case
    with case.client("ADMIN") as client:
        response = client.post(f"/api/{KINDS[kind][2]}", json=creation(case, kind))
        assert response.status_code == 201; identifier = response.json()["id"]; path = endpoint(kind, identifier)
        assert client.patch(path, json=update(kind)).status_code == 200
        assert client.patch(path, json=update(kind)).status_code == 200
        assert client.patch(path, json={}).status_code == 200
        response = client.get(path + "/history")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        data = response.json(); items = data["items"]
        assert data["resource_kind"] == kind and data["resource_id"] == identifier and data["next_cursor"] is None
        assert [item["version"] for item in items] == [2, 1] and [item["action"] for item in items] == ["UPDATED", "CREATED"]
        assert items[1]["before"] == {} and items[0]["before"] == items[1]["after"]
        assert set(items[0]["after"]) == {"id", *KINDS[kind][3]}
        assert all(item["actor_id"] == str(case.users["ADMIN"].id) and len(item["before_sha256"]) == len(item["after_sha256"]) == 64 for item in items)
        for field, value in update(kind).items(): assert items[0]["after"][field] == value
        assert not {"password", "password_hash", "credential", "token", "csrf_token", "next_sequence_number"} & set(items[0]["after"])


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("role", [None, "PROCESSOR", "LEADERSHIP", "PRODUCTION_LEAD"])
def test_ordinary_resource_history_is_admin_only(access_case, kind, role):
    case = access_case; identifier = existing(case, kind)
    with case.client(role) as client:
        assert client.get(endpoint(kind, identifier) + "/history").status_code == (401 if role is None else 403)


@pytest.mark.parametrize("kind", KINDS)
def test_legacy_target_has_no_invented_history_then_starts_with_first_edit(access_case, kind):
    case = access_case; identifier = existing(case, kind); path = endpoint(kind, identifier)
    with case.client("ADMIN") as client:
        assert client.get(path + "/history").json()["items"] == []
        assert client.patch(path, json=update(kind)).status_code == 200
        items = client.get(path + "/history").json()["items"]
        assert len(items) == 1 and items[0]["version"] == 1 and items[0]["action"] == "UPDATED"
        assert items[0]["before"]["id"] == str(identifier)


def test_assigned_processor_change_records_its_actor_without_granting_history_access(access_case):
    case = access_case; path = endpoint("MATERIAL", case.materials[0].id)
    with case.client("PROCESSOR") as processor, case.client("ADMIN") as admin:
        assert processor.patch(path, json={"material_name": "Processor edit"}).status_code == 200
        assert processor.get(path + "/history").status_code == 403
        items = admin.get(path + "/history").json()["items"]
        assert len(items) == 1 and items[0]["actor_id"] == str(case.users["PROCESSOR"].id)
        assert processor.patch(endpoint("MATERIAL", case.materials[1].id), json={"material_name": "Unauthorized edit"}).status_code == 404
        assert admin.get(endpoint("MATERIAL", case.materials[1].id) + "/history").json()["items"] == []


@pytest.mark.parametrize("kind", KINDS)
def test_audit_failure_rolls_back_domain_write_and_creates_no_success_history(access_case, monkeypatch, kind):
    from sqlalchemy.exc import IntegrityError
    from app.api import resources
    case = access_case; identifier = existing(case, kind)
    with case.database.session() as session: before = resource_snapshot(session.get(KINDS[kind][0], identifier))
    def reject(session, *args, **kwargs):
        session.flush()
        raise IntegrityError("Synthetic audit failure", {}, Exception("Synthetic failure"))
    monkeypatch.setattr(resources, "append_resource_change", reject)
    with case.client("ADMIN") as client:
        assert client.patch(endpoint(kind, identifier), json=update(kind)).status_code == 409
        assert client.post(f"/api/{KINDS[kind][2]}", json=creation(case, kind)).status_code == 409
        assert client.get(endpoint(kind, identifier) + "/history").json()["items"] == []
    with case.database.session() as session:
        assert resource_snapshot(session.get(KINDS[kind][0], identifier)) == before
        assert not list(session.scalars(select(ResourceChangeEvent)))


@pytest.mark.parametrize("kind", KINDS)
def test_history_pages_are_bounded_and_cannot_use_another_targets_cursor(access_case, kind):
    case = access_case; identifier = existing(case, kind); model, _, _, fields = KINDS[kind]
    field = next(value for value in ("name", "display_name", "material_name") if value in fields)
    for index in range(23):
        with case.database.session() as session:
            item = session.get(model, identifier); before = resource_snapshot(item); setattr(item, field, f"Synthetic change {index}")
            append_resource_change(session, item, case.users["ADMIN"].id, before); session.commit()
    with case.client("ADMIN") as client:
        path = endpoint(kind, identifier) + "/history"
        first = client.get(path).json(); assert len(first["items"]) == 20 and first["next_cursor"] == first["items"][-1]["id"]
        assert [item["version"] for item in first["items"]] == list(range(23, 3, -1))
        last = client.get(path, params={"after": first["next_cursor"]}).json()
        assert [item["version"] for item in last["items"]] == [3, 2, 1] and last["next_cursor"] is None
        assert client.get(path, params={"after": str(uuid4())}).status_code == 409
        other_kind = "BRAND" if kind != "BRAND" else "PROJECT"
        assert client.get(endpoint(other_kind, existing(case, other_kind)) + "/history", params={"after": first["next_cursor"]}).status_code == 409
        assert client.get(endpoint(kind, uuid4()) + "/history").status_code == 404


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_resource_evidence_is_immutable_in_the_orm(access_case, operation):
    case = access_case; path = endpoint("MATERIAL", case.materials[0].id)
    with case.client("ADMIN") as client:
        assert client.patch(path, json=update("MATERIAL")).status_code == 200
        identifier = UUID(client.get(path + "/history").json()["items"][0]["id"])
    with case.database.session() as session, pytest.raises(ImmutableAuditSnapshotError):
        event = session.get(ResourceChangeEvent, identifier)
        if operation == "delete": session.delete(event)
        else: event.action = "CREATED"
        session.commit()
