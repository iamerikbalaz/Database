"""Reviewed local writes using synthetic Notion HTTP and disposable companies."""
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.db.models import AuthSession, Company, CompanyChangeEvent, InternalUser, UserCredential
from app.main import create_app
from test_application_access import access_case
from test_notion_preview import notion_case, snapshot
from test_notion_reader import PAGE, TOKEN, page, schema, text


def command(item, fields=None):
    with item.case.client("ADMIN") as client:
        response = client.post(item.path, json=item.payload)
        assert response.status_code == 200
    comparison = response.json()
    return {"request_key": str(uuid4()), "expected_page_id": PAGE,
        "expected_local_sha256": comparison["local_sha256"],
        "expected_observation_sha256": comparison["source"]["observation_sha256"],
        "selected_fields": fields or ["name", "country"], "reason": "Reviewed synthetic company values"}


def path(item): return f"/api/companies/{item.identifier}/notion-adopt"


def events(item):
    with item.case.database.session() as session:
        return list(session.scalars(select(CompanyChangeEvent).where(CompanyChangeEvent.company_id == item.identifier)))


def test_selected_values_and_clearing_commit_with_exact_atomic_provenance(notion_case):
    item = notion_case; body = command(item); before = snapshot(item)
    with item.case.client("ADMIN") as client:
        result = client.post(path(item), json=body)
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        saved = result.json(); event = saved["event"]
        assert saved["request_key"] == body["request_key"] and event["action"] == "NOTION_ADOPTED"
        assert event["actor_id"] == str(item.case.users["ADMIN"].id) and event["version"] == 1
        assert event["before"]["name"] == before["name"] and event["before"]["country"] == "CZ"
        assert event["after"]["name"] == "Synthetic česká company" and event["after"]["country"] is None
        assert event["after"]["website"] is None and event["after"]["notion_page_id"] == PAGE and event["after"]["is_active"]
        assert event["reason"] == body["reason"]
        assert event["source"]["selected_fields"] == ["country", "name"]
        assert event["source"]["observation_sha256"] == body["expected_observation_sha256"]
        assert set(event["source"]) == {"page_id", "data_source_id", "database_id", "last_edited_time", "mapping_sha256", "observation_sha256", "selected_fields"}
        assert "SYNTHETIC-PRIVATE-CONTENT" not in result.text and TOKEN not in result.text
        recovery = client.get(f"/api/companies/{item.identifier}/notion-adoptions/{body['request_key']}")
        assert recovery.status_code == 200 and recovery.json() == saved and recovery.headers["cache-control"] == "no-store"
        assert client.get(f"/api/companies/{item.identifier}/history").json()["items"] == [event]
    assert snapshot(item)["name"] == event["after"]["name"] and len(events(item)) == 1
    assert all(request.method == "GET" for request in item.server.requests)


def test_exact_retry_is_historical_after_local_edit_link_removal_and_disabled_integration(notion_case):
    item = notion_case; body = command(item)
    with item.case.client("ADMIN") as client:
        first = client.post(path(item), json=body); assert first.status_code == 200
        assert client.patch(f"/api/companies/{item.identifier}", json={"name": "Later local value", "notion_page_id": None}).status_code == 200
    calls = len(item.server.requests)
    settings = item.case.app.state.settings.model_copy(update={"notion_enabled": False})
    item.case.app = create_app(settings, item.case.database, item.case.worker)
    with item.case.client("ADMIN") as client:
        assert client.post(path(item), json=body).json() == first.json()
        normalized = deepcopy(body); normalized["selected_fields"].reverse(); normalized["reason"] = "  " + body["reason"] + "  "
        assert client.post(path(item), json=normalized).json() == first.json()
    assert len(item.server.requests) == calls and snapshot(item)["name"] == "Later local value"
    assert len(events(item)) == 2


@pytest.mark.parametrize("change", ["reason", "fields", "local", "observation", "page", "company"])
def test_request_key_reuse_cannot_apply_another_command(notion_case, change):
    item = notion_case; body = command(item)
    with item.case.client("ADMIN") as client:
        assert client.post(path(item), json=body).status_code == 200
        changed = deepcopy(body); target = path(item); calls = len(item.server.requests)
        if change == "reason": changed["reason"] = "Different reason"
        elif change == "fields": changed["selected_fields"] = ["name"]
        elif change == "local": changed["expected_local_sha256"] = "a" * 64
        elif change == "observation": changed["expected_observation_sha256"] = "a" * 64
        elif change == "page": changed["expected_page_id"] = str(uuid4())
        else: target = f"/api/companies/{uuid4()}/notion-adopt"
        response = client.post(target, json=changed)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "NOTION_REQUEST_KEY_REUSED"
        assert len(item.server.requests) == calls and len(events(item)) == 1


@pytest.mark.parametrize("role", [None, "PRODUCTION_LEAD", "PROCESSOR", "LEADERSHIP"])
def test_adoption_and_recovery_require_current_administrator(notion_case, role):
    item = notion_case; body = command(item); calls = len(item.server.requests)
    with item.case.client(role) as client:
        expected = 401 if role is None else 403
        assert client.post(path(item), json=body).status_code == expected
        assert client.get(f"/api/companies/{item.identifier}/notion-adoptions/{body['request_key']}").status_code == expected
    assert len(item.server.requests) == calls and not events(item)


@pytest.mark.parametrize("change", ["empty-fields", "duplicates", "unmapped", "active", "zero-key", "blank-reason", "long-reason", "control-reason", "bad-digest", "extra-values"])
def test_command_validation_rejects_unsafe_or_unreviewable_requests(notion_case, change):
    item = notion_case; body = command(item); calls = len(item.server.requests)
    status = 422
    if change == "empty-fields": body["selected_fields"] = []
    elif change == "duplicates": body["selected_fields"] = ["name", "name"]
    elif change == "unmapped": body["selected_fields"] = ["address"]; status = 409
    elif change == "active": body["selected_fields"] = ["is_active"]
    elif change == "zero-key": body["request_key"] = str(UUID(int=0))
    elif change == "blank-reason": body["reason"] = " \n "
    elif change == "long-reason": body["reason"] = "a" * 2001
    elif change == "control-reason": body["reason"] = "reason\x00"
    elif change == "bad-digest": body["expected_local_sha256"] = "not-a-hash"
    else: body["values"] = {"name": "Unreviewed client value"}
    with item.case.client("ADMIN") as client: assert client.post(path(item), json=body).status_code == status
    assert len(item.server.requests) == calls and not events(item)


def test_adoption_requires_csrf(notion_case):
    item = notion_case; body = command(item); calls = len(item.server.requests)
    with item.case.client("ADMIN") as client:
        token = client.headers.pop("X-CSRF-Token")
        assert client.post(path(item), json=body).status_code == 403
        client.headers["X-CSRF-Token"] = token
        assert client.post(path(item), json=body, headers={"Origin": "https://wrong.invalid"}).status_code == 403
    assert len(item.server.requests) == calls and not events(item)


@pytest.mark.parametrize("change", ["local", "link", "remote-value", "remote-time", "mapping", "disabled", "unchanged"])
def test_stale_or_unchanged_selections_never_write(notion_case, change):
    item = notion_case
    if change == "unchanged":
        with item.case.database.session() as session:
            session.get(Company, item.identifier).country = None; session.commit()
    body = command(item); calls = len(item.server.requests)
    if change in {"local", "link"}:
        with item.case.database.session() as session:
            company = session.get(Company, item.identifier)
            if change == "local": company.legal_name = "Changed after comparison"
            else: company.notion_page_id = str(uuid4())
            session.commit()
    elif change == "disabled":
        item.case.app = create_app(item.case.app.state.settings.model_copy(update={"notion_enabled": False}), item.case.database, item.case.worker)
    elif change == "mapping": item.reader.configuration = item.reader.configuration.model_copy(update={"data_source_id": str(uuid4())})
    elif change.startswith("remote"):
        document = page()
        if change == "remote-value": document["properties"]["A new display name"]["title"] = [text("Changed remotely")]
        else: document["last_edited_time"] = "2026-09-18T13:00:00Z"
        async def hook(request): return item.server.response(schema() if "/data_sources/" in str(request.url) else document)
        item.server.hook = hook
    before = snapshot(item)
    with item.case.client("ADMIN") as client:
        response = client.post(path(item), json=body)
        assert response.status_code == (503 if change in {"mapping", "disabled"} else 409)
    assert snapshot(item) == before and not events(item)
    if change in {"local", "link", "disabled", "mapping"}: assert len(item.server.requests) == calls


@pytest.mark.parametrize("change,status", [("name", 409), ("link", 409), ("active", 409), ("role", 403), ("disable", 401), ("password", 403), ("revoke", 401)])
@pytest.mark.parametrize("failed_read", [False, True])
def test_local_change_or_revocation_during_read_prevents_adoption(notion_case, change, status, failed_read):
    item = notion_case; body = command(item)
    async def hook(request):
        with item.case.database.session() as session:
            company = session.get(Company, item.identifier); user = session.get(InternalUser, item.case.users["ADMIN"].id)
            if change == "name": company.name = "New local name"
            elif change == "link": company.notion_page_id = str(uuid4())
            elif change == "active": company.is_active = False
            elif change == "role": user.role = "PROCESSOR"
            elif change == "disable": user.is_active = False
            elif change == "password": session.get(UserCredential, user.id).must_change_password = True
            else:
                for stored in session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)): stored.revoked_at = stored.created_at
            session.commit()
        return item.server.response(schema(), status=503 if failed_read else 200)
    item.server.hook = hook
    with item.case.client("ADMIN") as client:
        response = client.post(path(item), json=body)
        assert response.status_code == status and "Synthetic česká company" not in response.text
    assert not events(item) and snapshot(item)["country"] == "CZ"


def test_failed_audit_insert_rolls_back_adopted_values(notion_case, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from app.api import notion_adoption
    item = notion_case; body = command(item); before = snapshot(item)
    def reject(session, *args, **kwargs):
        session.flush()
        raise IntegrityError("Synthetic failure", {}, Exception("Synthetic failure"))
    monkeypatch.setattr(notion_adoption, "append_company_change", reject)
    with item.case.client("ADMIN") as client:
        response = client.post(path(item), json=body)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "NOTION_ADOPTION_CONFLICT"
    assert snapshot(item) == before and not events(item)


def test_saved_request_recovery_does_not_disclose_other_actor_or_company(notion_case):
    item = notion_case; body = command(item)
    with item.case.client("ADMIN") as client:
        assert client.post(path(item), json=body).status_code == 200
        assert client.get(f"/api/companies/{uuid4()}/notion-adoptions/{body['request_key']}").status_code == 404
        assert client.get(f"/api/companies/{item.identifier}/notion-adoptions/{uuid4()}").status_code == 404
    with item.case.database.session() as session:
        session.get(InternalUser, item.case.users["PROCESSOR"].id).role = "ADMIN"; session.commit()
    with item.case.client("PROCESSOR") as client:
        assert client.get(f"/api/companies/{item.identifier}/notion-adoptions/{body['request_key']}").status_code == 404


@pytest.mark.parametrize("kind", ["page", "source", "mapping", "digest", "value", "exception"])
def test_untrusted_adapter_observation_cannot_be_adopted(notion_case, kind):
    from app.notion_reader import _hash, project
    item = notion_case; body = command(item); before = snapshot(item)
    original = project(item.reader.configuration, PAGE, schema(), page())
    async def forged(*args, **kwargs):
        if kind == "exception": raise RuntimeError("SYNTHETIC-PRIVATE-CONTENT")
        data = original.model_dump(mode="python"); data["values"] = original.values
        if kind == "value":
            data["values"] = tuple(value.model_copy(update={"value": "unsafe\x00value"}) if value.field == "name" else value for value in original.values)
        else: data[{"page": "page_id", "source": "data_source_id", "mapping": "mapping_sha256", "digest": "observation_sha256"}[kind]] = str(uuid4()) if kind in {"page", "source"} else "a" * 64
        observation = original.model_construct(**data)
        if kind != "digest": observation = observation.model_copy(update={"observation_sha256": _hash(observation.model_dump(mode="json", exclude={"observation_sha256"}))})
        return observation
    item.reader.company = forged
    with item.case.client("ADMIN") as client:
        response = client.post(path(item), json=body)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "NOTION_RESPONSE_INVALID"
        assert "SYNTHETIC-PRIVATE-CONTENT" not in response.text and "unsafe" not in response.text
    assert snapshot(item) == before and not events(item)


def test_committed_replay_still_requires_current_role(notion_case):
    item = notion_case; body = command(item)
    with item.case.client("ADMIN") as client:
        assert client.post(path(item), json=body).status_code == 200
        with item.case.database.session() as session:
            session.get(InternalUser, item.case.users["ADMIN"].id).role = "PROCESSOR"; session.commit()
        assert client.post(path(item), json=body).status_code == 403
        assert client.get(f"/api/companies/{item.identifier}/notion-adoptions/{body['request_key']}").status_code == 403
