"""Immutable successful account-security outcomes with synthetic credentials."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.account_security_history import append_security_event
from app.auth import accounts, router
from app.auth.cli import provision_first_admin
from app.auth.service import _aware
from app.db.models import AccountSecurityEvent, ImmutableAuditSnapshotError, InternalUser, UserCredential
from test_application_access import PASSWORD, TEMPORARY, access_case
from test_auth import NEW_PASSWORD
from test_auth_cli import database, settings


def path(user_id): return f"/api/internal-users/{user_id}/security-history"


def events(case, user_id):
    with case.client("ADMIN") as client:
        response = client.get(path(user_id)); assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        return response.json()["items"]


@pytest.mark.parametrize("existing", [False, True])
def test_first_admin_records_host_origin_without_inventing_an_application_actor(database, settings, existing):
    from test_auth_cli import PASSWORD as bootstrap_password
    email = "security-admin@example.invalid"
    if existing:
        with database.session() as session:
            session.add(InternalUser(email=email, display_name="Existing profile", role="PROCESSOR", is_active=False)); session.commit()
    identifier = provision_first_admin(database, settings, email=email, display_name="Synthetic admin", password=bootstrap_password)
    with database.session() as session:
        item = session.scalar(select(AccountSecurityEvent)); credential = session.get(UserCredential, identifier)
        assert item.user_id == identifier and item.actor_id is None and item.version == 1
        assert item.action == "FIRST_ADMIN_PROVISIONED" and item.requires_password_change
        assert _aware(item.credential_changed_at) == _aware(credential.password_changed_at)
        assert not {"password_hash", "token", "session_id", "payload"} & set(AccountSecurityEvent.__table__.columns.keys())


def test_admin_provisioning_records_exact_safe_metadata(access_case):
    case = access_case
    with case.client("ADMIN") as admin:
        user = admin.post("/api/internal-users", json={"display_name": "Audit account", "email": "audit-account@example.invalid", "role": "PROCESSOR"}).json()
        assert admin.post(f"/api/auth/accounts/{user['id']}/access", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 200
        history = admin.get(path(user["id"])).json(); assert history["user_id"] == user["id"] and history["next_cursor"] is None
        item = history["items"][0]
        assert item["action"] == "ADMIN_ACCESS_PROVISIONED" and item["actor_id"] == str(case.users["ADMIN"].id)
        assert item["version"] == 1 and item["requires_password_change"]
        assert set(item) == {"id", "user_id", "actor_id", "version", "action", "requires_password_change", "credential_changed_at", "created_at"}


def test_host_recovery_records_explicit_host_origin_and_keeps_application_role(access_case, monkeypatch, capsys):
    from app.auth import recovery
    case = access_case; user = case.users["PROCESSOR"]
    answers = iter([TEMPORARY, TEMPORARY])
    monkeypatch.setattr("sys.argv", ["recovery", "--email", user.email])
    monkeypatch.setattr(recovery.getpass, "getpass", lambda _: next(answers))
    monkeypatch.setattr(recovery, "Database", lambda _: case.database)
    monkeypatch.setattr(recovery, "get_settings", lambda: case.app.state.settings)
    with case.client("PROCESSOR") as old:
        assert recovery.main() == 0 and old.get("/api/auth/session").status_code == 401
    item, = events(case, user.id)
    assert item["action"] == "HOST_ACCESS_RECOVERED" and item["actor_id"] is None and item["requires_password_change"]
    with case.database.session() as session: assert session.get(InternalUser, user.id).role == "PROCESSOR"
    output = capsys.readouterr(); assert TEMPORARY not in output.out + output.err


@pytest.mark.parametrize("existing", [False, True])
def test_bootstrap_audit_failure_restores_profile_and_creates_no_credentials(database, settings, monkeypatch, existing):
    from app.auth import cli
    from test_auth_cli import PASSWORD as bootstrap_password
    email = "failed-bootstrap@example.invalid"
    if existing:
        with database.session() as session:
            session.add(InternalUser(email=email, display_name="Prior profile", role="PROCESSOR", is_active=False)); session.commit()
    def reject(session, *args):
        session.flush(); raise RuntimeError("Synthetic bootstrap audit failure")
    monkeypatch.setattr(cli, "append_security_event", reject)
    with pytest.raises(RuntimeError, match="Synthetic bootstrap audit failure"):
        cli.provision_first_admin(database, settings, email=email, display_name="Attempted admin", password=bootstrap_password)
    with database.session() as session:
        assert not list(session.scalars(select(UserCredential))) and not list(session.scalars(select(AccountSecurityEvent)))
        user = session.scalar(select(InternalUser))
        if existing: assert user.role == "PROCESSOR" and user.display_name == "Prior profile" and not user.is_active
        else: assert user is None


def test_reset_and_self_service_preserve_actor_order_and_session_revocation(access_case):
    case = access_case; target = case.users["OTHER"].id
    with case.client("ADMIN") as admin, case.client("OTHER") as old:
        assert admin.post(f"/api/auth/accounts/{target}/access", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 200
        assert old.get("/api/companies").status_code == 401
    with case.client("OTHER", password=NEW_PASSWORD) as owner:
        body = {"current_password": NEW_PASSWORD, "new_password": NEW_PASSWORD + " updated"}
        assert owner.post("/api/auth/change-password", json=body).status_code == 200
        assert owner.post("/api/auth/change-password", json=body).status_code == 401
    items = events(case, target)
    assert [item["action"] for item in items] == ["SELF_PASSWORD_CHANGED", "ADMIN_ACCESS_RESET"]
    assert [item["version"] for item in items] == [2, 1]
    assert [item["requires_password_change"] for item in items] == [False, True]
    assert [item["actor_id"] for item in items] == [str(target), str(case.users["ADMIN"].id)]


@pytest.mark.parametrize("role", [None, "PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"])
def test_security_history_requires_current_admin(access_case, role):
    case = access_case
    with case.client(role) as client:
        assert client.get(path(case.users["OTHER"].id)).status_code == (401 if role is None else 403)


@pytest.mark.parametrize("mode", ["admin", "self"])
def test_failed_audit_rolls_back_password_and_session_changes(access_case, monkeypatch, mode):
    case = access_case; target = case.users["OTHER"].id
    with case.database.session() as session:
        before = session.get(UserCredential, target).password_hash
    def reject(session, *args):
        session.flush(); raise RuntimeError("Synthetic audit failure")
    monkeypatch.setattr(accounts if mode == "admin" else router, "append_security_event", reject)
    with case.client("OTHER") as owner, case.client("ADMIN") as admin:
        endpoint = f"/api/auth/accounts/{target}/access" if mode == "admin" else "/api/auth/change-password"
        with pytest.raises(RuntimeError, match="Synthetic audit failure"):
            (admin if mode == "admin" else owner).post(endpoint, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        assert owner.get("/api/companies").status_code == 200
    with case.database.session() as session:
        assert session.get(UserCredential, target).password_hash == before
        assert not list(session.scalars(select(AccountSecurityEvent)))


def test_rejected_reset_is_not_a_successful_security_event(access_case):
    case = access_case; target = case.users["OTHER"].id
    with case.client("ADMIN") as admin:
        assert admin.post(f"/api/auth/accounts/{target}/access", json={"current_password": PASSWORD + " incorrect", "new_password": NEW_PASSWORD}).status_code == 400
    assert events(case, target) == []


def test_security_history_pages_are_bounded_and_target_bound(access_case):
    case = access_case; target = case.users["OTHER"].id
    assert events(case, target) == []
    for _ in range(23):
        with case.database.session() as session:
            credential = session.get(UserCredential, target); credential.password_changed_at += timedelta(seconds=1)
            append_security_event(session, target, "SELF_PASSWORD_CHANGED", target); session.commit()
    with case.client("ADMIN") as admin:
        first = admin.get(path(target)).json(); assert [item["version"] for item in first["items"]] == list(range(23, 3, -1))
        assert first["next_cursor"] == first["items"][-1]["id"]
        last = admin.get(path(target), params={"after": first["next_cursor"]}).json()
        assert [item["version"] for item in last["items"]] == [3, 2, 1] and last["next_cursor"] is None
        assert admin.get(path(case.users["ADMIN"].id), params={"after": first["next_cursor"]}).status_code == 409
        assert admin.get(path(target), params={"after": str(uuid4())}).status_code == 409
        assert admin.get(path(uuid4())).status_code == 404


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_security_event_is_immutable_in_orm(access_case, operation):
    case = access_case; target = case.users["OTHER"].id
    with case.database.session() as session:
        item = append_security_event(session, target, "SELF_PASSWORD_CHANGED", target); session.commit(); identifier = item.id
    with case.database.session() as session, pytest.raises(ImmutableAuditSnapshotError):
        item = session.get(AccountSecurityEvent, identifier)
        if operation == "update": item.version = 2
        else: session.delete(item)
        session.commit()
