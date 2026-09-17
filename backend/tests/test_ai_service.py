"""Real issuer sessions, one-time secrets and restricted one-material access."""
import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.auth.service import database_now, _aware
from app.db.models import AiServiceCredential, AuthSession, InternalUser, MaterialAiDraft, MaterialAuditEvent, UserCredential
from test_application_access import access_case, domain_routes
from test_ai_content import proposal
from test_ai_adoption import adoption


def issuance(**changes):
    return {"idempotency_key": str(uuid4()), "lifetime_seconds": 900, "reason": "Synthetic scoped AI client", **changes}


def issue(client, path, **changes):
    result = client.post(path + "/ai-service-credentials", json=issuance(**changes))
    assert result.status_code == 201
    return result.json()


def service_headers(issued):
    return {"Authorization": "Bearer " + issued["token"]}


def test_one_time_issuance_metadata_replay_and_no_secret_in_audit(access_case):
    case = access_case; path = f"/api/materials/{case.materials[0].id}"
    data = issuance()
    with case.client("ADMIN") as client:
        before = client.get(path + "/review").json()
        response = client.post(path + "/ai-service-credentials", json=data); assert response.status_code == 201
        body = response.json(); assert body["secret_available"] is True
        assert response.headers["cache-control"] == "no-store"
        assert bool(body["token"].startswith("reawote_ai_"))
        replay = client.post(path + "/ai-service-credentials", json=data)
        assert replay.status_code == 201 and replay.json()["token"] is None and replay.json()["secret_available"] is False
        assert replay.json()["credential"] == body["credential"]
        assert client.post(path + "/ai-service-credentials", json={**data, "lifetime_seconds": 60}).status_code == 409
        assert client.get(path + "/review").json() == before
        history = client.get(path + "/ai-service-credentials").json()
        assert len(history["items"]) == 1 and history["next_cursor"] is None
        assert set(history["items"][0]) == {"id", "material_id", "actor_id", "created_at", "expires_at", "revoked_at", "scopes"}
        audit_text = client.get(path + "/audit").text
        assert bool(body["token"] not in audit_text and body["token"].split(".")[1] not in audit_text)
    with case.database.session() as session:
        row = session.get(AiServiceCredential, UUID(body["credential"]["id"]))
        assert len(row.token_hash) == 64 and row.token_hash != body["token"].split(".")[1]
        stored = json.dumps([item.result for item in session.scalars(select(MaterialAuditEvent))])
        assert bool(body["token"] not in stored and body["token"].split(".")[1] not in stored)


@pytest.mark.parametrize("role,expected", [(None, 401), ("ADMIN", 201), ("PRODUCTION_LEAD", 403), ("PROCESSOR", 403), ("LEADERSHIP", 403), ("OTHER", 403)])
def test_only_real_administrator_can_issue_credentials(access_case, role, expected):
    with access_case.client(role) as client:
        assert client.post(f"/api/materials/{access_case.materials[0].id}/ai-service-credentials", json=issuance()).status_code == expected


def test_service_has_only_minimal_context_and_draft_write_never_human_authority(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"; service = f"/api/ai/materials/{material.id}"
    with case.client("ADMIN") as admin, case.client() as ai:
        issued = issue(admin, path); ai.headers.update(service_headers(issued))
        view = ai.get(service + "/publishing-context"); assert view.status_code == 200
        assert set(view.json()["context"]) == {"material_id", "name", "brand", "categories", "collections", "source_urls"}
        assert not any(key in view.text for key in ("issuer", "actor", "session", "folder_path", "metadata", "email", "credits", "description"))
        data = proposal(view.json()); first = ai.post(service + "/content-drafts", json=data)
        assert first.status_code == 201
        assert set(first.json()) == {"id", "status", "context_hash"}
        replay = ai.post(service + "/content-drafts", json=data); assert replay.status_code == 201 and replay.json() == first.json()
        assert ai.post(service + "/content-drafts", json={**data, "description": "Conflicting service retry"}).status_code == 409
        for method, target in domain_routes(case.app):
            assert ai.request(method, target, json={}).status_code == 401, (method, target)
        assert ai.get(f"/api/ai/materials/{case.materials[1].id}/publishing-context").status_code == 401
        assert ai.get(f"/api/ai/materials/{uuid4()}/publishing-context").status_code == 401
        draft = admin.get(path + "/content-drafts").json()["items"][0]
        assert draft["service_credential_id"] == issued["credential"]["id"]
        assert admin.get(path + "/content").json()["revision"] == 0
        adopted = admin.post(path + "/content-drafts/" + draft["id"] + "/adopt", json=adoption(draft))
        assert adopted.status_code == 200
        assert adopted.json()["ai_provenance"]["service_credential_id"] == issued["credential"]["id"]
        assert ai.post(service + "/content-drafts", json=data).json() == first.json()
        assert ai.post(service + "/content-drafts", json={**data, "idempotency_key": str(uuid4())}).status_code == 409


def test_cookie_origin_query_and_malformed_credentials_never_authenticate(access_case):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"; target = f"/api/ai/materials/{material.id}/publishing-context"
    with case.client("ADMIN") as admin, case.client() as ai:
        issued = issue(admin, path)
        assert admin.get(target, headers=service_headers(issued)).status_code == 401
        for header in ({}, {"Authorization": "Bearer " + "x" * 200}, {**service_headers(issued), "Origin": "https://testserver"},
                       {**service_headers(issued), "Cookie": "unrelated=value"}):
            assert ai.get(target, headers=header).status_code == 401
        assert ai.get(target + "?unused=value", headers=service_headers(issued)).status_code == 401
        assert ai.get(target, headers=[("Authorization", "Bearer invalid"), ("Authorization", service_headers(issued)["Authorization"])]).status_code == 401
        assert ai.get(target, headers=service_headers(issued)).status_code == 200


def test_ai_service_route_inventory_is_explicit_and_rejects_browser_sessions(access_case):
    expected = {("/api/ai/materials/{material_id}/publishing-context", "get"), ("/api/ai/materials/{material_id}/content-drafts", "post")}
    actual = {(path, method) for path, methods in access_case.app.openapi()["paths"].items() if path.startswith("/api/ai/") for method in methods}
    assert actual == expected
    for role in (None, "ADMIN", "PROCESSOR"):
        with access_case.client(role) as client:
            for path, method in actual:
                target = path.replace("{material_id}", str(access_case.materials[0].id))
                assert client.request(method, target, json={}).status_code == 401


@pytest.mark.parametrize("change", ["revoke", "logout", "deactivate", "demote", "assignment", "must_change", "session_expired", "credential_expired", "session_removed"])
def test_issuer_or_credential_lifecycle_blocks_context_submission_and_replay(access_case, monkeypatch, change):
    case = access_case; material = case.materials[0]; path = f"/api/materials/{material.id}"; service = f"/api/ai/materials/{material.id}"
    with case.client("ADMIN") as admin, case.client() as ai:
        issued = issue(admin, path); ai.headers.update(service_headers(issued))
        context = ai.get(service + "/publishing-context").json(); data = proposal(context)
        assert ai.post(service + "/content-drafts", json=data).status_code == 201
        if change == "revoke":
            response = admin.post(path + "/ai-service-credentials/" + issued["credential"]["id"] + "/revoke", json={"idempotency_key": str(uuid4()), "reason": "Stop synthetic service"})
            assert response.status_code == 200
        elif change == "logout": assert admin.post("/api/auth/logout").status_code == 200
        else:
            with case.database.session() as session:
                grant = session.get(AiServiceCredential, UUID(issued["credential"]["id"]))
                human = session.get(AuthSession, grant.issuer_session_id)
                if change == "deactivate": session.get(InternalUser, grant.actor_id).is_active = False
                elif change == "demote": session.get(InternalUser, grant.actor_id).role = "LEADERSHIP"
                elif change == "assignment": session.get(InternalUser, grant.actor_id).role = "PROCESSOR"
                elif change == "must_change": session.get(UserCredential, grant.actor_id).must_change_password = True
                elif change == "session_expired": human.idle_expires_at = _aware(human.created_at) + timedelta(microseconds=1)
                elif change == "session_removed": session.delete(human)
                elif change == "credential_expired": monkeypatch.setattr("app.ai_service_access.database_now", lambda _: _aware(grant.expires_at) + timedelta(seconds=1))
                session.commit()
        assert ai.get(service + "/publishing-context").status_code == 401
        assert ai.post(service + "/content-drafts", json=data).status_code == 401
        with case.database.session() as session:
            assert len(list(session.scalars(select(MaterialAiDraft)))) == 1


def test_service_keys_are_independent_from_human_and_other_issuances(access_case):
    case = access_case; path = f"/api/materials/{case.materials[0].id}"; service = path.replace("/api/", "/api/ai/")
    with case.client("ADMIN") as admin, case.client() as ai:
        first = issue(admin, path); second = issue(admin, path)
        data = proposal(admin.get(path + "/publishing-context").json())
        human = admin.post(path + "/content-drafts", json=data); assert human.status_code == 201
        results = []
        for credential in (first, second):
            response = ai.post(service + "/content-drafts", headers=service_headers(credential), json=data)
            assert response.status_code == 201; results.append(response.json()["id"])
        assert len(set([human.json()["id"], *results])) == 3


def test_active_limit_paging_and_idempotent_permanent_revocation(access_case):
    case = access_case; path = f"/api/materials/{case.materials[0].id}"
    with case.client("ADMIN") as admin:
        grants = [issue(admin, path) for _ in range(10)]
        assert admin.post(path + "/ai-service-credentials", json=issuance()).status_code == 409
        result = admin.get(path + "/ai-service-credentials?limit=3").json(); assert len(result["items"]) == 3
        ids = {item["id"] for item in result["items"]}
        while result["next_cursor"]:
            result = admin.get(path + "/ai-service-credentials", params={"limit": 3, "after": result["next_cursor"]}).json()
            assert not ids.intersection(item["id"] for item in result["items"]); ids.update(item["id"] for item in result["items"])
        assert len(ids) == 10
        target = path + "/ai-service-credentials/" + grants[0]["credential"]["id"] + "/revoke"
        data = {"idempotency_key": str(uuid4()), "reason": "Retire test credential"}
        one = admin.post(target, json=data); two = admin.post(target, json=data)
        assert one.status_code == two.status_code == 200 and one.json() == two.json()
        assert admin.post(target, json={**data, "idempotency_key": str(uuid4())}).json() == one.json()
        issue(admin, path)
        with case.database.session() as session:
            grant = session.get(AiServiceCredential, UUID(grants[0]["credential"]["id"]))
            grant.revoked_at = None
            with pytest.raises(ValueError): session.commit()


@pytest.mark.parametrize("ttl", [0, 59, 3601, True, "900", 90.5])
def test_issuance_bounds_and_validation_never_echo_extra_secret_input(access_case, ttl):
    path = f"/api/materials/{access_case.materials[0].id}/ai-service-credentials"
    with access_case.client("ADMIN") as client:
        response = client.post(path, json=issuance(lifetime_seconds=ttl))
        assert response.status_code == 422


def test_issuance_validation_does_not_reflect_extra_input(access_case):
    marker = "SYNTHETIC_DO_NOT_REFLECT"
    with access_case.client("ADMIN") as client:
        response = client.post(f"/api/materials/{access_case.materials[0].id}/ai-service-credentials", json=issuance(**{marker: marker}))
        assert response.status_code == 422 and marker not in response.text


def test_service_validation_uses_fixed_errors_and_no_foreign_sources(access_case):
    case = access_case; path = f"/api/materials/{case.materials[0].id}"; service = path.replace("/api/", "/api/ai/")
    with case.client("ADMIN") as admin, case.client() as ai:
        ai.headers.update(service_headers(issue(admin, path)))
        context = ai.get(service + "/publishing-context").json()
        marker = "SYNTHETIC_DO_NOT_REFLECT"
        response = ai.post(service + "/content-drafts", json={**proposal(context), marker: marker, "provider": "\x00" + marker})
        assert response.status_code == 422 and marker not in response.text
        assert ai.post(service + "/content-drafts", json=proposal(context, source_link_ids=[str(uuid4())])).status_code == 422
