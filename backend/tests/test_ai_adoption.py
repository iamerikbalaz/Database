from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from app.db.models import MaterialContentRevision, MaterialAiDraft
from test_application_access import access_case
from test_ai_content import approve_source, proposal
from test_content_approvals import approval_payload
from test_catalog_content import content_payload


def adoption(draft, **updates):
    return {"idempotency_key": str(uuid4()), "expected_revision": draft["content_revision"], "expected_context_hash": draft["context_hash"],
        "description": draft["description"], "tags": draft["tags"], "reason": "Human reviewed AI proposal", **updates}


def prepare(client, material):
    path = f"/api/materials/{material.id}"
    approve_source(client, path)
    # PG concurrency cases share the catalog. Each preparation owns its values;
    # a duplicate name must remain a real conflict, never a tolerated fixture error.
    category = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "AI stone " + uuid4().hex})
    collection = client.post("/api/collections", json={"idempotency_key": str(uuid4()), "value": "AI series " + uuid4().hex, "brand_id": str(material.published_brand_id)})
    assert category.status_code == collection.status_code == 201
    assert client.post(path + "/content", json=content_payload(description="Synthetic reviewed content", credits=10, tags=["stone"],
        category_ids=[category.json()["id"]], collection_ids=[collection.json()["id"]])).status_code == 200
    context = client.get(path + "/publishing-context").json()
    result = client.post(path + "/content-drafts", json=proposal(context))
    assert result.status_code == 201
    draft = result.json()
    return path, draft, path + "/content-drafts/" + draft["id"] + "/adopt"


def test_adoption_preserves_other_fields_invalidates_approval_and_retains_provenance_after_manual_edit(access_case):
    case = access_case; material = case.materials[0]
    with case.client("ADMIN") as client:
        path, draft, target = prepare(client, material)
        before = client.get(path + "/content").json()
        review = client.get(path + "/content-review").json()
        assert client.post(path + "/content/approve", json=approval_payload(review)).status_code == 200
        # Approval itself does not change the generation used by AI context.
        payload = adoption(draft, description="Human edited proposed description")
        result = client.post(target, json=payload)
        assert result.status_code == 200
        adopted = result.json(); assert adopted["revision"] == 2 and adopted["content_status"] == "AI_DRAFT"
        assert adopted["description"] == payload["description"] and adopted["ai_provenance"]["draft_id"] == draft["id"]
        assert adopted["ai_provenance"]["edited"] is True
        assert all(adopted[field] == before[field] for field in ("credits", "categories", "collections"))
        assert client.get(path + "/content-review").json()["approval"] is None
        assert len(client.get(path + "/content-approvals").json()) == 1
        assert client.get(path + "/content").json() == adopted
        manual = content_payload(expected_revision=2, description="Later human revision", credits=before["credits"], tags=adopted["tags"],
            category_ids=[item["id"] for item in before["categories"]], collection_ids=[item["id"] for item in before["collections"]])
        edited = client.post(path + "/content", json=manual)
        assert edited.status_code == 200 and edited.json()["revision"] == 3
        assert edited.json()["content_status"] == "MANUAL_DRAFT" and edited.json()["ai_provenance"] == adopted["ai_provenance"]
        assert client.get(path + "/content").json() == edited.json()
        assert client.post(target, json=payload).json() == adopted
        assert client.get(path + "/content-history").json()[1]["snapshot"]["ai_provenance"]["draft_id"] == draft["id"]
        assert client.get(path + "/content-drafts").json()["items"][0]["description"] == draft["description"]


@pytest.mark.parametrize("role,expected", [("ADMIN", 200), ("PROCESSOR", 200), ("PRODUCTION_LEAD", 200), ("LEADERSHIP", 403), ("OTHER", 404), (None, 401)])
def test_only_material_editors_can_explicitly_adopt_proposals(access_case, role, expected):
    case = access_case
    with case.client("ADMIN") as client: path, draft, target = prepare(client, case.materials[0])
    with case.client(role) as client:
        assert client.post(target, json=adoption(draft)).status_code == expected
    if expected != 200:
        with case.client("ADMIN") as client: assert client.get(path + "/content").json()["revision"] == 1


def test_context_revision_ownership_and_extra_credits_cannot_be_bypassed(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        path, draft, target = prepare(client, case.materials[0])
        assert client.post(target, json=adoption(draft, expected_revision=0)).status_code == 409
        assert client.post(target, json=adoption(draft, expected_context_hash="f" * 64)).status_code == 409
        assert client.post(target, json=adoption(draft, credits=999)).status_code == 422
        assert client.post(f"/api/materials/{case.materials[1].id}/content-drafts/{draft['id']}/adopt", json=adoption(draft, expected_revision=0)).status_code == 404
        assert client.post(path + "/content", json=content_payload(expected_revision=1, description="Concurrent human edit")).status_code == 200
        assert client.post(target, json=adoption(draft, expected_revision=2)).status_code == 409


def test_exact_wording_adoption_creates_a_revision_for_provenance_and_still_needs_approval(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        path, draft, target = prepare(client, case.materials[0])
        result = client.post(target, json=adoption(draft))
        assert result.status_code == 200 and result.json()["ai_provenance"]["edited"] is False
        review = client.get(path + "/content-review").json()
        assert review["approval"] is None and review["can_approve"]
        assert client.post(path + "/content/approve", json=approval_payload(review)).status_code == 200
        current = client.get(path + "/content").json()
        assert current["content_status"] == "APPROVED" and current["ai_provenance"]["draft_id"] == draft["id"]


def test_failed_revision_insert_rolls_back_content_and_keeps_original_proposal(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        path, draft, target = prepare(client, case.materials[0]); before = client.get(path + "/content").json()
        def fail(_mapper, _connection, item):
            if item.snapshot.get("ai_provenance"): raise IntegrityError("synthetic failure", {}, None)
        event.listen(MaterialContentRevision, "before_insert", fail)
        try:
            assert client.post(target, json=adoption(draft)).status_code == 409
        finally: event.remove(MaterialContentRevision, "before_insert", fail)
        assert client.get(path + "/content").json() == before
    with case.database.session() as session:
        assert session.get(MaterialAiDraft, UUID(draft["id"])) is not None
        assert len(list(session.scalars(select(MaterialContentRevision).where(MaterialContentRevision.material_id == case.materials[0].id)))) == 1
