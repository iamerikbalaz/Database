"""Actual HTTP sessions and domain authorization; no dependency overrides."""
from dataclasses import dataclass
import re
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.auth.security import PasswordService
from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    AuthSession, Company, InternalUser, PBRMaterial, PBRMaterialMetadata,
    PBRMaterialMetadataSnapshot, Project, PublishedBrand, UserCredential,
)
from app.db.session import Database
from app.main import create_app
from test_material_operations import StubWorker, _preflight

ORIGIN = "https://testserver"
PASSWORD = "Quartz meadow river! 2026"
TEMPORARY = "Amber forest lanterns 2047"
NEW_PASSWORD = "Violet harbor lanterns 2048"


@dataclass
class AccessCase:
    database: Database
    app: object
    worker: StubWorker
    users: dict
    materials: list

    def client(self, role=None, password=PASSWORD):
        client = TestClient(self.app, base_url=ORIGIN)
        if role is not None:
            response = client.post("/api/auth/login", json={
                "email": self.users[role].email, "password": password,
            }, headers={"Origin": ORIGIN})
            assert response.status_code == 200
            client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
        return client


@pytest.fixture
def access_case():
    class SharedTestDatabase(Database):
        def dispose(self):
            pass  # Individual browser lifespans share this fixture's database.
    database = SharedTestDatabase("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    settings = Settings(_env_file=None, cors_origins=ORIGIN, auth_rate_limit_attempts=100)
    password_hash = PasswordService(settings).hash_password(PASSWORD)
    users = {}
    with database.session() as session:
        for key in ("ADMIN", "PRODUCTION_LEAD", "LEADERSHIP", "PROCESSOR", "OTHER"):
            user = InternalUser(display_name=key, email=f"{key.lower()}@example.invalid",
                                role="PROCESSOR" if key == "OTHER" else key)
            user.credential = UserCredential(password_hash=password_hash, must_change_password=False)
            session.add(user)
            users[key] = user
        company = Company(name="Synthetic company")
        session.add(company)
        session.flush()
        project = Project(company_id=company.id, project_number="TEST", name="Synthetic project")
        brand = PublishedBrand(company_id=company.id, name="Synthetic brand", folder_prefix="SAFE",
                               brand_identifier="synthetic", next_sequence_number=3)
        session.add_all([project, brand])
        session.flush()
        materials = []
        for number, key in enumerate(("PROCESSOR", "OTHER"), 1):
            material = PBRMaterial(project_id=project.id, published_brand_id=brand.id,
                assigned_processor_id=users[key].id, sequence_number=number,
                main_category_code="G03", material_name=f"Material {number}",
                technical_identity=f"SAFE_{number:04d}_G03")
            material.metadata_state = PBRMaterialMetadata()
            session.add(material)
            materials.append(material)
        session.commit()
    worker = StubWorker()
    app = create_app(settings, database, worker)
    assert not app.dependency_overrides
    yield AccessCase(database, app, worker, users, materials)
    database.engine.dispose()


def domain_routes(app):
    for path, methods in app.openapi()["paths"].items():
        # AI service routes reject cookies entirely and have a separate exhaustive
        # bearer scope/lifecycle suite. Human credential management stays here.
        if path.startswith("/api/") and not path.startswith("/api/ai/") and (
            not path.startswith("/api/auth/") or path.startswith("/api/auth/accounts/")
        ):
            path = re.sub(r"\{[^}]+\}", lambda _: str(uuid4()), path)
            for method in methods:
                if method.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                    yield method.upper(), path


def test_every_domain_route_rejects_anonymous_and_forced_password_accounts(access_case):
    case = access_case
    with case.client() as anonymous:
        routes = list(domain_routes(case.app))
        assert len(routes) >= 25
        for method, path in routes:
            assert anonymous.request(method, path, json={}).status_code == 401, (method, path)
    with case.database.session() as session:
        session.get(UserCredential, case.users["ADMIN"].id).must_change_password = True
        session.commit()
    with case.client("ADMIN") as forced:
        assert forced.get("/api/auth/session").status_code == 200
        for method, path in routes:
            response = forced.request(method, path, json={})
            assert response.status_code == 403, (method, path)
            assert response.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
        assert forced.post("/api/auth/change-password", json={
            "current_password": PASSWORD, "new_password": NEW_PASSWORD,
        }).status_code == 200
        assert forced.get("/api/companies").status_code == 401
    with case.client("ADMIN", NEW_PASSWORD) as renewed:
        assert renewed.get("/api/companies").status_code == 200


def test_every_domain_mutation_requires_csrf_and_trusted_origin(access_case):
    with access_case.client("ADMIN") as client:
        token = client.headers.pop("X-CSRF-Token")
        for method, path in domain_routes(access_case.app):
            if method in {"GET", "HEAD", "OPTIONS"}:
                continue
            assert client.request(method, path, json={}).status_code == 403
            assert client.request(method, path, json={}, headers={
                "X-CSRF-Token": token, "Origin": "https://untrusted.invalid",
            }).status_code == 403


@pytest.mark.parametrize("role,can_manage", [("PROCESSOR", False), ("LEADERSHIP", False),
    ("PRODUCTION_LEAD", True), ("ADMIN", True)])
def test_catalog_and_user_management_role_matrix(access_case, role, can_manage):
    with access_case.client(role) as client:
        assert client.get("/api/companies").status_code == 200
        response = client.post("/api/companies", json={"name": "New company"})
        assert response.status_code == (201 if can_manage else 403)
        response = client.post("/api/internal-users", json={
            "display_name": "New processor", "email": "new@example.invalid", "role": "ADMIN",
        })
        assert response.status_code == (201 if role == "ADMIN" else 403)
        response = client.patch(f"/api/internal-users/{access_case.users['OTHER'].id}", json={"role": "ADMIN"})
        assert response.status_code == (200 if role == "ADMIN" else 403)


def test_processor_only_sees_and_edits_assigned_materials(access_case):
    own, other = access_case.materials
    with access_case.client("PROCESSOR") as client:
        assert [item["id"] for item in client.get("/api/materials").json()] == [str(own.id)]
        assert client.get("/api/materials", params={"assigned_processor_id": str(other.assigned_processor_id)}).json() == []
        assert [item["id"] for item in client.get("/api/internal-users").json()] == [str(own.assigned_processor_id)]
        for suffix in ("", "/metadata", "/metadata/snapshots"):
            assert client.get(f"/api/materials/{own.id}{suffix}").status_code == 200
            assert client.get(f"/api/materials/{other.id}{suffix}").status_code == 404
        assert client.patch(f"/api/materials/{own.id}", json={"material_name": "Own new name"}).status_code == 200
        assert client.patch(f"/api/materials/{other.id}", json={"material_name": "Forbidden"}).status_code == 404
        assert client.patch(f"/api/materials/{own.id}", json={"assigned_processor_id": str(other.assigned_processor_id)}).status_code == 403
        assert client.post("/api/materials", json={
            "project_id": str(own.project_id), "published_brand_id": str(own.published_brand_id),
            "material_name": "Forbidden", "main_category_code": "G03", "assigned_processor_id": str(own.assigned_processor_id),
        }).status_code == 403
        for action in ("folder-preflight", "folder-link", "mark-done"):
            payload = {} if action == "mark-done" else {"folder_path": f"library/{other.technical_identity}"}
            assert client.post(f"/api/materials/{other.id}/{action}", json=payload).status_code == 404
        for action in ("folder-preflight", "folder-link"):
            assert client.post(f"/api/materials/{own.id}/{action}", json={
                "folder_path": f"library/{other.technical_identity}",
            }).status_code == 404
        assert access_case.worker.calls == []


@pytest.mark.parametrize("role", ["PRODUCTION_LEAD", "LEADERSHIP", "ADMIN"])
def test_management_reads_all_materials_and_leadership_cannot_modify(access_case, role):
    with access_case.client(role) as client:
        assert len(client.get("/api/materials").json()) == 2
        material = access_case.materials[0]
        response = client.patch(f"/api/materials/{material.id}", json={"material_name": "Reviewed"})
        assert response.status_code == (403 if role == "LEADERSHIP" else 200)


@pytest.mark.parametrize("change,expected", [("role", 403), ("active", 401),
    ("password", 403), ("revocation", 401), ("assignment", 404)])
def test_access_rechecked_after_worker_call(access_case, change, expected):
    case = access_case
    material = case.materials[0]
    def preflight(path):
        with case.database.session() as session:
            user = session.get(InternalUser, case.users["PROCESSOR"].id)
            if change == "role": user.role = "LEADERSHIP"
            if change == "active": user.is_active = False
            if change == "password": session.get(UserCredential, user.id).must_change_password = True
            if change == "revocation":
                for stored in session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
                    stored.revoked_at = stored.created_at
            if change == "assignment": session.get(PBRMaterial, material.id).assigned_processor_id = case.users["OTHER"].id
            session.commit()
        return _preflight(material.technical_identity)
    case.worker.preflight = preflight
    with case.client("PROCESSOR") as client:
        response = client.post(f"/api/materials/{material.id}/folder-link", json={"folder_path": f"library/{material.technical_identity}"})
        assert response.status_code == expected
    with case.database.session() as session:
        assert session.get(PBRMaterial, material.id).folder_path is None
        assert list(session.scalars(select(PBRMaterialMetadataSnapshot))) == []


def test_admin_reset_revokes_sessions_forces_change_and_never_returns_secrets(access_case):
    case = access_case
    target = case.users["PROCESSOR"]
    with case.client("PROCESSOR") as old, case.client("ADMIN") as admin:
        response = admin.post(f"/api/auth/accounts/{target.id}/access", json={
            "current_password": PASSWORD, "new_password": TEMPORARY,
        })
        assert response.status_code == 200
        assert set(response.json()) == {"user_id", "must_change_password", "changed_at"}
        assert response.json()["must_change_password"] is True
        assert response.headers["cache-control"] == "no-store"
        assert old.get("/api/auth/session").status_code == 401
        assert old.post("/api/auth/login", json={"email": target.email, "password": PASSWORD}).status_code == 401
    with case.client("PROCESSOR", TEMPORARY) as renewed:
        assert renewed.get("/api/materials").status_code == 403
        assert renewed.post("/api/auth/change-password", json={"current_password": TEMPORARY, "new_password": NEW_PASSWORD}).status_code == 200
    with case.client("PROCESSOR", NEW_PASSWORD) as renewed:
        assert renewed.get("/api/materials").status_code == 200


def test_access_provisioning_rejects_non_admin_weak_password_and_wrong_confirmation(access_case):
    case = access_case
    target = case.users["OTHER"]
    for role in ("PROCESSOR", "PRODUCTION_LEAD", "LEADERSHIP"):
        with case.client(role) as client:
            assert client.post(f"/api/auth/accounts/{target.id}/access", json={"current_password": PASSWORD, "new_password": TEMPORARY}).status_code == 403
    with case.client("ADMIN") as admin:
        for current, new, status in ((TEMPORARY, NEW_PASSWORD, 400), (PASSWORD, PASSWORD, 422),
                                      (PASSWORD, "xxxxxxxxxxxxxxxx", 422)):
            response = admin.post(f"/api/auth/accounts/{target.id}/access", json={"current_password": current, "new_password": new})
            assert response.status_code == status
            assert current not in response.text and new not in response.text
        assert admin.patch(f"/api/internal-users/{case.users['ADMIN'].id}", json={"is_active": False}).status_code == 409
        assert admin.patch(f"/api/internal-users/{case.users['ADMIN'].id}", json={"role": "PROCESSOR"}).status_code == 409
        created = admin.post("/api/internal-users", json={"display_name": "New Person", "email": "new@example.invalid", "role": "PROCESSOR"}).json()
        assert admin.post(f"/api/auth/accounts/{created['id']}/access", json={"current_password": PASSWORD, "new_password": TEMPORARY}).status_code == 200
        assert admin.patch(f"/api/internal-users/{created['id']}", json={"is_active": False}).status_code == 200
        assert admin.post(f"/api/auth/accounts/{created['id']}/access", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 409


def test_role_change_immediately_revokes_existing_sessions(access_case):
    case = access_case
    with case.client("PRODUCTION_LEAD") as lead, case.client("ADMIN") as admin:
        assert admin.patch(f"/api/internal-users/{case.users['PRODUCTION_LEAD'].id}", json={"role": "LEADERSHIP"}).status_code == 200
        assert lead.get("/api/materials").status_code == 401
    with case.client("PRODUCTION_LEAD") as renewed:
        assert renewed.get("/api/materials").status_code == 200
        assert renewed.post("/api/companies", json={"name": "Denied"}).status_code == 403


def test_recovery_cli_preserves_role_and_forces_change(access_case, monkeypatch, capsys):
    from app.auth import recovery
    case = access_case
    monkeypatch.setattr("sys.argv", ["recovery", "--email", case.users["PROCESSOR"].email])
    answers = iter([TEMPORARY, TEMPORARY])
    monkeypatch.setattr(recovery.getpass, "getpass", lambda _: next(answers))
    monkeypatch.setattr(recovery, "Database", lambda _: case.database)
    monkeypatch.setattr(recovery, "get_settings", lambda: case.app.state.settings)
    with case.client("PROCESSOR") as existing:
        assert recovery.main() == 0
        assert existing.get("/api/auth/session").status_code == 401
    with case.database.session() as session:
        assert session.get(InternalUser, case.users["PROCESSOR"].id).role == "PROCESSOR"
        assert session.get(UserCredential, case.users["PROCESSOR"].id).must_change_password
    with case.client("PROCESSOR", TEMPORARY) as renewed:
        assert renewed.get("/api/materials").status_code == 403
    output = capsys.readouterr()
    assert TEMPORARY not in output.out + output.err


def test_recovery_mismatch_never_opens_database(monkeypatch):
    from app.auth import recovery
    monkeypatch.setattr("sys.argv", ["recovery", "--email", "test@example.invalid"])
    answers = iter([TEMPORARY, NEW_PASSWORD])
    monkeypatch.setattr(recovery.getpass, "getpass", lambda _: next(answers))
    monkeypatch.setattr(recovery, "Database", lambda _: pytest.fail("Must not open a database"))
    with pytest.raises(SystemExit) as exc:
        recovery.main()
    assert exc.value.code == 2
