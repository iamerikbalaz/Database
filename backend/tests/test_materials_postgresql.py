import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import PasswordService
from app.core.config import Settings
from app.core.config import get_settings
from app.db.models import (
    AuthSession,
    Company,
    InternalUser,
    PBRMaterial,
    PBRMaterialMetadata,
    PBRMaterialMetadataSnapshot,
    Project,
    PublishedBrand,
    UserCredential,
    MaterialAuditEvent, MaterialInventory, MaterialReviewState,
)
from app.db.session import Database
from domain_support import create_domain_app as create_app
from app.worker_client import WorkerMaterialPreflight, WorkerMaterialPreflightResponse


@pytest.fixture
def review_pg_case(migrated_postgresql_url):
    from types import SimpleNamespace
    from app.main import create_app as authenticated_app
    from test_material_review import InventoryStub
    from test_material_approvals import TechnicalStub
    from test_application_access import PASSWORD, ORIGIN

    class SharedDatabase(Database):
        def dispose(self): pass
    database = SharedDatabase(migrated_postgresql_url)
    suffix = uuid4().hex
    settings = Settings(_env_file=None, database_url=migrated_postgresql_url, cors_origins=ORIGIN, auth_rate_limit_attempts=100)
    password_hash = PasswordService(settings).hash_password(PASSWORD)
    with database.session() as session:
        users = []
        for role in ("ADMIN", "PROCESSOR", "PROCESSOR", "ADMIN"):
            user = InternalUser(display_name="Review fixture", email=f"{uuid4().hex}@example.invalid", role=role)
            user.credential = UserCredential(password_hash=password_hash, must_change_password=False)
            session.add(user); users.append(user)
        company = Company(name="Review company " + suffix); session.add(company); session.flush()
        project = Project(company_id=company.id, project_number=suffix, name="Review fixture")
        brand = PublishedBrand(company_id=company.id, name="Review brand", folder_prefix="RV" + suffix[:8], brand_identifier=suffix)
        session.add_all([project, brand]); session.flush()
        material = PBRMaterial(project_id=project.id, published_brand_id=brand.id, sequence_number=1,
            assigned_processor_id=users[1].id, material_name="Review material", main_category_code="G03",
            technical_identity=f"{brand.folder_prefix}_0001_G03", folder_path=f"library/{brand.folder_prefix}_0001_G03")
        material.metadata_state = PBRMaterialMetadata(); session.add(material); session.commit()
    inventory = InventoryStub()
    technical = TechnicalStub()
    app = authenticated_app(settings, database, inventory_client=inventory, technical_client=technical)
    def client_for(index=0):
        client = TestClient(app, base_url=ORIGIN)
        response = client.post("/api/auth/login", json={"email": users[index].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        assert response.status_code == 200
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
        return client
    yield SimpleNamespace(database=database, app=app, inventory=inventory, technical=technical, users=users, material=material,
                          path=f"/api/materials/{material.id}", client_for=client_for)
    database.engine.dispose()


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_concurrent_inventory_replay_is_atomic(review_pg_case, same_key):
    case = review_pg_case
    barrier = Barrier(2)
    case.inventory.callback = lambda: barrier.wait(timeout=15)
    key = str(uuid4())
    with case.client_for() as first, case.client_for() as second:
        def request(client, request_key):
            response = client.post(case.path + "/inventory/scan", json={"idempotency_key": request_key, "expected_generation": 0})
            return response.status_code, response.json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(request, first, key)
            two = pool.submit(request, second, key if same_key else str(uuid4()))
            results = [one.result(timeout=25), two.result(timeout=25)]
        assert sorted(item[0] for item in results) == ([200, 200] if same_key else [200, 409])
        if same_key: assert results[0][1] == results[1][1]
    with case.database.session() as session:
        assert len(list(session.scalars(select(MaterialInventory).where(MaterialInventory.material_id == case.material.id)))) == 1
        assert len(list(session.scalars(select(MaterialAuditEvent).where(MaterialAuditEvent.material_id == case.material.id)))) == 1


@pytest.mark.parametrize("change", ["name", "assignment", "reopen"])
def test_postgresql_inventory_rechecks_material_after_worker_delay(review_pg_case, change):
    case = review_pg_case
    if change == "reopen":
        with case.database.session() as session:
            session.get(PBRMaterial, case.material.id).workflow_status = "DONE"; session.commit()
    entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Review test worker was not released")
    case.inventory.callback = hold
    with case.client_for(1) as processor, case.client_for(0) as admin:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(processor.post, case.path + "/inventory/scan", json={"idempotency_key": str(uuid4()), "expected_generation": 0})
            try:
                assert entered.wait(15)
                if change == "name": response = admin.patch(case.path, json={"material_name": "Changed while scanning"})
                elif change == "assignment": response = admin.patch(case.path, json={"assigned_processor_id": str(case.users[2].id)})
                else: response = admin.post(case.path + "/reopen", json={"idempotency_key": str(uuid4()), "expected_generation": 0, "reason": "Review correction"})
                assert response.status_code == 200
            finally: release.set()
            assert pending.result(timeout=20).status_code == (404 if change == "assignment" else 409)
    with case.database.session() as session:
        assert list(session.scalars(select(MaterialInventory).where(MaterialInventory.material_id == case.material.id))) == []


def test_postgresql_inventory_and_events_are_append_only(review_pg_case):
    case = review_pg_case
    with case.client_for() as client:
        assert client.post(case.path + "/inventory/scan", json={"idempotency_key": str(uuid4()), "expected_generation": 0}).status_code == 200
    for table in ("material_inventories", "material_audit_events"):
        for statement in (f"UPDATE {table} SET generation = generation + 1 WHERE material_id = :id",
                          f"DELETE FROM {table} WHERE material_id = :id", f"TRUNCATE {table} CASCADE"):
            with pytest.raises(DBAPIError):
                with case.database.engine.begin() as connection:
                    connection.execute(text(statement), {"id": case.material.id})
    with case.database.session() as session:
        state = session.get(MaterialReviewState, case.material.id)
        assert state.generation == 1 and state.inventory_id is not None


def _prepare_pg_approval(case):
    from test_material_approvals import run
    with case.database.session() as session:
        session.get(PBRMaterial, case.material.id).workflow_status = "DONE"
        session.commit()
    with case.client_for() as client:
        response = run(client, case.path)
        assert response.status_code == 200
        return response.json()


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_concurrent_approvals_create_one_immutable_decision(review_pg_case, same_key):
    from app.db.models import MaterialApproval, MaterialTechnicalCheck
    from test_material_approvals import approval_payload
    case = review_pg_case; view = _prepare_pg_approval(case)
    barrier = Barrier(2); case.technical.callback = lambda: barrier.wait(timeout=15)
    payload = approval_payload(view)
    with case.client_for() as first, case.client_for() as second:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(first.post, case.path + "/approvals", json=payload)
            two = pool.submit(second.post, case.path + "/approvals", json=payload if same_key else approval_payload(view))
            results = [one.result(timeout=25), two.result(timeout=25)]
        assert sorted(item.status_code for item in results) == ([200, 200] if same_key else [200, 409])
        if same_key: assert results[0].json() == results[1].json()
    with case.database.session() as session:
        assert len(list(session.scalars(select(MaterialApproval).where(MaterialApproval.material_id == case.material.id)))) == 1
        assert len(list(session.scalars(select(MaterialTechnicalCheck).where(MaterialTechnicalCheck.material_id == case.material.id)))) == 2


@pytest.mark.parametrize("change", ["name", "assignment", "reopen", "disable"])
def test_postgresql_approval_rechecks_actual_api_changes_after_worker_delay(review_pg_case, change):
    from app.db.models import MaterialApproval
    from test_material_approvals import approval_payload
    case = review_pg_case; view = _prepare_pg_approval(case)
    entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Approval test worker was not released")
    case.technical.callback = hold
    with case.client_for() as approver, case.client_for(3) as admin:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(approver.post, case.path + "/approvals", json=approval_payload(view))
            try:
                assert entered.wait(15)
                if change == "name": response = admin.patch(case.path, json={"material_name": "Edited during approval"})
                elif change == "assignment": response = admin.patch(case.path, json={"assigned_processor_id": str(case.users[2].id)})
                elif change == "disable": response = admin.patch(f"/api/internal-users/{case.users[0].id}", json={"is_active": False})
                else: response = admin.post(case.path + "/reopen", json={"idempotency_key": str(uuid4()), "expected_generation": view["review"]["generation"], "reason": "Correct source"})
                assert response.status_code == 200
            finally: release.set()
            assert pending.result(timeout=25).status_code == (401 if change == "disable" else 409)
    with case.database.session() as session:
        assert list(session.scalars(select(MaterialApproval).where(MaterialApproval.material_id == case.material.id))) == []


def test_postgresql_approval_history_is_immutable_and_cannot_cross_revisions(review_pg_case):
    from app.db.models import MaterialApproval
    from test_material_approvals import approval_payload
    case = review_pg_case; view = _prepare_pg_approval(case)
    with case.client_for() as client:
        approved = client.post(case.path + "/approvals", json=approval_payload(view))
        assert approved.status_code == 200
        check_id = approved.json()["validation"]["id"]
    for table in ("material_technical_checks", "material_approvals"):
        for statement in (f"UPDATE {table} SET generation = generation + 1 WHERE material_id = :id",
                          f"DELETE FROM {table} WHERE material_id = :id", f"TRUNCATE {table} CASCADE"):
            with pytest.raises(DBAPIError):
                with case.database.engine.begin() as connection:
                    connection.execute(text(statement), {"id": case.material.id})
    with pytest.raises(IntegrityError), case.database.session() as session:
        session.add(MaterialApproval(material_id=case.material.id, actor_id=case.users[0].id, generation=999,
            revision_hash=view["review"]["revision_hash"], kind="TECHNICAL", technical_check_id=check_id, warnings_acknowledged=False))
        session.commit()


def test_postgresql_approval_upgrade_from_inventory_head_and_downgrade_preserve_history():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260915_0007")
            material_id = _create_postgresql_material_with_metadata(database_url, uuid4().hex[:12])
            with engine.begin() as connection:
                actor_id = connection.execute(text("SELECT assigned_processor_id FROM pbr_materials WHERE id=:id"), {"id": material_id}).scalar_one()
                connection.execute(text("INSERT INTO material_audit_events (id, material_id, actor_id, event_type, generation, result) VALUES (:id, :material, :actor, 'PRIOR_REVIEW', 0, '{}'::jsonb)"),
                                   {"id": uuid4(), "material": material_id, "actor": actor_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM material_audit_events WHERE material_id=:id"), {"id": material_id}).scalar_one() == 1
                assert connection.execute(text("SELECT count(*) FROM material_approvals")).scalar_one() == 0
            command.downgrade(config, "20260915_0007")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM material_audit_events WHERE material_id=:id"), {"id": material_id}).scalar_one() == 1
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


def test_postgresql_review_upgrade_from_auth_head_preserves_credentials():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260914_0006")
            user_id = uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO internal_users (id, display_name, email, role, is_active) VALUES (:id, 'Prior operator', 'prior@example.invalid', 'ADMIN', true)"), {"id": user_id})
                connection.execute(text("INSERT INTO user_credentials (user_id, password_hash, must_change_password) VALUES (:id, 'synthetic-preserved-hash', false)"), {"id": user_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            with engine.connect() as connection:
                assert connection.execute(text("SELECT password_hash FROM user_credentials WHERE user_id=:id"), {"id": user_id}).scalar_one() == "synthetic-preserved-hash"
                assert connection.execute(text("SELECT count(*) FROM material_review_states")).scalar_one() == 0
            command.downgrade(config, "20260914_0006")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM user_credentials WHERE user_id=:id"), {"id": user_id}).scalar_one() == 1
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


POSTGRES_TEST_ADMIN_URL = os.getenv("POSTGRES_TEST_ADMIN_URL")
if POSTGRES_TEST_ADMIN_URL is None and os.getenv("RUN_POSTGRES_TESTS") == "1":
    POSTGRES_TEST_ADMIN_URL = make_url(Settings().resolved_database_url).set(
        database="postgres"
    ).render_as_string(hide_password=False)
pytestmark = pytest.mark.skipif(
    POSTGRES_TEST_ADMIN_URL is None,
    reason="POSTGRES_TEST_ADMIN_URL is required for PostgreSQL integration tests",
)


@contextmanager
def isolated_postgresql_database():
    assert POSTGRES_TEST_ADMIN_URL is not None
    admin_url = make_url(POSTGRES_TEST_ADMIN_URL)
    database_name = f"reawote_material_test_{uuid4().hex}"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    database_url = admin_url.set(database=database_name)
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        yield database_url.render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def migrated_postgresql_url() -> str:
    with isolated_postgresql_database() as database_url:
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()
        try:
            config = Config("alembic.ini")
            command.upgrade(config, "head")
            yield database_url
        finally:
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
            get_settings.cache_clear()


def test_postgresql_alembic_upgrade_and_check(migrated_postgresql_url: str) -> None:
    config = Config("alembic.ini")

    # check_heads was added after our declared Alembic 1.16 minimum.
    command.current(config)
    command.heads(config)
    command.check(config)

    engine = create_engine(migrated_postgresql_url)
    try:
        schema = inspect(engine)
        assert schema.has_table(UserCredential.__tablename__)
        assert schema.has_table(AuthSession.__tablename__)
        assert schema.has_table("auth_login_rate_limits")
        session_indexes = {item["name"] for item in schema.get_indexes("auth_sessions")}
        assert {
            "ix_auth_sessions_user_id",
            "ix_auth_sessions_idle_expires_at",
            "ix_auth_sessions_absolute_expires_at",
            "ix_auth_sessions_revoked_at",
        } <= session_indexes
        assert any(
            foreign_key["referred_table"] == "internal_users"
            and foreign_key["constrained_columns"] == ["user_id"]
            for foreign_key in schema.get_foreign_keys("auth_sessions")
        )
        with engine.connect() as connection:
            assert set(MigrationContext.configure(connection).get_current_heads()) == set(
                ScriptDirectory.from_config(config).get_heads()
            )
            current_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert current_revision == "20260915_0008"
    finally:
        engine.dispose()


def test_postgresql_auth_upgrade_from_previous_head_preserves_users_without_credentials() -> None:
    with isolated_postgresql_database() as database_url:
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()
        engine = create_engine(database_url)
        existing_user_id = uuid4()
        try:
            config = Config("alembic.ini")
            command.upgrade(config, "20260909_0005")
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO internal_users "
                        "(id, display_name, email, role, is_active) "
                        "VALUES (:id, 'Existing User', 'existing@example.com', "
                        "'PROCESSOR', true)"
                    ),
                    {"id": existing_user_id},
                )

            command.upgrade(config, "head")
            command.current(config)
            command.heads(config)
            command.check(config)
            with engine.connect() as connection:
                assert set(MigrationContext.configure(connection).get_current_heads()) == set(
                    ScriptDirectory.from_config(config).get_heads()
                )
                assert connection.execute(
                    text("SELECT count(*) FROM internal_users WHERE id = :id"),
                    {"id": existing_user_id},
                ).scalar_one() == 1
                assert connection.execute(
                    text("SELECT count(*) FROM user_credentials")
                ).scalar_one() == 0
                assert connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "20260915_0008"
        finally:
            engine.dispose()
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
            get_settings.cache_clear()


def _postgresql_failed_login(database_url: str, email: str, settings: Settings) -> int:
    database = Database(database_url)
    try:
        application = create_app(settings, database)
        with TestClient(application) as client:
            return client.post(
                "/api/auth/login",
                json={"email": email, "password": "wrong concurrent password"},
                headers={"Origin": "http://localhost:5173"},
            ).status_code
    finally:
        database.dispose()


def test_postgresql_login_rate_limit_is_atomic_across_backend_instances(
    migrated_postgresql_url: str,
) -> None:
    suffix = uuid4().hex
    email = f"rate-limit-{suffix}@example.com"
    settings = Settings(
        database_url=migrated_postgresql_url,
        auth_rate_limit_attempts=3,
        auth_rate_limit_window_seconds=300,
    )
    password_service = PasswordService(settings)
    engine = create_engine(migrated_postgresql_url)
    with Session(engine) as session:
        user = InternalUser(
            display_name="Rate Limit User",
            email=email,
            role="ADMIN",
        )
        session.add(user)
        session.flush()
        session.add(
            UserCredential(
                user_id=user.id,
                password_hash=password_service.hash_password(
                    "valid concurrent account password"
                ),
                must_change_password=False,
            )
        )
        session.commit()
    engine.dispose()

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(
            executor.map(
                lambda _: _postgresql_failed_login(
                    migrated_postgresql_url, email, settings
                ),
                range(8),
            )
        )

    assert sorted(statuses) == [401, 401, 401, 429, 429, 429, 429, 429]


class _ConcurrentPreflightWorker:
    def __init__(self, barrier: Barrier, material_identity: str) -> None:
        self._barrier = barrier
        wire_response = WorkerMaterialPreflightResponse.model_validate_json(
            json.dumps(
                {
                    "schema_version": 1,
                    "folder_path": f"library/{material_identity}",
                    "folder_name": material_identity,
                    "master_resolution": "16K",
                    "master_last_modified_at": "2026-03-04T00:00:00.000000000+00:00",
                    "policy": "CURRENT_ON_OR_AFTER_2026_03_04",
                    "metadata": {
                        "status": "VALID",
                        "source_file_name": "metadata.txt",
                        "sha256": "b" * 64,
                        "raw_content": "texture size: 20x30 cm",
                        "hex_color": "#A1B2C3",
                        "width_cm": "20.0000",
                        "height_cm": "30.0000",
                        "warnings": [],
                        "errors": [],
                    },
                    "warnings": [],
                    "errors": [],
                    "can_continue": True,
                }
            ),
            strict=True,
        )
        self._response = wire_response.to_internal()

    def preflight(self, folder_path: str) -> WorkerMaterialPreflight:
        assert folder_path
        self._barrier.wait(timeout=10)
        return self._response


def _concurrent_mark_done_request(
    database_url: str,
    material_id: object,
    worker: _ConcurrentPreflightWorker,
) -> tuple[int, dict[str, object]]:
    database = Database(database_url)
    try:
        application = create_app(Settings(database_url=database_url), database, worker)
        with TestClient(application) as client:
            response = client.post(f"/api/materials/{material_id}/mark-done")
            return response.status_code, response.json()
    finally:
        database.dispose()


def _setup_prefix_race(migrated_postgresql_url: str) -> dict[str, object]:
    setup_engine = create_engine(migrated_postgresql_url)
    suffix = uuid4().hex[:12]
    old_prefix = f"OLD{suffix.upper()}"
    new_prefix = f"NEW{suffix.upper()}"
    with Session(setup_engine) as session:
        company = Company(name=f"Prefix race {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"PREFIX-{suffix}",
            name="Prefix race",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Prefix race brand",
            folder_prefix=old_prefix,
            brand_identifier=f"prefix-race-{suffix}",
        )
        processor = InternalUser(
            display_name="Prefix race processor",
            email=f"prefix-race-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.commit()
        result: dict[str, object] = {
            "project_id": project.id,
            "brand_id": brand.id,
            "processor_id": processor.id,
            "old_prefix": old_prefix,
            "new_prefix": new_prefix,
        }
    setup_engine.dispose()
    return result


def _request_holding_brand_lock(
    database_url: str,
    method: str,
    path: str,
    payload: dict[str, object],
    lock_acquired: Event,
    release_lock: Event,
) -> tuple[int, dict[str, object]]:
    database = Database(database_url)

    def hold_after_lock(*args: object) -> None:
        statement = str(args[2])
        if "FOR UPDATE" in statement.upper():
            lock_acquired.set()
            if not release_lock.wait(timeout=10):
                raise TimeoutError("Timed out while holding the PublishedBrand row lock.")

    event.listen(database.engine, "after_cursor_execute", hold_after_lock)
    try:
        application = create_app(Settings(database_url=database_url), database)
        with TestClient(application) as client:
            response = client.request(method, path, json=payload)
            return response.status_code, response.json()
    finally:
        database.dispose()


def _request_waiting_for_row_lock(
    database_url: str,
    method: str,
    path: str,
    payload: dict[str, object],
    lock_attempted: Event,
    lock_acquired: Event,
) -> tuple[int, dict[str, object]]:
    database = Database(database_url)

    def record_lock_attempt(*args: object) -> None:
        statement = str(args[2])
        if "FOR UPDATE" in statement.upper():
            lock_attempted.set()

    def record_lock_acquisition(*args: object) -> None:
        statement = str(args[2])
        if "FOR UPDATE" in statement.upper():
            lock_acquired.set()

    event.listen(database.engine, "before_cursor_execute", record_lock_attempt)
    event.listen(database.engine, "after_cursor_execute", record_lock_acquisition)
    try:
        application = create_app(Settings(database_url=database_url), database)
        with TestClient(application) as client:
            response = client.request(method, path, json=payload)
            return response.status_code, response.json()
    finally:
        database.dispose()


def _assert_prefix_race_database_state(
    database_url: str,
    context: dict[str, object],
    expected_prefix: object,
) -> None:
    setup_engine = create_engine(database_url)
    with Session(setup_engine) as session:
        stored_brand = session.get(PublishedBrand, context["brand_id"])
        assert stored_brand is not None
        assert stored_brand.folder_prefix == expected_prefix
        materials = list(
            session.scalars(
                select(PBRMaterial).where(
                    PBRMaterial.published_brand_id == context["brand_id"]
                )
            )
        )
        assert len(materials) == 1
        assert materials[0].sequence_number == 1
        assert materials[0].technical_identity == f"{expected_prefix}_0001_G03"
    setup_engine.dispose()


def test_prefix_patch_first_serializes_material_creation(
    migrated_postgresql_url: str,
) -> None:
    context = _setup_prefix_race(migrated_postgresql_url)
    brand_path = f"/api/brands/{context['brand_id']}"
    material_payload = {
        "project_id": str(context["project_id"]),
        "published_brand_id": str(context["brand_id"]),
        "material_name": "Material after prefix update",
        "main_category_code": "G03",
        "assigned_processor_id": str(context["processor_id"]),
    }
    first_locked = Event()
    release_first = Event()
    second_attempted = Event()
    second_acquired = Event()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            _request_holding_brand_lock,
            migrated_postgresql_url,
            "PATCH",
            brand_path,
            {"folder_prefix": context["new_prefix"]},
            first_locked,
            release_first,
        )
        try:
            assert first_locked.wait(timeout=10)
            second_future = executor.submit(
                _request_waiting_for_row_lock,
                migrated_postgresql_url,
                "POST",
                "/api/materials",
                material_payload,
                second_attempted,
                second_acquired,
            )
            assert second_attempted.wait(timeout=10)
            assert not second_acquired.wait(timeout=0.25)
        finally:
            release_first.set()
        patch_result = first_future.result(timeout=10)
        post_result = second_future.result(timeout=10)

    assert patch_result[0] == 200
    assert patch_result[1]["folder_prefix"] == context["new_prefix"]
    assert post_result[0] == 201
    assert post_result[1]["technical_identity"] == (
        f"{context['new_prefix']}_0001_G03"
    )
    _assert_prefix_race_database_state(
        migrated_postgresql_url,
        context,
        context["new_prefix"],
    )


def test_material_creation_first_blocks_prefix_patch_and_returns_conflict(
    migrated_postgresql_url: str,
) -> None:
    context = _setup_prefix_race(migrated_postgresql_url)
    brand_path = f"/api/brands/{context['brand_id']}"
    material_payload = {
        "project_id": str(context["project_id"]),
        "published_brand_id": str(context["brand_id"]),
        "material_name": "Material before prefix update",
        "main_category_code": "G03",
        "assigned_processor_id": str(context["processor_id"]),
    }
    first_locked = Event()
    release_first = Event()
    second_attempted = Event()
    second_acquired = Event()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            _request_holding_brand_lock,
            migrated_postgresql_url,
            "POST",
            "/api/materials",
            material_payload,
            first_locked,
            release_first,
        )
        try:
            assert first_locked.wait(timeout=10)
            second_future = executor.submit(
                _request_waiting_for_row_lock,
                migrated_postgresql_url,
                "PATCH",
                brand_path,
                {"folder_prefix": context["new_prefix"]},
                second_attempted,
                second_acquired,
            )
            assert second_attempted.wait(timeout=10)
            assert not second_acquired.wait(timeout=0.25)
        finally:
            release_first.set()
        post_result = first_future.result(timeout=10)
        patch_result = second_future.result(timeout=10)

    assert post_result[0] == 201
    assert post_result[1]["technical_identity"] == (
        f"{context['old_prefix']}_0001_G03"
    )
    assert patch_result == (
        409,
        {"detail": "folder_prefix cannot be changed after materials have been created."},
    )
    _assert_prefix_race_database_state(
        migrated_postgresql_url,
        context,
        context["old_prefix"],
    )


def test_concurrent_material_creation_allocates_distinct_numbers(
    migrated_postgresql_url: str,
) -> None:
    setup_engine = create_engine(migrated_postgresql_url)
    suffix = uuid4().hex
    with Session(setup_engine) as session:
        company = Company(name=f"Concurrency {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"CONCURRENT-{suffix}",
            name="Concurrent allocation",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Concurrent brand",
            folder_prefix=f"CONCURRENT{suffix.upper()}",
            brand_identifier=f"concurrent-{suffix}",
        )
        processor = InternalUser(
            display_name="Concurrent processor",
            email=f"concurrent-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.commit()
        project_id = project.id
        brand_id = brand.id
        processor_id = processor.id

    barrier = Barrier(2)

    def create_one(index: int) -> tuple[int, dict[str, object]]:
        database = Database(migrated_postgresql_url)
        application = create_app(Settings(database_url=migrated_postgresql_url), database)
        with TestClient(application) as client:
            barrier.wait(timeout=10)
            response = client.post(
                "/api/materials",
                json={
                    "project_id": str(project_id),
                    "published_brand_id": str(brand_id),
                    "material_name": f"Concurrent material {index}",
                    "main_category_code": "G03",
                    "assigned_processor_id": str(processor_id),
                },
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_one, (1, 2)))

    assert [status_code for status_code, _ in results] == [201, 201]
    materials = [body for _, body in results]
    assert {material["sequence_number"] for material in materials} == {1, 2}
    assert len({material["technical_identity"] for material in materials}) == 2

    with Session(setup_engine) as session:
        stored_brand = session.get(PublishedBrand, brand_id)
        assert stored_brand is not None
        assert stored_brand.next_sequence_number == 3
        stored_numbers = set(
            session.scalars(
                select(PBRMaterial.sequence_number).where(
                    PBRMaterial.published_brand_id == brand_id
                )
            )
        )
        assert stored_numbers == {1, 2}
    setup_engine.dispose()


def _setup_material_path_race(migrated_postgresql_url: str) -> dict[str, object]:
    setup_engine = create_engine(migrated_postgresql_url)
    suffix = uuid4().hex
    prefix = f"PATHRACE{suffix.upper()}"
    with Session(setup_engine) as session:
        company = Company(name=f"Material path race {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"PATH-RACE-{suffix}",
            name="Material path race",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Material path race brand",
            folder_prefix=prefix,
            brand_identifier=f"material-path-race-{suffix}",
            next_sequence_number=2,
        )
        processor = InternalUser(
            display_name="Material path race processor",
            email=f"material-path-race-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.flush()
        material = PBRMaterial(
            project_id=project.id,
            published_brand_id=brand.id,
            sequence_number=1,
            material_name="Material path race",
            main_category_code="G03",
            assigned_processor_id=processor.id,
            technical_identity=f"{prefix}_0001_G03",
        )
        session.add(material)
        session.commit()
        context: dict[str, object] = {
            "material_id": material.id,
            "technical_identity": material.technical_identity,
            "folder_path": f"materials/{prefix}_0001_G03",
        }
    setup_engine.dispose()
    return context


def _simulate_future_system_link_transaction(
    database_url: str,
    material_id: object,
    folder_path: str,
    lock_acquired: Event,
    release_lock: Event,
) -> None:
    """Simulate the future filesystem-verified link operation at the DB boundary."""
    link_engine = create_engine(database_url)
    try:
        with Session(link_engine) as session:
            material = session.scalar(
                select(PBRMaterial)
                .where(PBRMaterial.id == material_id)
                .with_for_update()
            )
            assert material is not None
            material.folder_path = folder_path
            session.flush()
            lock_acquired.set()
            if not release_lock.wait(timeout=10):
                raise TimeoutError("Timed out while holding the PBRMaterial row lock.")
            session.commit()
    finally:
        link_engine.dispose()


def test_concurrent_system_link_and_category_patch_use_locked_current_state(
    migrated_postgresql_url: str,
) -> None:
    context = _setup_material_path_race(migrated_postgresql_url)
    material_path = f"/api/materials/{context['material_id']}"
    link_locked = Event()
    release_link = Event()
    patch_attempted = Event()
    patch_acquired = Event()

    with ThreadPoolExecutor(max_workers=2) as executor:
        link_future = executor.submit(
            _simulate_future_system_link_transaction,
            migrated_postgresql_url,
            context["material_id"],
            context["folder_path"],
            link_locked,
            release_link,
        )
        try:
            assert link_locked.wait(timeout=10)
            patch_future = executor.submit(
                _request_waiting_for_row_lock,
                migrated_postgresql_url,
                "PATCH",
                material_path,
                {"main_category_code": "G04"},
                patch_attempted,
                patch_acquired,
            )
            assert patch_attempted.wait(timeout=10)
            assert not patch_acquired.wait(timeout=0.25)
        finally:
            release_link.set()
        link_future.result(timeout=10)
        patch_result = patch_future.result(timeout=10)

    assert patch_acquired.is_set()
    assert patch_result == (
        409,
        {"detail": "main_category_code cannot be changed while folder_path is set."},
    )

    database = Database(migrated_postgresql_url)
    application = create_app(Settings(database_url=migrated_postgresql_url), database)
    try:
        with TestClient(application) as client:
            stored_response = client.get(material_path)
            follow_up_response = client.patch(
                material_path,
                json={"material_name": "Transaction remains usable"},
            )
    finally:
        database.dispose()

    assert stored_response.status_code == 200
    stored = stored_response.json()
    assert stored["main_category_code"] == "G03"
    assert stored["technical_identity"] == context["technical_identity"]
    assert stored["folder_path"] == context["folder_path"]
    assert not (
        stored["technical_identity"].endswith("_G04")
        and stored["folder_path"].endswith("_G03")
    )
    assert follow_up_response.status_code == 200


def test_sequence_9999_is_allocated_then_returns_controlled_conflict(
    migrated_postgresql_url: str,
) -> None:
    setup_engine = create_engine(migrated_postgresql_url)
    suffix = uuid4().hex
    with Session(setup_engine) as session:
        company = Company(name=f"Boundary {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"BOUNDARY-{suffix}",
            name="Boundary allocation",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Boundary brand",
            folder_prefix=f"BOUNDARY{suffix.upper()}",
            brand_identifier=f"boundary-{suffix}",
            next_sequence_number=9999,
        )
        processor = InternalUser(
            display_name="Boundary processor",
            email=f"boundary-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.commit()
        project_id = project.id
        brand_id = brand.id
        processor_id = processor.id

    database = Database(migrated_postgresql_url)
    application = create_app(Settings(database_url=migrated_postgresql_url), database)
    payload = {
        "project_id": str(project_id),
        "published_brand_id": str(brand_id),
        "material_name": "Final sequence material",
        "main_category_code": "G03",
        "assigned_processor_id": str(processor_id),
    }
    with TestClient(application) as client:
        created_response = client.post("/api/materials", json=payload)
        brand_response = client.get(f"/api/brands/{brand_id}")
        exhausted_response = client.post("/api/materials", json=payload)

    assert created_response.status_code == 201
    assert created_response.json()["sequence_number"] == 9999
    assert created_response.json()["technical_identity"].endswith("_9999_G03")
    assert brand_response.status_code == 200
    assert brand_response.json()["next_sequence_number"] == 10000
    assert exhausted_response.status_code == 409
    assert exhausted_response.json()["detail"] == "Published brand sequence is exhausted."

    with Session(setup_engine) as session:
        stored_numbers = list(
            session.scalars(
                select(PBRMaterial.sequence_number).where(
                    PBRMaterial.published_brand_id == brand_id
                )
            )
        )
        assert stored_numbers == [9999]
    setup_engine.dispose()


def test_postgresql_metadata_schema_uses_exact_and_structured_types(
    migrated_postgresql_url: str,
) -> None:
    engine = create_engine(migrated_postgresql_url)
    schema = inspect(engine)

    current_columns = {
        column["name"]: column for column in schema.get_columns("pbr_material_metadata")
    }
    snapshot_columns = {
        column["name"]: column
        for column in schema.get_columns("pbr_material_metadata_snapshots")
    }

    for columns in (current_columns, snapshot_columns):
        assert columns["width_cm"]["type"].precision == 12
        assert columns["width_cm"]["type"].scale == 4
        assert columns["height_cm"]["type"].precision == 12
        assert columns["height_cm"]["type"].scale == 4
        assert isinstance(columns["warnings"]["type"], JSONB)
        assert columns["source_content"]["type"].__class__.__name__ == "TEXT"

    assert snapshot_columns["sequence_number"]["type"].__class__.__name__ == "BIGINT"
    for table_name in (
        "pbr_material_metadata",
        "pbr_material_metadata_snapshots",
    ):
        assert {
            constraint["name"]
            for constraint in schema.get_check_constraints(table_name)
        } >= {
            f"ck_{table_name}_status",
            f"ck_{table_name}_source_sha256",
            f"ck_{table_name}_width_cm_positive",
            f"ck_{table_name}_height_cm_positive",
            f"ck_{table_name}_warnings",
        }

    snapshot_material_fk = next(
        foreign_key
        for foreign_key in schema.get_foreign_keys("pbr_material_metadata_snapshots")
        if foreign_key["constrained_columns"] == ["material_id"]
    )
    assert snapshot_material_fk["options"]["ondelete"] == "RESTRICT"
    engine.dispose()


def _create_postgresql_material_with_metadata(database_url: str, suffix: str) -> object:
    engine = create_engine(database_url)
    with Session(engine) as session:
        company = Company(name=f"Metadata PostgreSQL {suffix}")
        session.add(company)
        session.flush()
        project = Project(
            company_id=company.id,
            project_number=f"META-PG-{suffix}",
            name="Metadata PostgreSQL",
        )
        brand = PublishedBrand(
            company_id=company.id,
            name="Metadata PostgreSQL brand",
            folder_prefix=f"METAPG{suffix.upper()}",
            brand_identifier=f"metadata-pg-{suffix}",
            next_sequence_number=2,
        )
        processor = InternalUser(
            display_name="Metadata PostgreSQL processor",
            email=f"metadata-pg-{suffix}@example.com",
            role="PROCESSOR",
        )
        session.add_all([project, brand, processor])
        session.flush()
        material = PBRMaterial(
            project_id=project.id,
            published_brand_id=brand.id,
            sequence_number=1,
            material_name="Metadata PostgreSQL material",
            main_category_code="G03",
            assigned_processor_id=processor.id,
            technical_identity=f"METAPG{suffix.upper()}_0001_G03",
        )
        material.metadata_state = PBRMaterialMetadata()
        session.add(material)
        session.commit()
        material_id = material.id
    engine.dispose()
    return material_id


def test_concurrent_mark_done_creates_one_atomic_snapshot(
    migrated_postgresql_url: str,
) -> None:
    material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        uuid4().hex[:12],
    )
    engine = create_engine(migrated_postgresql_url)
    with Session(engine) as session:
        material = session.get(PBRMaterial, material_id)
        assert material is not None
        material.folder_path = f"library/{material.technical_identity}"
        identity = material.technical_identity
        session.commit()
    engine.dispose()

    worker = _ConcurrentPreflightWorker(Barrier(2), identity)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_mark_done_request,
                migrated_postgresql_url,
                material_id,
                worker,
            )
            for _ in range(2)
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(result[0] for result in results) == [200, 409]
    engine = create_engine(migrated_postgresql_url)
    with Session(engine) as session:
        material = session.get(PBRMaterial, material_id)
        current = session.get(PBRMaterialMetadata, material_id)
        snapshots = list(
            session.scalars(
                select(PBRMaterialMetadataSnapshot).where(
                    PBRMaterialMetadataSnapshot.material_id == material_id
                )
            )
        )
        assert len(snapshots) == 1
        assert material is not None and material.workflow_status == "DONE"
        assert current is not None and current.current_snapshot_id == snapshots[0].id
        assert snapshots[0].sequence_number == 1
    engine.dispose()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("status", "UNKNOWN"),
        ("source_filename", "folder/metadata.txt"),
        ("source_sha256", "A" * 64),
        ("source_sha256", "f" * 63),
        ("hex_color", "#abcdef"),
        ("width_cm", Decimal("0")),
        ("height_cm", Decimal("-1")),
        ("master_resolution", "016K"),
    ],
)
def test_postgresql_enforces_metadata_value_constraints(
    migrated_postgresql_url: str,
    column: str,
    value: object,
) -> None:
    suffix = uuid4().hex[:12]
    material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        suffix,
    )
    engine = create_engine(migrated_postgresql_url)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(f"UPDATE pbr_material_metadata SET {column} = :value WHERE material_id = :id"),
            {"value": value, "id": material_id},
        )

    engine.dispose()


def test_postgresql_enforces_snapshot_order_and_current_snapshot_ownership(
    migrated_postgresql_url: str,
) -> None:
    first_material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        uuid4().hex[:12],
    )
    second_material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        uuid4().hex[:12],
    )
    engine = create_engine(migrated_postgresql_url)
    with Session(engine) as session:
        first = PBRMaterialMetadataSnapshot(
            material_id=first_material_id,
            sequence_number=1,
        )
        second = PBRMaterialMetadataSnapshot(
            material_id=second_material_id,
            sequence_number=1,
        )
        session.add_all([first, second])
        session.commit()
        second_snapshot_id = second.id

    with Session(engine) as session:
        session.add(
            PBRMaterialMetadataSnapshot(
                material_id=first_material_id,
                sequence_number=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with Session(engine) as session:
        current = session.get(PBRMaterialMetadata, first_material_id)
        assert current is not None
        current.current_snapshot_id = second_snapshot_id
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    engine.dispose()


def test_postgresql_metadata_migration_backfills_existing_materials() -> None:
    with isolated_postgresql_database() as database_url:
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()
        engine = create_engine(database_url)
        material_id = uuid4()
        company_id = uuid4()
        project_id = uuid4()
        brand_id = uuid4()
        processor_id = uuid4()
        try:
            config = Config("alembic.ini")
            command.upgrade(config, "20260908_0004")
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO companies (id, name) VALUES (:id, 'Backfill company')"),
                    {"id": company_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO projects (id, company_id, project_number, name) "
                        "VALUES (:id, :company_id, 'BACKFILL-1', 'Backfill project')"
                    ),
                    {"id": project_id, "company_id": company_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO published_brands "
                        "(id, company_id, name, folder_prefix, brand_identifier) "
                        "VALUES (:id, :company_id, 'Backfill brand', 'BACKFILL', 'backfill')"
                    ),
                    {"id": brand_id, "company_id": company_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO internal_users (id, display_name, email, role) "
                        "VALUES (:id, 'Backfill processor', 'backfill@example.com', 'PROCESSOR')"
                    ),
                    {"id": processor_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO pbr_materials "
                        "(id, project_id, published_brand_id, sequence_number, material_name, "
                        "main_category_code, assigned_processor_id, technical_identity) "
                        "VALUES (:id, :project_id, :brand_id, 1, 'Backfill material', "
                        "'G03', :processor_id, 'BACKFILL_0001_G03')"
                    ),
                    {
                        "id": material_id,
                        "project_id": project_id,
                        "brand_id": brand_id,
                        "processor_id": processor_id,
                    },
                )

            command.upgrade(config, "head")
            command.check(config)
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT status, current_snapshot_id, warnings "
                        "FROM pbr_material_metadata WHERE material_id = :id"
                    ),
                    {"id": material_id},
                ).one()
                assert row.status == "NOT_SCANNED"
                assert row.current_snapshot_id is None
                assert row.warnings == []
        finally:
            engine.dispose()
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
            get_settings.cache_clear()


def test_postgresql_current_metadata_remains_updatable(
    migrated_postgresql_url: str,
) -> None:
    material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        uuid4().hex[:12],
    )
    engine = create_engine(migrated_postgresql_url)
    valid_warnings = [{"code": "UPDATED", "message": "Current state changed."}]

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE pbr_material_metadata SET status = 'WARNING', "
                "hex_color = '#A1B2C3', warnings = CAST(:warnings AS jsonb) "
                "WHERE material_id = :material_id"
            ),
            {"warnings": json.dumps(valid_warnings), "material_id": material_id},
        )

    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT status, hex_color, warnings FROM pbr_material_metadata "
                "WHERE material_id = :material_id"
            ),
            {"material_id": material_id},
        ).one()
    assert stored.status == "WARNING"
    assert stored.hex_color == "#A1B2C3"
    assert stored.warnings == valid_warnings
    engine.dispose()


def test_postgresql_snapshot_trigger_rejects_direct_update_and_delete(
    migrated_postgresql_url: str,
) -> None:
    material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        uuid4().hex[:12],
    )
    engine = create_engine(migrated_postgresql_url)
    original_warnings = [{"code": "ORIGINAL", "message": "Original warning."}]
    with Session(engine) as session:
        snapshot = PBRMaterialMetadataSnapshot(
            material_id=material_id,
            sequence_number=1,
            hex_color="#A1B2C3",
            warnings=original_warnings,
        )
        session.add(snapshot)
        session.commit()
        snapshot_id = snapshot.id

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE pbr_material_metadata_snapshots "
                "SET hex_color = '#D4E5F6' WHERE id = :snapshot_id"
            ),
            {"snapshot_id": snapshot_id},
        )

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(
            text("DELETE FROM pbr_material_metadata_snapshots WHERE id = :snapshot_id"),
            {"snapshot_id": snapshot_id},
        )

    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as connection:
        connection.execute(text("TRUNCATE pbr_material_metadata_snapshots CASCADE"))

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("DELETE FROM pbr_materials WHERE id = :material_id"),
            {"material_id": material_id},
        )

    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT sequence_number, hex_color, warnings "
                "FROM pbr_material_metadata_snapshots WHERE id = :snapshot_id"
            ),
            {"snapshot_id": snapshot_id},
        ).one()
    assert stored.sequence_number == 1
    assert stored.hex_color == "#A1B2C3"
    assert stored.warnings == original_warnings

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO pbr_material_metadata_snapshots "
                "(id, material_id, sequence_number) VALUES (:id, :material_id, 1)"
            ),
            {"id": uuid4(), "material_id": material_id},
        )
    engine.dispose()


def _write_postgresql_warnings(
    engine: Engine,
    table_name: str,
    material_id: object,
    warnings: object,
) -> None:
    encoded_warnings = json.dumps(warnings)
    with engine.begin() as connection:
        if table_name == "pbr_material_metadata":
            connection.execute(
                text(
                    "UPDATE pbr_material_metadata "
                    "SET warnings = CAST(:warnings AS jsonb) WHERE material_id = :material_id"
                ),
                {"warnings": encoded_warnings, "material_id": material_id},
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO pbr_material_metadata_snapshots "
                    "(id, material_id, sequence_number, warnings) "
                    "VALUES (:id, :material_id, 1, CAST(:warnings AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "material_id": material_id,
                    "warnings": encoded_warnings,
                },
            )


@pytest.mark.parametrize(
    "invalid_warnings",
    [
        pytest.param({"code": "OBJECT", "message": "Not an array."}, id="object"),
        pytest.param("not-an-array", id="string"),
        pytest.param(42, id="number"),
        pytest.param(None, id="json-null"),
        pytest.param(["not-an-object"], id="non-object-item"),
        pytest.param([{"code": "", "message": "Empty code."}], id="empty-code"),
        pytest.param([{"code": "\t", "message": "Whitespace code."}], id="blank-code"),
        pytest.param([{"code": "MISSING_MESSAGE"}], id="missing-message"),
        pytest.param([{"code": "BLANK_MESSAGE", "message": "\n"}], id="blank-message"),
        pytest.param(
            [{"code": "EXTRA", "message": "Unknown field.", "unsafe": True}],
            id="unknown-field",
        ),
    ],
)
@pytest.mark.parametrize(
    "table_name",
    ["pbr_material_metadata", "pbr_material_metadata_snapshots"],
)
def test_postgresql_rejects_invalid_warnings_for_current_and_snapshot_metadata(
    migrated_postgresql_url: str,
    table_name: str,
    invalid_warnings: object,
) -> None:
    material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        uuid4().hex[:12],
    )
    engine = create_engine(migrated_postgresql_url)

    with pytest.raises(IntegrityError):
        _write_postgresql_warnings(engine, table_name, material_id, invalid_warnings)

    with engine.connect() as connection:
        current_warnings = connection.execute(
            text("SELECT warnings FROM pbr_material_metadata WHERE material_id = :material_id"),
            {"material_id": material_id},
        ).scalar_one()
        snapshot_count = connection.execute(
            text(
                "SELECT count(*) FROM pbr_material_metadata_snapshots "
                "WHERE material_id = :material_id"
            ),
            {"material_id": material_id},
        ).scalar_one()
    assert current_warnings == []
    assert snapshot_count == 0
    engine.dispose()


@pytest.mark.parametrize(
    ("valid_warnings", "expected_warnings"),
    [
        pytest.param([], [], id="empty-array"),
        pytest.param(
            [
                {"code": "VALID", "message": "Valid warning."},
                {"code": "WITH_PATH", "message": "Valid path.", "path": "metadata.txt"},
            ],
            [
                {"code": "VALID", "message": "Valid warning.", "path": None},
                {"code": "WITH_PATH", "message": "Valid path.", "path": "metadata.txt"},
            ],
            id="warning-objects",
        ),
    ],
)
@pytest.mark.parametrize(
    "table_name",
    ["pbr_material_metadata", "pbr_material_metadata_snapshots"],
)
def test_postgresql_accepts_contract_valid_warnings(
    migrated_postgresql_url: str,
    table_name: str,
    valid_warnings: object,
    expected_warnings: object,
) -> None:
    material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        uuid4().hex[:12],
    )
    engine = create_engine(migrated_postgresql_url)

    _write_postgresql_warnings(engine, table_name, material_id, valid_warnings)

    database = Database(migrated_postgresql_url)
    application = create_app(Settings(database_url=migrated_postgresql_url), database)
    try:
        with TestClient(application) as client:
            if table_name == "pbr_material_metadata":
                response = client.get(f"/api/materials/{material_id}/metadata")
                assert response.status_code == 200
                assert response.json()["warnings"] == expected_warnings
            else:
                response = client.get(f"/api/materials/{material_id}/metadata/snapshots")
                assert response.status_code == 200
                assert response.json()[0]["warnings"] == expected_warnings
    finally:
        database.dispose()
        engine.dispose()


def test_postgresql_rejected_warnings_leave_future_get_response_valid(
    migrated_postgresql_url: str,
) -> None:
    material_id = _create_postgresql_material_with_metadata(
        migrated_postgresql_url,
        uuid4().hex[:12],
    )
    engine = create_engine(migrated_postgresql_url)
    with pytest.raises(IntegrityError):
        _write_postgresql_warnings(
            engine,
            "pbr_material_metadata",
            material_id,
            {"code": "OBJECT", "message": "Not an array."},
        )

    database = Database(migrated_postgresql_url)
    application = create_app(Settings(database_url=migrated_postgresql_url), database)
    try:
        with TestClient(application) as client:
            response = client.get(f"/api/materials/{material_id}/metadata")
        assert response.status_code == 200
        assert response.json()["warnings"] == []
    finally:
        database.dispose()
        engine.dispose()


def test_postgresql_metadata_fresh_upgrade_and_downgrade() -> None:
    with isolated_postgresql_database() as database_url:
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini")
            command.upgrade(config, "20260909_0005")
            upgraded_schema = inspect(engine)
            assert upgraded_schema.has_table("pbr_material_metadata")
            assert upgraded_schema.has_table("pbr_material_metadata_snapshots")
            with engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT count(*) FROM pg_trigger "
                        "WHERE tgname = "
                        "'trg_pbr_material_metadata_snapshots_reject_update_delete'"
                    )
                ).scalar_one() == 1

            command.downgrade(config, "20260908_0004")
            downgraded_schema = inspect(engine)
            assert not downgraded_schema.has_table("pbr_material_metadata")
            assert not downgraded_schema.has_table("pbr_material_metadata_snapshots")
            with engine.connect() as connection:
                remaining_functions = connection.execute(
                    text(
                        "SELECT proname FROM pg_proc WHERE proname IN "
                        "('pbr_material_metadata_warnings_are_valid', "
                        "'pbr_material_metadata_snapshots_reject_mutation')"
                    )
                ).scalars()
                assert list(remaining_functions) == []
        finally:
            engine.dispose()
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
            get_settings.cache_clear()
