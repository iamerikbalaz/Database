"""Bulk library properties preserve independent data and command receipts."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from test_application_access import access_case  # noqa: F401
from test_catalog_content import create_vocabulary


def payload(client, material_id, **change):
    path = f"/api/materials/{material_id}"
    return {"idempotency_key": str(uuid4()),
        "expected_updated_at": client.get(path).json()["updated_at"],
        "expected_revision": client.get(path + "/content").json()["revision"], **change}


def test_append_tags_and_credits_preserve_all_other_content_and_replay(access_case):
    case = access_case; material_id = case.materials[0].id; path = f"/api/materials/{material_id}"
    with case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, case.materials[0].published_brand_id)
        initial = client.post(path + "/content", json={"idempotency_key": str(uuid4()), "expected_revision": 0,
            "description": "Keep description", "credits": 8, "tags": ["matte", "stone"],
            "category_ids": [category["id"]], "collection_ids": [collection["id"]]}).json()
        tags = payload(client, material_id, field="tags", tags=["MATTE", "rough"])
        response = client.post(path + "/library-field", json=tags)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["tags"] == ["matte", "rough", "stone"]
        for field in ("description", "credits", "categories", "collections"):
            assert body[field] == initial[field]
        credits = payload(client, material_id, field="credits", credits=0)
        saved = client.post(path + "/library-field", json=credits)
        assert saved.status_code == 200 and saved.json()["credits"] == 0
        assert saved.json()["tags"] == body["tags"]
        assert client.post(path + "/library-field", json=tags).json() == body
        assert client.post(path + "/library-field", json={**tags, "tags": ["changed"]}).status_code == 409
        history = client.get(path + "/content-history").json()
        assert len(history) == 3


@pytest.mark.parametrize("change", ["archived", "deleted"])
def test_exact_receipt_survives_archive_or_delete_but_new_writes_stay_denied(access_case, change):
    from sqlalchemy import func, select
    from app.db.models import MaterialAuditEvent, MaterialContentRevision, PBRMaterial
    case = access_case; material_id = case.materials[0].id; path = f"/api/materials/{material_id}/library-field"
    with case.client("ADMIN") as client:
        command = payload(client, material_id, field="credits", credits=7)
        first = client.post(path, json=command)
        assert first.status_code == 200, first.text
        if change == "archived":
            from test_material_archives import apply as archive, prepare as prepare_archive
            response = archive(client, material_id, prepare_archive(client, material_id))
            assert response.status_code == 200, response.text
        else:
            with case.database.session() as session:
                session.get(PBRMaterial, material_id).deleted_at = datetime.now(UTC)
                session.commit()
        with case.database.session() as session:
            baseline = tuple(session.scalar(select(func.count()).select_from(model)) for model in (MaterialAuditEvent, MaterialContentRevision))
        replay = client.post(path, json=command)
        assert replay.status_code == 200 and replay.json() == first.json()
        assert client.post(path, json={**command, "idempotency_key": str(uuid4())}).status_code == 404
        assert client.post(path, json={**command, "credits": 9}).status_code == 409
        with case.database.session() as session:
            after = tuple(session.scalar(select(func.count()).select_from(model)) for model in (MaterialAuditEvent, MaterialContentRevision))
        assert after == baseline


def test_rejects_stale_material_even_when_content_revision_is_current(access_case):
    case = access_case; material_id = case.materials[0].id; path = f"/api/materials/{material_id}"
    with case.client("ADMIN") as client:
        change = payload(client, material_id, field="credits", credits=5)
        with case.database.session() as session:
            from app.db.models import PBRMaterial
            row = session.get(PBRMaterial, material_id)
            row.updated_at += timedelta(seconds=1); session.commit()
        response = client.post(path + "/library-field", json=change)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "MATERIAL_CHANGED"
        assert client.get(path + "/content").json()["revision"] == 0


def test_rejects_stale_content_and_tag_overflow_without_partial_changes(access_case):
    material_id = access_case.materials[0].id; path = f"/api/materials/{material_id}"
    with access_case.client("ADMIN") as client:
        assert client.post(path + "/content", json={"idempotency_key": str(uuid4()), "expected_revision": 0,
            "description": "Preserved", "tags": [f"tag {index}" for index in range(100)]}).status_code == 200
        change = payload(client, material_id, field="credits", credits=9)
        response = client.post(path + "/library-field", json={**change, "expected_revision": 0})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "CONTENT_REVISION_CHANGED"
        response = client.post(path + "/library-field", json=payload(client, material_id, field="tags", tags=["extra"]))
        assert response.status_code == 422 and response.json()["detail"]["code"] == "TOO_MANY_TAGS"
        content = client.get(path + "/content").json()
        assert content["revision"] == 1 and content["credits"] is None and len(content["tags"]) == 100


@pytest.mark.parametrize("updates", [
    {"field": "credits", "credits": True}, {"field": "credits", "credits": -1},
    {"field": "credits", "credits": 1.2}, {"field": "tags", "tags": ["a:b"]},
    {"field": "tags", "tags": []}, {"field": "tags", "tags": ["x"] * 101},
    {"field": "tags", "tags": ["matte"], "description": "overwrite"},
])
def test_invalid_values_do_not_write(access_case, updates):
    material_id = access_case.materials[0].id; path = f"/api/materials/{material_id}"
    with access_case.client("ADMIN") as client:
        response = client.post(path + "/library-field", json=payload(client, material_id, **updates))
        assert response.status_code == 422
        assert client.get(path + "/content").json()["revision"] == 0


@pytest.mark.parametrize("role, status", [("ADMIN", 200), ("PRODUCTION_LEAD", 200), ("PROCESSOR", 200), ("OTHER", 404), ("LEADERSHIP", 403)])
def test_roles_assignments_and_csrf(access_case, role, status):
    material_id = access_case.materials[0].id; path = f"/api/materials/{material_id}"
    with access_case.client("ADMIN") as admin:
        change = payload(admin, material_id, field="credits", credits=4)
    with access_case.client(role) as client:
        assert client.post(path + "/library-field", json=change).status_code == status
        client.headers.pop("X-CSRF-Token", None)
        assert client.post(path + "/library-field", json={**change, "idempotency_key": str(uuid4())}).status_code == 403
