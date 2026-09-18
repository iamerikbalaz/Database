"""Public history windows retain authorization and traverse timestamp ties."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.db.models import (CatalogAuditEvent, InternalUser, MaterialAuditEvent,
    MaterialContentApproval, MaterialContentRevision, MaterialFileOperation, MaterialIdentityHistory)
from app.material_identity import identity_context
from test_application_access import access_case  # noqa: F401

ROUTES = ("audit", "identity-operations", "identity-history", "content-history", "content-approvals", "catalog-audit")
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def seed_history(database, material, actor, route, count=103, *, stamp=STAMP, start=1):
    """Synthetic records only; use the actual domain context and valid foreign keys."""
    rows = []
    context = identity_context(material)
    with database.session() as session:
        for index in range(start, start + count):
            common = dict(id=uuid4(), actor_id=actor.id, created_at=stamp)
            scoped = dict(**common, material_id=material.id)
            if route == "audit":
                row = MaterialAuditEvent(**scoped, event_type="REOPENED", generation=index, result={"audit": {"reason": "Synthetic history"}})
            elif route == "identity-history":
                row = MaterialIdentityHistory(**scoped, old_context=context, new_context=context, reason="Synthetic history")
            elif route == "identity-operations":
                row = MaterialFileOperation(**scoped, source_brand_id=material.published_brand_id,
                    target_brand_id=material.published_brand_id, request_key=uuid4(), request_hash="a" * 64,
                    proposal_hash="b" * 64, request_payload={"reason": "Synthetic history"}, source_context=context,
                    target_context=context, worker_plan={}, status="REJECTED", result=None)
            elif route == "catalog-audit":
                row = CatalogAuditEvent(**common, resource_id=uuid4(), resource_kind="CATEGORY",
                    request_key=uuid4(), request_hash="a" * 64, result={"audit": {"reason": "Synthetic history"}})
            else:
                revision = MaterialContentRevision(**scoped, revision=index, snapshot={}, snapshot_hash="a" * 64, reason="Synthetic history")
                session.add(revision)
                session.flush()
                row = revision if route == "content-history" else MaterialContentApproval(
                    **{**scoped, "id": uuid4()}, content_revision=index, context_hash=f"{index:064x}",
                    snapshot={}, note="Synthetic history", warnings_acknowledged=True)
            session.add(row)
            rows.append((index if route == "content-history" else stamp, row.id))
        session.commit()
    return [str(row_id) for _, row_id in sorted(rows, reverse=True)]


def path(material, route):
    return "/api/catalog-audit" if route == "catalog-audit" else f"/api/materials/{material.id}/{route}"


def items(response, route):
    assert response.status_code == 200
    data = response.json()
    if route == "identity-operations":
        assert set(data) == {"mutations_enabled", "operations"}
        assert isinstance(data["mutations_enabled"], bool)
        return data["operations"]
    assert isinstance(data, list)
    return data


@pytest.mark.parametrize("route", ROUTES)
def test_all_legacy_windows_reach_older_records_without_gaps_or_duplicates(access_case, route):
    case = access_case; material = case.materials[0]
    expected = seed_history(case.database, material, case.users["ADMIN"], route)
    url = path(material, route)
    with case.client("ADMIN") as client:
        first = items(client.get(url), route)
        assert [row["id"] for row in first] == expected[:100]
        # A later insert must not shift the boundary or duplicate older rows.
        newer = seed_history(case.database, material, case.users["ADMIN"], route,
            count=1, stamp=STAMP + timedelta(days=1), start=104)
        second = items(client.get(url, params={"after": first[-1]["id"]}), route)
        assert [row["id"] for row in second] == expected[100:]
        assert items(client.get(url, params={"after": second[-1]["id"]}), route) == []
        assert items(client.get(url, params={"limit": 1}), route)[0]["id"] == newer[0]
        for limit in (0, -1, 101, "1.5"):
            assert client.get(url, params={"limit": limit}).status_code == 422
        assert client.get(url, params={"after": "invalid"}).status_code == 422
        unknown = client.get(url, params={"after": str(uuid4())})
        assert unknown.status_code == 409
        assert unknown.json()["detail"] == {"code": "HISTORY_CURSOR_INVALID"}


@pytest.mark.parametrize("route", ROUTES[:-1])
def test_cursor_is_scoped_and_current_assignment_is_checked_first(access_case, route):
    case = access_case; material, other = case.materials
    foreign = seed_history(case.database, other, case.users["ADMIN"], route, count=1)[0]
    own = seed_history(case.database, material, case.users["ADMIN"], route, count=1)[0]
    with case.client("ADMIN") as admin, case.client("PROCESSOR") as processor, case.client("OTHER") as stranger:
        url = path(material, route)
        assert admin.get(url, params={"after": foreign}).status_code == 409
        assert items(processor.get(url), route)[0]["id"] == own
        for cursor in (own, foreign, str(uuid4())):
            assert stranger.get(url, params={"after": cursor}).status_code == 404
        response = admin.patch(f"/api/materials/{material.id}", json={"assigned_processor_id": str(case.users["OTHER"].id)})
        assert response.status_code == 200
        assert processor.get(url, params={"after": own}).status_code == 404


def test_catalog_cursor_does_not_bypass_current_role(access_case):
    case = access_case
    cursor = seed_history(case.database, case.materials[0], case.users["ADMIN"], "catalog-audit", count=1)[0]
    with case.client("ADMIN") as client:
        assert client.get("/api/catalog-audit", params={"after": cursor}).status_code == 200
        with case.database.session() as session:
            session.get(InternalUser, case.users["ADMIN"].id).role = "PROCESSOR"
            session.commit()
        for value in (cursor, str(uuid4())):
            assert client.get("/api/catalog-audit", params={"after": value}).status_code == 403


@pytest.mark.parametrize("route", ROUTES[:-1])
def test_archived_material_history_cannot_be_reopened_by_a_cursor(access_case, route):
    from test_material_archives import attach, prepare
    case = access_case; attach(case)
    material = case.materials[0]
    cursor = seed_history(case.database, material, case.users["ADMIN"], route, count=1)[0]
    with case.client("ADMIN") as client:
        response = client.post(f"/api/material-archives/{material.id}/commands", json=prepare(client, material.id, "ARCHIVE"))
        assert response.status_code == 200
        assert client.get(path(material, route), params={"after": cursor}).status_code == 404
