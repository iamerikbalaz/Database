"""Actual sessions, exact content decisions and permanent invalidation history."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.models import MaterialContentApproval, MaterialContentRevision, MaterialAuditEvent, PBRMaterial
from app.material_review import canonical_hash
from test_application_access import access_case
from test_catalog_content import create_vocabulary, content_payload
from test_material_review import review_case, scan


def prepare_content(client, path, brand_id, **changes):
    category, collection = create_vocabulary(client, brand_id)
    payload = content_payload(description="Synthetic reviewed content", credits=10, tags=["stone"],
        category_ids=[category["id"]], collection_ids=[collection["id"]])
    payload.update(changes)
    assert client.post(path + "/content", json=payload).status_code == 200
    return category, collection, payload


def approval_payload(review, **changes):
    return {"idempotency_key": str(uuid4()), "expected_revision": review["content_revision"],
        "expected_context_hash": review["context_hash"], **changes}


@pytest.mark.parametrize("role,code", [("ADMIN", 200), ("LEADERSHIP", 200), ("PRODUCTION_LEAD", 403), ("PROCESSOR", 403), ("OTHER", 403)])
def test_content_approval_roles_and_assignment(access_case, role, code):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as admin:
        prepare_content(admin, path, material.published_brand_id)
        review = admin.get(path + "/content-review").json()
    with case.client(role) as client:
        assert client.get(path + "/content-review").status_code == (404 if role == "OTHER" else 200)
        assert client.get(path + "/content-approvals").status_code == (404 if role == "OTHER" else 200)
        response = client.post(path + "/content/approve", json=approval_payload(review))
        assert response.status_code == code


def test_approval_is_idempotent_append_only_and_does_not_advance_source_generation(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as client:
        _, _, content = prepare_content(client, path, material.published_brand_id)
        before = client.get(path + "/review").json()
        review = client.get(path + "/content-review").json()
        assert review["context_hash"] == canonical_hash(review["snapshot"])
        assert review["can_approve"] and review["approval"] is None
        payload = approval_payload(review)
        result = client.post(path + "/content/approve", json=payload)
        assert result.status_code == 200
        approved = result.json()
        assert approved["content_status"] == "APPROVED" and not approved["can_approve"]
        assert approved["approval"]["actor_id"] == str(case.users["ADMIN"].id)
        assert client.get(path + "/content").json()["content_status"] == "APPROVED"
        assert client.get(path + "/review").json() == before
        assert client.post(path + "/content/approve", json=payload).json() == approved
        assert client.post(path + "/content/approve", json={**payload, "note": "Different"}).status_code == 409
        assert client.post(path + "/content/approve", json=approval_payload(review)).json()["detail"]["code"] == "CONTENT_ALREADY_APPROVED"
        noop = client.post(path + "/content", json={**content, "expected_revision": 1, "idempotency_key": str(uuid4())})
        assert noop.json()["content_status"] == "APPROVED"
        assert client.get(path + "/content-review").json() == approved
        assert len(client.get(path + "/content-history").json()) == 1
        history = client.get(path + "/content-approvals").json()
        assert len(history) == 1 and history[0]["snapshot"] == review["snapshot"]
        assert len([event for event in client.get(path + "/audit").json() if event["event_type"] == "CONTENT_APPROVED"]) == 1
    for field, value in (("note", "Changed"), ("snapshot", {})):
        with case.database.session() as session:
            record = session.get(MaterialContentApproval, UUID(approved["approval"]["id"]))
            setattr(record, field, value)
            with pytest.raises(Exception, match="append-only"): session.commit()
    with case.database.session() as session:
        session.delete(session.get(MaterialContentApproval, UUID(approved["approval"]["id"])))
        with pytest.raises(Exception, match="append-only"): session.commit()


@pytest.mark.parametrize("ack,note,code", [(False, None, 422), (True, None, 422), (False, "Reviewed", 422), (True, "Reviewed", 200)])
def test_empty_description_and_tags_require_explicit_ack_and_note(access_case, ack, note, code):
    material = access_case.materials[0]; path = f"/api/materials/{material.id}"
    with access_case.client("ADMIN") as client:
        prepare_content(client, path, material.published_brand_id, description=None, tags=[])
        review = client.get(path + "/content-review").json()
        assert review["errors"] == [] and review["warnings"] == ["CONTENT_DESCRIPTION_EMPTY", "CONTENT_TAGS_EMPTY"]
        assert client.post(path + "/content/approve", json=approval_payload(review, warnings_acknowledged=ack, note=note)).status_code == code


def test_required_content_findings_block_approval_without_creating_a_decision(access_case):
    material = access_case.materials[0]; path = f"/api/materials/{material.id}"
    with access_case.client("ADMIN") as client:
        empty = client.get(path + "/content-review").json()
        assert set(empty["errors"]) == {"CONTENT_DRAFT_REQUIRED", "CONTENT_CREDITS_REQUIRED", "CONTENT_CATEGORIES_REQUIRED"}
        assert client.post(path + "/content", json=content_payload(description="Incomplete draft")).status_code == 200
        review = client.get(path + "/content-review").json()
        blocked = client.post(path + "/content/approve", json=approval_payload(review))
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "CONTENT_APPROVAL_BLOCKED"
        assert client.get(path + "/content-approvals").json() == []


@pytest.mark.parametrize("change", ["content", "material", "brand", "category", "collection"])
def test_changes_reject_stale_review_invalidate_approval_and_preserve_history(access_case, change):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as client:
        category, collection, content = prepare_content(client, path, material.published_brand_id)
        review = client.get(path + "/content-review").json(); payload = approval_payload(review)
        approved = client.post(path + "/content/approve", json=payload).json()
        if change == "content":
            result = client.post(path + "/content", json={**content, "idempotency_key": str(uuid4()), "expected_revision": 1, "credits": 12})
        elif change == "material":
            result = client.patch(path, json={"material_name": "Changed synthetic material"})
        elif change == "brand":
            brand_path = f"/api/brands/{material.published_brand_id}"
            old = client.get(brand_path).json()["name"]
            assert client.patch(brand_path, json={"name": "Changed synthetic brand"}).status_code == 200
            result = client.patch(brand_path, json={"name": old})
        else:
            item = category if change == "category" else collection
            route = "/api/online-categories/" if change == "category" else "/api/collections/"
            assert client.patch(route + item["id"], json={"idempotency_key": str(uuid4()), "expected_version": 1,
                "is_active": False, "reason": "Synthetic retirement"}).status_code == 200
            inactive = client.get(path + "/content-review").json()
            assert "CONTENT_CATALOG_VALUE_INACTIVE" in inactive["errors"]
            result = client.patch(route + item["id"], json={"idempotency_key": str(uuid4()), "expected_version": 2,
                "is_active": True, "reason": "Synthetic reactivation"})
        assert result.status_code == 200
        current = client.get(path + "/content-review").json()
        assert current["context_hash"] != review["context_hash"]
        assert current["approval"] is None and current["content_status"] == "MANUAL_DRAFT"
        assert client.post(path + "/content/approve", json=approval_payload(review)).json()["detail"]["code"] == "CONTENT_CONTEXT_CHANGED"
        assert client.post(path + "/content/approve", json=payload).json() == approved  # replay, never a new current decision
        assert client.get(path + "/content-approvals").json()[0]["snapshot"] == review["snapshot"]
        assert client.post(path + "/content/approve", json=approval_payload(current)).status_code == 200
        assert len(client.get(path + "/content-approvals").json()) == 2


def test_same_source_scan_preserves_content_approval_changed_source_invalidates(review_case):
    case, worker, path = review_case; material = case.materials[0]
    with case.client("ADMIN") as client:
        prepare_content(client, path, material.published_brand_id)
        assert scan(client, path).status_code == 200
        review = client.get(path + "/content-review").json()
        assert client.post(path + "/content/approve", json=approval_payload(review)).status_code == 200
        assert scan(client, path).status_code == 200
        assert client.get(path + "/content-review").json()["content_status"] == "APPROVED"
        worker.version = 2
        assert scan(client, path).status_code == 200
        assert client.get(path + "/content-review").json()["approval"] is None
        assert len(client.get(path + "/content-approvals").json()) == 1


def test_brand_noop_preserves_approval_and_deactivation_blocks_new_approval(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as client:
        prepare_content(client, path, material.published_brand_id)
        review = client.get(path + "/content-review").json()
        assert client.post(path + "/content/approve", json=approval_payload(review)).status_code == 200
        brand_path = f"/api/brands/{material.published_brand_id}"
        assert client.patch(brand_path, json={"name": client.get(brand_path).json()["name"]}).status_code == 200
        assert client.get(path + "/content-review").json()["approval"] is not None
        assert client.patch(brand_path, json={"is_active": False}).status_code == 200
        review = client.get(path + "/content-review").json()
        assert review["approval"] is None and "CONTENT_BRAND_INACTIVE" in review["errors"]
        assert client.post(path + "/content/approve", json=approval_payload(review)).status_code == 409


def test_invalid_approval_does_not_echo_content_or_arbitrary_field_names(access_case):
    marker = "PRIVATE_SYNTHETIC_MARKER"
    with access_case.client("ADMIN") as client:
        response = client.post(f"/api/materials/{access_case.materials[0].id}/content/approve", json={marker: marker})
        assert response.status_code == 422 and marker not in response.text
