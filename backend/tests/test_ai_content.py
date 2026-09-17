from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.ai_content import AiDraftCreate, source_url
from app.db.models import MaterialAiDraft, MaterialSourceLink, MaterialContent, MaterialReviewState
from test_application_access import access_case
from test_catalog_content import content_payload, create_vocabulary


def proposal(context, **updates):
    return {"idempotency_key": str(uuid4()), "expected_context_hash": context["context_hash"],
        "provider": "Synthetic external tool", "model": "fixture-v1", "prompt_version": "pbr-1",
        "description": "Synthetic stone surface.", "tags": ["stone"], "source_link_ids": [],
        "reason": "Review synthetic proposal", **updates}


def approve_source(client, path, url="https://catalog.example/material/stone"):
    response = client.post(path + "/content-sources", json={"idempotency_key": str(uuid4()), "url": url, "reason": "Approve synthetic source"})
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("value", ["http://catalog.example/x", "https://a:b@catalog.example/x", "https://catalog.example/x?token=PRIVATE", "https://catalog.example/#PRIVATE",
    "https://127.0.0.1/x", "https://[::1]/x", "https://local.internal/x", "https://localhost/x", "https://x.local/x", "https://catalog.example:444/x",
    "https://catalog.example/x%0aprivate", "https://catalog.example/x\\private", "https://catalog.example/x%zz", "https://catalog.example/?", "https://catalog.example/#",
    "https://catalog.example/" + "x" * 2048, "https://catalog.example/a\u200bb", "https://catalog.example/%7F", "https://catalog.example/a b"])
def test_source_url_rejects_credentials_controls_non_https_and_private_literals(value):
    with pytest.raises(ValueError): source_url(value)


def test_source_url_preserves_path_and_canonicalizes_host_without_network():
    assert source_url("https://CATALOG.example:443/Stone") == "https://catalog.example/Stone"
    assert source_url("https://catalog.example") == "https://catalog.example/"
    assert source_url("https://catalog.example/café") == "https://catalog.example/caf%C3%A9"


@pytest.mark.parametrize("role,read,write", [(None, 401, 401), ("OTHER", 404, 404), ("PROCESSOR", 200, 201), ("PRODUCTION_LEAD", 200, 201), ("LEADERSHIP", 200, 403), ("ADMIN", 200, 201)])
def test_context_and_proposal_use_actual_material_access(access_case, role, read, write):
    case = access_case; path = f"/api/materials/{case.materials[0].id}"
    with case.client("ADMIN") as admin: current = admin.get(path + "/publishing-context").json()
    with case.client(role) as client:
        result = client.get(path + "/publishing-context"); assert result.status_code == read
        assert client.post(path + "/content-drafts", json=proposal(current)).status_code == write
        assert client.get(path + "/content-drafts").status_code == read
        source = client.post(path + "/content-sources", json={"idempotency_key": str(uuid4()), "url": "https://catalog.example/stone", "reason": "Synthetic approval"})
        assert source.status_code == (201 if role == "ADMIN" else 401 if role is None else 403)


def test_context_contains_only_minimal_selected_data_and_explicit_urls(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as client:
        category, collection = create_vocabulary(client, material.published_brand_id)
        assert client.post(path + "/content", json=content_payload(description="PRIVATE_SAVED_DRAFT", tags=["private draft tag"], credits=8,
            category_ids=[category["id"]], collection_ids=[collection["id"]])).status_code == 200
        source = approve_source(client, path)
        response = client.get(path + "/publishing-context"); view = response.json()
        assert set(view) == {"context", "context_hash", "content_revision"}
        assert set(view["context"]) == {"material_id", "name", "brand", "categories", "collections", "source_urls"}
        assert view["context"]["source_urls"] == [{"id": source["id"], "url": source["url"]}]
        assert view["context"]["categories"] == [{"id": category["id"], "value": category["value"]}]
        assert all(value not in response.text for value in ("PRIVATE_SAVED_DRAFT", "private draft tag", "folder_path", "metadata", "email", "assigned_processor_id", "project_id", material.technical_identity))


def test_draft_is_immutable_provenance_and_never_replaces_or_approves_saved_content(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as client:
        source = approve_source(client, path)
        before = client.get(path + "/content").json(); review = client.get(path + "/review").json()
        context = client.get(path + "/publishing-context").json()
        data = proposal(context, source_link_ids=[source["id"]], tags=["stone", "STONE", "  Natural   stone  "])
        response = client.post(path + "/content-drafts", json=data)
        assert response.status_code == 201
        draft = response.json(); assert draft["status"] == "AI_DRAFT" and draft["tags"] == ["Natural stone", "stone"]
        assert draft["context"] == context["context"] and draft["actor_id"] == str(case.users["ADMIN"].id)
        assert client.post(path + "/content-drafts", json=data).json() == draft
        assert client.post(path + "/content-drafts", json={**data, "description": "Other proposal"}).status_code == 409
        assert client.get(path + "/content").json() == before and client.get(path + "/review").json() == review
        assert client.get(path + "/content-drafts").json()["items"][0]["context_is_current"] is True
        assert client.get(path + "/content-approvals").json() == []
    with case.database.session() as session:
        stored = session.get(MaterialAiDraft, UUID(draft["id"])); stored.description = "Changed"
        with pytest.raises(Exception, match="append-only"): session.commit()


@pytest.mark.parametrize("change", ["content", "source", "brand"])
def test_changed_context_rejects_new_proposal_but_identical_retry_preserves_original(access_case, change):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"
    with case.client("ADMIN") as client:
        source = approve_source(client, path); context = client.get(path + "/publishing-context").json(); data = proposal(context)
        saved = client.post(path + "/content-drafts", json=data).json()
        if change == "content": assert client.post(path + "/content", json=content_payload(description="Human edit")).status_code == 200
        elif change == "brand": assert client.patch(f"/api/brands/{material.published_brand_id}", json={"name": "Updated brand"}).status_code == 200
        else: assert client.patch(path + "/content-sources/" + source["id"], json={"idempotency_key": str(uuid4()), "expected_version": 1, "is_active": False, "reason": "Retire source"}).status_code == 200
        assert client.post(path + "/content-drafts", json={**data, "idempotency_key": str(uuid4())}).status_code == 409
        assert client.post(path + "/content-drafts", json=data).json() == saved
        assert client.get(path + "/content-drafts").json()["items"][0]["context_is_current"] is False


def test_unapproved_source_and_invalid_proposal_are_rejected_without_reflection(access_case):
    case = access_case; path = f"/api/materials/{case.materials[0].id}"
    with case.client("ADMIN") as client:
        context = client.get(path + "/publishing-context").json()
        assert client.post(path + "/content-drafts", json=proposal(context, source_link_ids=[str(uuid4())])).status_code == 422
        for update in ({"description": "PRIVATE\u200bVALUE"}, {"tags": ["PRIVATE:VALUE"]}, {"provider": {"PRIVATE": "VALUE"}}, {"PRIVATE": "VALUE"}, {"description": None, "tags": []}):
            result = client.post(path + "/content-drafts", json=proposal(context, **update))
            assert result.status_code == 422 and "PRIVATE" not in result.text
    with case.database.session() as session: assert list(session.scalars(select(MaterialAiDraft))) == []


def test_source_approval_is_audited_idempotent_bounded_and_retirable(access_case):
    case = access_case; path = f"/api/materials/{case.materials[0].id}"
    with case.client("ADMIN") as client:
        data = {"idempotency_key": str(uuid4()), "url": "https://catalog.example/stone", "reason": "Approved source"}
        source = client.post(path + "/content-sources", json=data).json()
        assert client.post(path + "/content-sources", json=data).json() == source
        assert client.post(path + "/content-sources", json={**data, "idempotency_key": str(uuid4())}).status_code == 409
        for index in range(19): approve_source(client, path, f"https://catalog.example/{index}")
        result = client.post(path + "/content-sources", json={**data, "idempotency_key": str(uuid4()), "url": "https://catalog.example/overflow"})
        assert result.status_code == 409
        activity = {"idempotency_key": str(uuid4()), "expected_version": 1, "is_active": False, "reason": "Retire source"}
        changed = client.patch(path + "/content-sources/" + source["id"], json=activity)
        assert changed.status_code == 200 and changed.json()["version"] == 2
        assert client.patch(path + "/content-sources/" + source["id"], json=activity).json() == changed.json()
        assert client.patch(path + "/content-sources/" + source["id"], json={**activity, "idempotency_key": str(uuid4())}).status_code == 409
        assert len(client.get(path + "/content-sources").json()) == 20
        assert len(client.get(path + "/publishing-context").json()["context"]["source_urls"]) == 19
        activity_events = [event for event in client.get(path + "/audit").json() if event["event_type"] == "CONTENT_SOURCE_ACTIVITY"]
        assert len(activity_events) == 1 and activity_events[0]["details"]["source_id"] == source["id"]
        assert activity_events[0]["details"]["version"] == 2 and activity_events[0]["details"]["is_active"] is False
    with case.database.session() as session:
        item = session.get(MaterialSourceLink, UUID(source["id"])); item.url = "https://catalog.example/replaced"
        with pytest.raises(Exception, match="immutable"): session.commit()


def test_draft_pagination_covers_timestamp_ties_exactly_once(access_case):
    case = access_case; path = f"/api/materials/{case.materials[0].id}"
    with case.client("ADMIN") as client:
        context = client.get(path + "/publishing-context").json()
        ids = {client.post(path + "/content-drafts", json=proposal(context)).json()["id"] for _ in range(4)}
        seen = []; cursor = None
        for _ in range(3):
            response = client.get(path + "/content-drafts", params={"limit": 2, **({"after": cursor} if cursor else {})})
            assert response.status_code == 200
            page = response.json(); seen.extend(item["id"] for item in page["items"]); cursor = page["next_cursor"]
            if not cursor: break
        assert len(seen) == len(set(seen)) == 4 and set(seen) == ids
