import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import UUID, uuid4

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
    yield from _review_pg_case(migrated_postgresql_url)


def _review_pg_case(migrated_postgresql_url):
    from types import SimpleNamespace
    from app.main import create_app as authenticated_app
    from test_material_review import InventoryStub
    from test_material_approvals import TechnicalStub
    from test_material_identity import IdentityStub
    from test_previews import PreviewStub
    from test_packaging_reservations import PreparationStub
    from test_application_access import PASSWORD, ORIGIN

    class SharedDatabase(Database):
        def dispose(self): pass
    database = SharedDatabase(migrated_postgresql_url)
    suffix = uuid4().hex
    settings = Settings(_env_file=None, database_url=migrated_postgresql_url, cors_origins=ORIGIN, auth_rate_limit_attempts=100,
        gcs_bucket_name="synthetic-reawote-staging", gcs_staging_prefix="isolated/contracts")
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
    identity = IdentityStub()
    previews = PreviewStub()
    packaging = PreparationStub()
    app = authenticated_app(settings.model_copy(update={"source_mutations_enabled": True, "packaging_enabled": True, "app_env": "test"}), database,
                            inventory_client=inventory, technical_client=technical, identity_client=identity, preview_client=previews,
                            packaging_client=packaging)
    def client_for(index=0):
        client = TestClient(app, base_url=ORIGIN)
        response = client.post("/api/auth/login", json={"email": users[index].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        assert response.status_code == 200
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
        return client
    yield SimpleNamespace(database=database, app=app, inventory=inventory, technical=technical, identity=identity, previews=previews, packaging=packaging, users=users, material=material,
                          path=f"/api/materials/{material.id}", client_for=client_for)
    database.engine.dispose()


@pytest.mark.parametrize("operation", ["listing", "image"])
@pytest.mark.parametrize("change,code", [("assignment", 404), ("disable", 401), ("identity", 409)])
def test_postgresql_preview_reauthorizes_after_actual_concurrent_api_change(review_pg_case, operation, change, code):
    from test_material_identity import prepare
    from app.identity_client import IdentityClientError
    case = review_pg_case; entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Preview test worker was not released")
    case.previews.callback = hold
    with case.client_for(1) as reader, case.client_for() as admin:
        target = _identity_target(case) if change == "identity" else None
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(reader.get, case.path + ("/previews" if operation == "listing" else "/preview"),
                params={} if operation == "listing" else {"name": "Synthetic preview.png", "expected_sha256": "a" * 64})
            try:
                assert entered.wait(15)
                if change == "assignment": response = admin.patch(case.path, json={"assigned_processor_id": str(case.users[2].id)})
                elif change == "disable": response = admin.patch(f"/api/internal-users/{case.users[1].id}", json={"is_active": False})
                else:
                    payload = prepare(admin, case.path, target); case.identity.failure = IdentityClientError()
                    response = admin.post(case.path + "/identity-confirm", json=payload)
                    assert response.json()["status"] == "RUNNING"
                assert response.status_code == 200
            finally: release.set()
            result = pending.result(timeout=25)
            assert result.status_code == code and result.headers["content-type"] == "application/json"
        if change == "identity":
            calls = len(case.previews.calls)
            assert reader.get(case.path + "/previews").status_code == 409
            assert len(case.previews.calls) == calls


@pytest.mark.parametrize("change", ["material", "brand"])
def test_postgresql_publication_preview_freezes_inputs_until_competing_edit_commits(review_pg_case, monkeypatch, change):
    from app import publication_preflight
    from test_publication_preflight import prepare_candidate, preview
    case = review_pg_case
    adapter = SimpleNamespace(database=case.database, materials=[case.material], client=lambda _: case.client_for())
    prepare_candidate(adapter, case.technical, case.path)
    entered = Event(); release = Event()
    original = publication_preflight._candidate
    calls = 0
    def hold(session, material):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            if not release.wait(15): raise TimeoutError("Publication preview test was not released")
        return original(session, material)
    with case.client_for() as reader, case.client_for(3) as editor:
        before = preview(reader, case.material.id)
        assert before["can_prepare"] is True
        monkeypatch.setattr(publication_preflight, "_candidate", hold)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(preview, reader, case.material.id)
            try:
                assert entered.wait(15)
                if change == "material":
                    writing = pool.submit(editor.patch, case.path, json={"material_name": "Changed after frozen preview"})
                else:
                    writing = pool.submit(editor.patch, f"/api/brands/{case.material.published_brand_id}",
                        json={"name": "Changed after frozen preview"})
                with pytest.raises(TimeoutError): writing.result(timeout=0.15)
            finally: release.set()
            assert pending.result(timeout=25) == before
            assert writing.result(timeout=25).status_code == 200
        after = preview(reader, case.material.id)
        assert after["can_prepare"] is False and after["preview_hash"] != before["preview_hash"]


def _catalog_pg_category(client):
    response = client.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Synthetic " + uuid4().hex})
    assert response.status_code == 201
    return response.json()


def _prepare_pg_content(client, case):
    from test_catalog_content import content_payload
    category = _catalog_pg_category(client)
    payload = content_payload(description="Synthetic content", credits=10, tags=["stone"], category_ids=[category["id"]])
    assert client.post(case.path + "/content", json=payload).status_code == 200
    return client.get(case.path + "/content-review").json(), payload, category


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_content_approval_race_keeps_one_decision(review_pg_case, same_key):
    from app.db.models import MaterialContentApproval
    from test_content_approvals import approval_payload
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as first, case.client_for(0 if same_key else 3) as second:
        view, _, _ = _prepare_pg_content(first, case)
        payload = approval_payload(view)
        def approve(client, body):
            barrier.wait(timeout=15)
            return client.post(case.path + "/content/approve", json=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve, client, body) for client, body in
                ((first, payload), (second, payload if same_key else approval_payload(view)))]
            responses = [future.result(timeout=25) for future in futures]
        assert sorted(item.status_code for item in responses) == ([200, 200] if same_key else [200, 409])
        if same_key: assert responses[0].json() == responses[1].json()
    with case.database.session() as session:
        assert len(list(session.scalars(select(MaterialContentApproval).where(MaterialContentApproval.material_id == case.material.id)))) == 1


@pytest.mark.parametrize("change", ["content", "brand", "category"])
def test_postgresql_concurrent_edit_never_leaves_stale_content_approved(review_pg_case, change):
    from test_content_approvals import approval_payload
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as approver, case.client_for(3) as editor:
        view, content, category = _prepare_pg_content(approver, case)
        def approve():
            barrier.wait(timeout=15)
            return approver.post(case.path + "/content/approve", json=approval_payload(view))
        def edit():
            barrier.wait(timeout=15)
            if change == "content":
                return editor.post(case.path + "/content", json={**content, "idempotency_key": str(uuid4()), "expected_revision": 1, "credits": 12})
            if change == "brand":
                return editor.patch(f"/api/brands/{case.material.published_brand_id}", json={"brand_identifier": "changed-" + uuid4().hex})
            return editor.patch("/api/online-categories/" + category["id"], json={"idempotency_key": str(uuid4()),
                "expected_version": 1, "is_active": False, "reason": "Synthetic retirement"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            approving = pool.submit(approve); editing = pool.submit(edit)
            approved = approving.result(timeout=25); edited = editing.result(timeout=25)
        assert edited.status_code == 200 and approved.status_code in (200, 409)
        current = approver.get(case.path + "/content-review").json()
        assert current["approval"] is None and current["context_hash"] != view["context_hash"]
        history = approver.get(case.path + "/content-approvals").json()
        assert len(history) == (1 if approved.status_code == 200 else 0)


def test_postgresql_content_decision_is_immutable_and_requires_exact_saved_revision(review_pg_case):
    from app.db.models import MaterialContentApproval
    from test_content_approvals import approval_payload
    case = review_pg_case
    with case.client_for() as client:
        view, _, _ = _prepare_pg_content(client, case)
        assert client.post(case.path + "/content/approve", json=approval_payload(view)).status_code == 200
    for statement in ("UPDATE material_content_approvals SET note='Changed' WHERE material_id=:id",
                      "DELETE FROM material_content_approvals WHERE material_id=:id", "TRUNCATE material_content_approvals"):
        with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
            connection.execute(text(statement), {"id": case.material.id})
    with pytest.raises(IntegrityError), case.database.session() as session:
        session.add(MaterialContentApproval(material_id=case.material.id, content_revision=999, actor_id=case.users[0].id,
            context_hash="a" * 64, snapshot={}, warnings_acknowledged=False))
        session.commit()
    with pytest.raises(IntegrityError), case.database.session() as session:
        session.add(MaterialContentApproval(material_id=case.material.id, content_revision=1, actor_id=case.users[0].id,
            context_hash=view["context_hash"], snapshot=view["snapshot"], warnings_acknowledged=False))
        session.commit()


def test_postgresql_content_approval_upgrade_preserves_prior_draft_and_history():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260916_0010")
            material_id = _create_postgresql_material_with_metadata(database_url, uuid4().hex[:12])
            with engine.begin() as connection:
                actor_id = connection.execute(text("SELECT assigned_processor_id FROM pbr_materials WHERE id=:id"), {"id": material_id}).scalar_one()
                connection.execute(text("INSERT INTO material_content(material_id, revision, description, credits, tags) VALUES (:id, 1, 'Prior synthetic draft', 8, '[\"stone\"]'::jsonb)"), {"id": material_id})
                connection.execute(text("INSERT INTO material_content_revisions(id, material_id, revision, actor_id, snapshot, snapshot_hash, reason) VALUES (:id, :material, 1, :actor, '{}'::jsonb, :hash, 'Prior synthetic save')"),
                    {"id": uuid4(), "material": material_id, "actor": actor_id, "hash": "a" * 64})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            with engine.connect() as connection:
                assert connection.execute(text("SELECT description FROM material_content WHERE material_id=:id"), {"id": material_id}).scalar_one() == "Prior synthetic draft"
                assert connection.execute(text("SELECT count(*) FROM material_content_revisions WHERE material_id=:id"), {"id": material_id}).scalar_one() == 1
                assert connection.execute(text("SELECT count(*) FROM material_content_approvals")).scalar_one() == 0
            command.downgrade(config, "20260916_0010")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT credits FROM material_content WHERE material_id=:id"), {"id": material_id}).scalar_one() == 8
                assert connection.execute(text("SELECT count(*) FROM material_content_revisions WHERE material_id=:id"), {"id": material_id}).scalar_one() == 1
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_catalog_creation_is_serialized_and_replayable(review_pg_case, same_key):
    case = review_pg_case
    payload = {"idempotency_key": str(uuid4()), "value": "Concurrent " + uuid4().hex}
    other = payload if same_key else {**payload, "idempotency_key": str(uuid4()), "value": payload["value"].upper()}
    barrier = Barrier(2)
    with case.client_for() as first, case.client_for(0 if same_key else 3) as second:
        def create(client, body):
            barrier.wait(timeout=15)
            return client.post("/api/online-categories", json=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, client, body) for client, body in ((first, payload), (second, other))]
            responses = [future.result(timeout=25) for future in futures]
        assert sorted(item.status_code for item in responses) == ([201, 201] if same_key else [201, 409])
        if same_key:
            assert responses[0].json() == responses[1].json()


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_content_version_prevents_lost_updates_and_duplicate_revisions(review_pg_case, same_key):
    from test_catalog_content import content_payload
    from app.db.models import MaterialContentRevision
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as first, case.client_for(0 if same_key else 3) as second:
        category = _catalog_pg_category(first)
        payload = content_payload(category_ids=[category["id"]], credits=8, tags=["stone"])
        other = payload if same_key else {**payload, "idempotency_key": str(uuid4()), "credits": 12}
        def save(client, body):
            barrier.wait(timeout=15)
            return client.post(case.path + "/content", json=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(save, client, body) for client, body in ((first, payload), (second, other))]
            responses = [future.result(timeout=25) for future in futures]
        assert sorted(item.status_code for item in responses) == ([200, 200] if same_key else [200, 409])
        if same_key:
            assert responses[0].json() == responses[1].json()
        assert first.get(case.path + "/content").json()["revision"] == 1
    with case.database.session() as session:
        assert len(list(session.scalars(select(MaterialContentRevision).where(MaterialContentRevision.material_id == case.material.id)))) == 1
        assert session.get(MaterialReviewState, case.material.id).generation == 1


def test_postgresql_catalog_retirement_cannot_race_new_material_membership(review_pg_case):
    from test_catalog_content import content_payload
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as editor, case.client_for(3) as manager:
        category = _catalog_pg_category(manager)
        def change(client, path, method, body):
            barrier.wait(timeout=15)
            return client.request(method, path, json=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            saving = pool.submit(change, editor, case.path + "/content", "POST", content_payload(category_ids=[category["id"]]))
            retiring = pool.submit(change, manager, "/api/online-categories/" + category["id"], "PATCH",
                {"idempotency_key": str(uuid4()), "expected_version": 1, "is_active": False, "reason": "Synthetic retirement"})
            saved = saving.result(timeout=25); retired = retiring.result(timeout=25)
        assert retired.status_code == 200
        assert saved.status_code in (200, 409)
        current = editor.get(case.path + "/content").json()
        if saved.status_code == 200:
            assert current["categories"][0]["is_active"] is False
            review = editor.get(case.path + "/review").json()
            assert review["generation"] == 2 and review["failure_code"] == "CATALOG_ACTIVITY_CHANGED"
        else:
            assert saved.json()["detail"]["code"] == "CONTENT_CATALOG_VALUE_INACTIVE"
            assert current["revision"] == 0 and current["categories"] == []


def test_postgresql_catalog_and_content_audit_are_immutable(review_pg_case):
    from test_catalog_content import content_payload
    case = review_pg_case
    with case.client_for() as client:
        category = _catalog_pg_category(client)
        assert client.post(case.path + "/content", json=content_payload(category_ids=[category["id"]], credits=10)).status_code == 200
    statements = [
        "UPDATE online_categories SET value='Changed' WHERE id=:id",
        "UPDATE online_categories SET normalized_key='changed' WHERE id=:id",
        "UPDATE online_categories SET is_active=false WHERE id=:id",
        "DELETE FROM online_categories WHERE id=:id",
        "TRUNCATE online_categories CASCADE",
        "UPDATE catalog_audit_events SET resource_kind='COLLECTION' WHERE resource_id=:id",
        "DELETE FROM catalog_audit_events WHERE resource_id=:id",
        "TRUNCATE catalog_audit_events",
        "UPDATE material_content_revisions SET reason='Changed' WHERE material_id=:material",
        "DELETE FROM material_content_revisions WHERE material_id=:material",
        "TRUNCATE material_content_revisions",
    ]
    for statement in statements:
        with pytest.raises(DBAPIError):
            with case.database.engine.begin() as connection:
                connection.execute(text(statement), {"id": UUID(category["id"]), "material": case.material.id})


def test_postgresql_catalog_upgrade_from_identity_schema_preserves_material_and_ledger():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260915_0008")
            material_id = _create_postgresql_material_with_metadata(database_url, uuid4().hex[:12])
            command.upgrade(config, "20260916_0009")
            with engine.connect() as connection:
                original = tuple(connection.execute(text("SELECT technical_identity, published_brand_id, sequence_number FROM pbr_materials WHERE id=:id"), {"id": material_id}).one())
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            with engine.connect() as connection:
                assert tuple(connection.execute(text("SELECT technical_identity, published_brand_id, sequence_number FROM pbr_materials WHERE id=:id"), {"id": material_id}).one()) == original
                assert connection.execute(text("SELECT count(*) FROM material_number_reservations WHERE material_id=:id"), {"id": material_id}).scalar_one() == 1
                assert connection.execute(text("SELECT count(*) FROM material_content")).scalar_one() == 0
            command.downgrade(config, "20260916_0009")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM material_number_reservations WHERE material_id=:id"), {"id": material_id}).scalar_one() == 1
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


def _identity_target(case):
    with case.database.session() as session:
        old = session.get(PublishedBrand, case.material.published_brand_id)
        suffix = uuid4().hex[:10]
        target = PublishedBrand(company_id=old.company_id, name="Identity target", folder_prefix="NEW" + suffix,
                                brand_identifier="identity-" + suffix)
        session.add(target); session.commit()
    return {"target_brand_id": str(target.id), "main_category_code": "G04"}


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_identity_confirmation_allocates_and_executes_once(review_pg_case, same_key):
    from test_material_identity import prepare
    from app.db.models import MaterialFileOperation, MaterialIdentityHistory, MaterialNumberReservation
    case = review_pg_case; target = _identity_target(case)
    with case.client_for() as first, case.client_for() as second:
        payload = prepare(first, case.path, target)
        barrier = Barrier(2); case.identity.plan_callback = lambda: barrier.wait(timeout=15)
        other = payload if same_key else {**payload, "idempotency_key": str(uuid4())}
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(client.post, case.path + "/identity-confirm", json=body)
                     for client, body in ((first, payload), (second, other))]
            results = [call.result(timeout=30) for call in calls]
        assert sorted(response.status_code for response in results) == ([200, 200] if same_key else [200, 409])
        if same_key: assert results[0].json()["id"] == results[1].json()["id"]
    assert len(case.identity.executions) == 1
    with case.database.session() as session:
        for model in (MaterialFileOperation, MaterialIdentityHistory, MaterialNumberReservation):
            assert len(list(session.scalars(select(model).where(model.material_id == case.material.id)))) == 1
        assert session.get(PBRMaterial, case.material.id).published_brand_id == UUID(target["target_brand_id"])
        assert session.get(PublishedBrand, UUID(target["target_brand_id"])).next_sequence_number == 2


@pytest.mark.parametrize("mutation", ["edit", "brand", "disable", "new-material"])
def test_postgresql_identity_io_holds_durable_ownership_without_open_transaction(review_pg_case, mutation):
    from test_material_identity import prepare
    case = review_pg_case; target = _identity_target(case)
    entered = Event(); release = Event()
    def pause():
        entered.set(); assert release.wait(20)
    case.identity.execute_callback = pause
    with case.client_for() as first, case.client_for(3) as second:
        payload = prepare(first, case.path, target)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(first.post, case.path + "/identity-confirm", json=payload)
            try:
                assert entered.wait(10)
                if mutation == "edit": response = second.patch(case.path, json={"material_name": "Cannot race source write"})
                elif mutation == "brand": response = second.patch("/api/brands/" + target["target_brand_id"], json={"name": "Cannot race rewrite"})
                elif mutation == "disable": response = second.patch("/api/internal-users/" + str(case.users[0].id), json={"is_active": False})
                else:
                    response = second.post("/api/materials", json={"project_id": str(case.material.project_id),
                        "published_brand_id": target["target_brand_id"], "assigned_processor_id": str(case.users[1].id),
                        "material_name": "Another allocation", "main_category_code": "G03"})
                assert response.status_code == (200 if mutation == "disable" else 201 if mutation == "new-material" else 409)
                if mutation == "new-material": assert response.json()["sequence_number"] == 2
            finally: release.set()
            result = future.result(timeout=15)
        assert result.status_code == 200 and result.json()["status"] == "COMPLETED"


def test_postgresql_identity_ledger_history_and_authorization_are_immutable(review_pg_case):
    from test_material_identity import prepare
    case = review_pg_case; target = _identity_target(case)
    with case.client_for() as client:
        result = client.post(case.path + "/identity-confirm", json=prepare(client, case.path, target))
        assert result.status_code == 200 and result.json()["status"] == "COMPLETED"
    for table, column in (("material_number_reservations", "sequence_number = sequence_number + 1"),
                          ("material_identity_history", "reason = 'tampered'"),
                          ("material_file_operations", "status = 'RUNNING'")):
        for sql in (f"UPDATE {table} SET {column} WHERE material_id = :id", f"DELETE FROM {table} WHERE material_id = :id", f"TRUNCATE {table} CASCADE"):
            with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
                connection.execute(text(sql), {"id": case.material.id})


def test_postgresql_identity_active_owner_is_unique_and_downgrade_refuses_it(review_pg_case):
    from test_material_identity import prepare
    from app.identity_client import IdentityClientError
    from app.db.models import MaterialFileOperation
    case = review_pg_case; target = _identity_target(case); case.identity.failure = IdentityClientError()
    with case.client_for() as client:
        result = client.post(case.path + "/identity-confirm", json=prepare(client, case.path, target))
        assert result.status_code == 200 and result.json()["status"] == "RUNNING"
        with case.database.session() as session:
            existing = session.get(MaterialFileOperation, UUID(result.json()["id"]))
            values = {column.name: getattr(existing, column.name) for column in MaterialFileOperation.__table__.columns
                      if column.name not in {"id", "request_key", "created_at", "updated_at"}}
            session.add(MaterialFileOperation(**values, request_key=uuid4()))
            with pytest.raises(IntegrityError): session.commit()
        with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
            connection.execute(text("UPDATE material_file_operations SET target_context = '{}'::jsonb WHERE id=:id"), {"id": result.json()["id"]})
        with pytest.raises(DBAPIError): command.downgrade(Config("alembic.ini"), "20260915_0008")
        case.identity.failure = None
        assert client.post(case.path + "/identity-operations/" + result.json()["id"] + "/resume").json()["status"] == "COMPLETED"


def test_postgresql_identity_upgrade_backfills_reservations_without_resetting_counter():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260915_0008")
            material_id = _create_postgresql_material_with_metadata(database_url, uuid4().hex[:12])
            with engine.begin() as connection:
                connection.execute(text("UPDATE published_brands SET next_sequence_number=25 WHERE id=(SELECT published_brand_id FROM pbr_materials WHERE id=:id)"), {"id": material_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            with engine.connect() as connection:
                row = connection.execute(text("SELECT sequence_number, actor_id, operation_id FROM material_number_reservations WHERE material_id=:id"), {"id": material_id}).one()
                assert tuple(row) == (1, None, None)
                assert connection.execute(text("SELECT next_sequence_number FROM published_brands")).scalar_one() == 25
            command.downgrade(config, "20260915_0008")
            command.upgrade(config, "head"); command.check(config)
            with engine.connect() as connection:
                assert connection.execute(text("SELECT next_sequence_number FROM published_brands")).scalar_one() == 25
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


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


@pytest.mark.parametrize("action", ["folder-preflight", "folder-link", "mark-done"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_postgresql_preflight_disclosure_obeys_actual_concurrent_role_change(review_pg_case, monkeypatch, action, unavailable):
    from app.worker_client import WorkerClient, WorkerUnavailableError
    from test_material_operations import _preflight
    case = review_pg_case; entered = Event(); release = Event()
    def source_read(_client, _path):
        entered.set()
        assert release.wait(timeout=10)
        if unavailable: raise WorkerUnavailableError("Synthetic dependency unavailable")
        return _preflight(case.material.technical_identity)
    monkeypatch.setattr(WorkerClient, "preflight", source_read)
    with case.client_for(1) as processor, case.client_for() as administrator:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(processor.post, case.path + "/" + action,
                json={} if action == "mark-done" else {"folder_path": case.material.folder_path})
            try:
                assert entered.wait(timeout=10)
                assert administrator.patch(f"/api/internal-users/{case.users[1].id}", json={"role": "LEADERSHIP"}).status_code == 200
            finally:
                release.set()
            response = future.result(timeout=15)
        # The account API revokes existing sessions when its role changes.
        # Direct in-transaction role changes are covered by the 403 unit case.
        assert response.status_code == 401
        assert "#A1B2C3" not in response.text and "Synthetic dependency unavailable" not in response.text
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.material.id).workflow_status == "IN_PROGRESS"
        assert session.scalar(select(PBRMaterialMetadataSnapshot.id).where(PBRMaterialMetadataSnapshot.material_id == case.material.id)) is None


@pytest.mark.parametrize("unavailable", [False, True])
@pytest.mark.parametrize("change,expected", [("role", 401), ("material", 409)])
def test_postgresql_folder_discovery_rechecks_after_actual_concurrent_change(review_pg_case, monkeypatch, unavailable, change, expected):
    from app.discovery_client import DiscoveryClientError, FolderDiscovery, WorkerDiscoveryClient
    from test_folder_discovery import payload
    case = review_pg_case; entered = Event(); release = Event()
    def listing(_client, parent):
        entered.set(); assert release.wait(timeout=15)
        if unavailable: raise DiscoveryClientError("DISCOVERY_BUSY")
        return FolderDiscovery.model_validate(payload(parent, case.material.technical_identity))
    monkeypatch.setattr(WorkerDiscoveryClient, "listing", listing)
    with case.client_for() as reader, case.client_for(3) as administrator:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(reader.post, case.path + "/folder-discovery", json={"parent_path": "library"})
            try:
                assert entered.wait(timeout=15)
                response = (administrator.patch(f"/api/internal-users/{case.users[0].id}", json={"role": "LEADERSHIP"}) if change == "role"
                    else administrator.patch(case.path, json={"assigned_processor_id": str(case.users[2].id)}))
                assert response.status_code == 200
            finally: release.set()
            result = future.result(timeout=20)
        assert result.status_code == expected
        assert case.material.technical_identity not in result.text and "DISCOVERY_BUSY" not in result.text


def _import_pg_payload(case, numbers=(7,)):
    from types import SimpleNamespace
    from test_import_preview import plan_payload
    prefix = case.material.technical_identity.rsplit("_", 2)[0]
    return plan_payload(SimpleNamespace(materials=[case.material]), tuple(f"{prefix}_{number:04d}_G03" for number in numbers))


@pytest.mark.parametrize("race", ["identical", "different_key", "different_actor"])
def test_postgresql_import_races_preserve_single_batch_and_permanent_number_owner(review_pg_case, race):
    from app.db.models import MaterialImportBatch, MaterialImportRow, MaterialNumberReservation, PublishedBrand
    from test_import_confirm import confirmation
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as first, case.client_for(3 if race == "different_actor" else 0) as second:
        body = confirmation(first, _import_pg_payload(case, (7, 8)))
        second_body = {**body, **({"idempotency_key": str(uuid4())} if race == "different_key" else {})}
        def submit(client, payload):
            barrier.wait(timeout=10)
            return client.post("/api/material-imports/confirm", json=payload)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(submit, first, body), pool.submit(submit, second, second_body)]
            responses = [future.result(timeout=20) for future in futures]
        if race == "identical":
            assert [response.status_code for response in responses] == [200, 200]
            assert responses[0].json() == responses[1].json()
        else:
            assert sorted(response.status_code for response in responses) == [200, 409]
        result = next(response.json() for response in responses if response.status_code == 200)
    with case.database.session() as session:
        batch = session.get(MaterialImportBatch, UUID(result["id"]))
        assert batch.row_count == 2
        assert len(session.scalars(select(MaterialImportRow).where(MaterialImportRow.batch_id == batch.id)).all()) == 2
        reservations = session.scalars(select(MaterialNumberReservation).where(
            MaterialNumberReservation.brand_id == case.material.published_brand_id,
            MaterialNumberReservation.sequence_number.in_((7, 8)))).all()
        assert len(reservations) == 2 and len({row.material_id for row in reservations}) == 2
        assert session.get(PublishedBrand, case.material.published_brand_id).next_sequence_number == 9


def test_postgresql_import_and_normal_creation_serialize_number_allocation(review_pg_case):
    from app.db.models import MaterialNumberReservation, PublishedBrand
    from test_import_confirm import confirmation
    case = review_pg_case; barrier = Barrier(2)
    with case.database.session() as session:
        session.get(PublishedBrand, case.material.published_brand_id).next_sequence_number = 7
        session.commit()
    with case.client_for() as importer, case.client_for(3) as normal:
        body = confirmation(importer, _import_pg_payload(case, (7, 8)))
        material = case.material
        ordinary = {"project_id": str(material.project_id), "published_brand_id": str(material.published_brand_id),
                    "assigned_processor_id": str(material.assigned_processor_id), "material_name": "Concurrent ordinary material",
                    "main_category_code": "G03"}
        def submit(client, path, payload):
            barrier.wait(timeout=10)
            return client.post(path, json=payload)
        with ThreadPoolExecutor(2) as pool:
            imported = pool.submit(submit, importer, "/api/material-imports/confirm", body)
            created = pool.submit(submit, normal, "/api/materials", ordinary)
            responses = imported.result(timeout=20), created.result(timeout=20)
        assert responses[1].status_code == 201
        assert responses[0].status_code in (200, 409)
        number = responses[1].json()["sequence_number"]
        assert number == (9 if responses[0].status_code == 200 else 7)
    with case.database.session() as session:
        owned = session.scalars(select(PBRMaterial).where(PBRMaterial.published_brand_id == material.published_brand_id)).all()
        assert len({item.sequence_number for item in owned}) == len(owned)
        for item in owned:
            if item.sequence_number != 1:
                assert session.get(MaterialNumberReservation, (material.published_brand_id, item.sequence_number)).material_id == item.id


def test_postgresql_import_rechecks_real_account_revocation_after_source_parsing(review_pg_case, monkeypatch):
    from app.api import material_imports
    from app.db.models import MaterialImportBatch
    from test_import_confirm import confirmation
    case = review_pg_case; entered = Event(); release = Event()
    original = material_imports.prepare_rows
    def paused(*args):
        entered.set()
        assert release.wait(timeout=10)
        return original(*args)
    with case.client_for() as importer, case.client_for(3) as administrator:
        body = confirmation(importer, _import_pg_payload(case))
        monkeypatch.setattr(material_imports, "prepare_rows", paused)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(importer.post, "/api/material-imports/confirm", json=body)
            try:
                assert entered.wait(timeout=10)
                response = administrator.patch(f"/api/internal-users/{case.users[0].id}", json={"is_active": False})
                assert response.status_code == 200
            finally:
                release.set()
            response = future.result(timeout=15)
        assert response.status_code == 401 and "rows" not in response.text
    with case.database.session() as session:
        assert session.scalar(select(MaterialImportBatch.id).where(MaterialImportBatch.actor_id == case.users[0].id)) is None


def test_postgresql_import_gate_keeps_reference_snapshot_consistent_until_commit(review_pg_case):
    from app.db.models import MaterialImportBatch
    from test_import_confirm import confirmation
    case = review_pg_case; entered = Event(); release = Event(); update_started = Event()
    def paused(*_):
        entered.set()
        assert release.wait(timeout=10)
    with case.client_for() as importer, case.client_for(3) as administrator:
        body = confirmation(importer, _import_pg_payload(case))
        event.listen(MaterialImportBatch, "before_insert", paused)
        try:
            with ThreadPoolExecutor(2) as pool:
                imported = pool.submit(importer.post, "/api/material-imports/confirm", json=body)
                try:
                    assert entered.wait(timeout=10)
                    def update():
                        update_started.set()
                        return administrator.patch(f"/api/projects/{case.material.project_id}", json={"name": "Changed after import"})
                    changed = pool.submit(update)
                    assert update_started.wait(timeout=10)
                    assert not changed.done()
                finally:
                    release.set()
                result = imported.result(timeout=15)
                assert result.status_code == 200 and changed.result(timeout=15).status_code == 200
        finally:
            event.remove(MaterialImportBatch, "before_insert", paused)
        assert result.json()["snapshot"]["references"]["projects"][0]["name"] == "Review fixture"
        assert administrator.get(f"/api/projects/{case.material.project_id}").json()["name"] == "Changed after import"


def test_postgresql_import_audit_rejects_direct_sql_mutation_and_destructive_downgrade(review_pg_case):
    from test_import_confirm import confirmation
    case = review_pg_case
    with case.client_for() as client:
        body = confirmation(client, _import_pg_payload(case))
        response = client.post("/api/material-imports/confirm", json=body)
        assert response.status_code == 200
        batch_id = UUID(response.json()["id"])
        for table in ("material_import_batches", "material_import_rows"):
            key = "id" if table == "material_import_batches" else "batch_id"
            for statement in (f"UPDATE {table} SET snapshot='{{}}'::jsonb WHERE {key}=:id",
                              f"DELETE FROM {table} WHERE {key}=:id", f"TRUNCATE {table} CASCADE"):
                with pytest.raises(DBAPIError):
                    with case.database.engine.begin() as connection:
                        connection.execute(text(statement), {"id": batch_id})
        with pytest.raises(DBAPIError):
            command.downgrade(Config("alembic.ini"), "20260916_0011")
        assert client.get("/api/material-imports/" + str(batch_id)).json() == response.json()
    with case.database.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0020"


def test_postgresql_import_upgrade_from_0011_and_empty_downgrade_preserve_prior_records():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260916_0011")
            company_id = uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO companies (id, name) VALUES (:id, 'Prior synthetic company')"), {"id": company_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            schema = inspect(engine)
            assert schema.has_table("material_import_batches") and schema.has_table("material_import_rows")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgname IN "
                    "('material_import_batches_append_only', 'material_import_rows_append_only')")).scalar_one() == 2
                assert connection.execute(text("SELECT name FROM companies WHERE id=:id"), {"id": company_id}).scalar_one() == "Prior synthetic company"
            command.downgrade(config, "20260916_0011")
            assert not inspect(engine).has_table("material_import_batches")
            assert not inspect(engine).has_table("material_import_rows")
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_ai_proposal_retry_and_parallel_distinct_proposals_are_atomic(review_pg_case, same_key):
    from test_ai_content import proposal
    from app.db.models import MaterialAiDraft
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as first, case.client_for(0 if same_key else 3) as second:
        current = first.get(case.path + "/publishing-context").json()
        data = proposal(current)
        other = data if same_key else {**data, "idempotency_key": str(uuid4())}
        def submit(client, body):
            barrier.wait(timeout=15)
            return client.post(case.path + "/content-drafts", json=body)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(submit, client, body) for client, body in ((first, data), (second, other))]
            responses = [future.result(timeout=25) for future in futures]
        assert all(item.status_code == 201 for item in responses)
        if same_key: assert responses[0].json() == responses[1].json()
    with case.database.session() as session:
        drafts = list(session.scalars(select(MaterialAiDraft).where(MaterialAiDraft.material_id == case.material.id)))
        assert len(drafts) == (1 if same_key else 2)


def test_postgresql_source_retirement_and_draft_intake_use_one_material_context(review_pg_case):
    from test_ai_content import approve_source, proposal
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as author, case.client_for(3) as admin:
        source = approve_source(admin, case.path); current = author.get(case.path + "/publishing-context").json()
        data = proposal(current, source_link_ids=[source["id"]])
        def submit():
            barrier.wait(timeout=15); return author.post(case.path + "/content-drafts", json=data)
        def retire():
            barrier.wait(timeout=15)
            return admin.patch(case.path + "/content-sources/" + source["id"], json={"idempotency_key": str(uuid4()), "expected_version": 1, "is_active": False, "reason": "Concurrent retirement"})
        with ThreadPoolExecutor(2) as pool:
            draft = pool.submit(submit); retired = pool.submit(retire)
            accepted = draft.result(timeout=25); changed = retired.result(timeout=25)
        assert changed.status_code == 200 and accepted.status_code in (201, 409)
        history = author.get(case.path + "/content-drafts").json()["items"]
        assert len(history) == (1 if accepted.status_code == 201 else 0)
        if history: assert history[0]["context_is_current"] is False


@pytest.mark.parametrize("duplicate", [True, False])
def test_postgresql_source_approval_serializes_duplicates_and_active_limit(review_pg_case, duplicate):
    from test_ai_content import approve_source
    from app.db.models import MaterialSourceLink
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as first, case.client_for(3) as second:
        if not duplicate:
            for number in range(19): approve_source(first, case.path, f"https://catalog.example/{number}")
        def submit(client, suffix):
            barrier.wait(timeout=15)
            return client.post(case.path + "/content-sources", json={"idempotency_key": str(uuid4()), "url": "https://catalog.example/new" + suffix, "reason": "Concurrent source approval"})
        with ThreadPoolExecutor(2) as pool:
            one = pool.submit(submit, first, ""); two = pool.submit(submit, second, "" if duplicate else "-other")
            responses = [one.result(timeout=25), two.result(timeout=25)]
        assert sorted(item.status_code for item in responses) == [201, 409]
    with case.database.session() as session:
        rows = list(session.scalars(select(MaterialSourceLink).where(MaterialSourceLink.material_id == case.material.id)))
        assert len(rows) == (1 if duplicate else 20)


def test_postgresql_ai_and_source_provenance_cannot_be_erased_or_rewritten(review_pg_case):
    from test_ai_content import approve_source, proposal
    case = review_pg_case
    with case.client_for() as client:
        source = approve_source(client, case.path)
        draft = client.post(case.path + "/content-drafts", json=proposal(client.get(case.path + "/publishing-context").json())).json()
        for table, record_id, column in (("material_source_links", source["id"], "url='https://catalog.example/tampered'"),
                                          ("material_ai_drafts", draft["id"], "description='Tampered'")):
            for statement in (f"UPDATE {table} SET {column} WHERE id=:id", f"DELETE FROM {table} WHERE id=:id", f"TRUNCATE {table} CASCADE"):
                with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
                    connection.execute(text(statement), {"id": record_id})
        with pytest.raises(DBAPIError): command.downgrade(Config("alembic.ini"), "20260917_0012")
        assert client.get(case.path + "/content-drafts").json()["items"][0]["id"] == draft["id"]
    with case.database.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0020"


def test_postgresql_ai_upgrade_from_0012_preserves_records_and_empty_downgrade():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260917_0012")
            company_id = uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO companies (id, name) VALUES (:id, 'Prior source context fixture')"), {"id": company_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            assert inspect(engine).has_table("material_ai_drafts") and inspect(engine).has_table("material_source_links")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT name FROM companies WHERE id=:id"), {"id": company_id}).scalar_one() == "Prior source context fixture"
            command.downgrade(config, "20260917_0012")
            assert not inspect(engine).has_table("material_ai_drafts") and not inspect(engine).has_table("material_source_links")
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_ai_adoption_prevents_double_revision_or_lost_human_edits(review_pg_case, same_key):
    from test_ai_adoption import prepare, adoption
    from app.db.models import MaterialContentRevision
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as first, case.client_for(0 if same_key else 3) as second:
        path, draft, target = prepare(first, case.material)
        body = adoption(draft)
        other = body if same_key else {**body, "idempotency_key": str(uuid4()), "description": "Other human edit"}
        def save(client, data):
            barrier.wait(timeout=15); return client.post(target, json=data)
        with ThreadPoolExecutor(2) as pool:
            one = pool.submit(save, first, body); two = pool.submit(save, second, other)
            responses = [one.result(timeout=25), two.result(timeout=25)]
        assert sorted(item.status_code for item in responses) == ([200, 200] if same_key else [200, 409])
        if same_key: assert responses[0].json() == responses[1].json()
        assert first.get(path + "/content").json()["revision"] == 2
    with case.database.session() as session:
        rows = list(session.scalars(select(MaterialContentRevision).where(MaterialContentRevision.material_id == case.material.id)))
        assert len(rows) == 2
        current = next(item for item in rows if item.revision == 2)
        assert current.snapshot["ai_provenance"]["draft_id"] == draft["id"]


def test_postgresql_ai_issuance_retry_never_persists_or_replays_secret(review_pg_case):
    from test_ai_service import issuance
    from app.db.models import AiServiceCredential
    case = review_pg_case; barrier = Barrier(2); payload = issuance()
    with case.client_for() as first, case.client_for() as second:
        def issue(client):
            barrier.wait(timeout=15); return client.post(case.path + "/ai-service-credentials", json=payload)
        with ThreadPoolExecutor(2) as pool:
            one = pool.submit(issue, first); two = pool.submit(issue, second)
            responses = [one.result(timeout=25), two.result(timeout=25)]
        assert all(item.status_code == 201 for item in responses)
        assert responses[0].json()["credential"] == responses[1].json()["credential"]
        assert sum(item.json()["secret_available"] for item in responses) == 1
    with case.database.session() as session:
        assert len(list(session.scalars(select(AiServiceCredential).where(AiServiceCredential.material_id == case.material.id)))) == 1
        audit = session.scalar(select(MaterialAuditEvent).where(MaterialAuditEvent.actor_id == case.users[0].id,
            MaterialAuditEvent.request_key == UUID(payload["idempotency_key"])))
        assert audit.result["body"]["token"] is None and audit.result["body"]["secret_available"] is False


def test_postgresql_ai_issuance_respects_active_bound_under_race(review_pg_case):
    from test_ai_service import issue, issuance
    from app.db.models import AiServiceCredential
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as first, case.client_for(3) as second:
        for _ in range(9): issue(first, case.path)
        def create(client):
            barrier.wait(timeout=15); return client.post(case.path + "/ai-service-credentials", json=issuance())
        with ThreadPoolExecutor(2) as pool:
            one = pool.submit(create, first); two = pool.submit(create, second)
            responses = [one.result(timeout=25), two.result(timeout=25)]
        assert sorted(item.status_code for item in responses) == [201, 409]
    with case.database.session() as session:
        assert len(list(session.scalars(select(AiServiceCredential).where(AiServiceCredential.material_id == case.material.id)))) == 10


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_ai_service_submission_replay_is_bound_to_credential(review_pg_case, same_key):
    from test_ai_service import issue, service_headers
    from test_ai_content import proposal
    from app.db.models import MaterialAiDraft
    case = review_pg_case; barrier = Barrier(2); target = case.path.replace("/api/", "/api/ai/")
    with case.client_for() as admin, TestClient(case.app) as first, TestClient(case.app) as second:
        issued = issue(admin, case.path)
        first.headers.update(service_headers(issued)); second.headers.update(service_headers(issued))
        context = first.get(target + "/publishing-context").json(); data = proposal(context)
        def submit(client, body):
            barrier.wait(timeout=15); return client.post(target + "/content-drafts", json=body)
        with ThreadPoolExecutor(2) as pool:
            one = pool.submit(submit, first, data); two = pool.submit(submit, second, data if same_key else proposal(context))
            responses = [one.result(timeout=25), two.result(timeout=25)]
        assert all(item.status_code == 201 for item in responses)
        assert (responses[0].json()["id"] == responses[1].json()["id"]) is same_key
    with case.database.session() as session:
        rows = list(session.scalars(select(MaterialAiDraft).where(MaterialAiDraft.material_id == case.material.id)))
        assert len(rows) == (1 if same_key else 2)
        assert all(str(item.service_credential_id) == issued["credential"]["id"] for item in rows)


def test_postgresql_ai_revoke_serializes_with_inflight_submission(review_pg_case):
    from test_ai_service import issue, service_headers
    from test_ai_content import proposal
    from app.db.models import MaterialAiDraft
    case = review_pg_case; entered = Event(); release = Event(); revoke_started = Event()
    target = case.path.replace("/api/", "/api/ai/")
    def held_insert(_, __, item):
        if item.material_id == case.material.id:
            entered.set(); assert release.wait(timeout=15)
    with case.client_for() as admin, case.client_for(3) as revoker, TestClient(case.app) as ai:
        issued = issue(admin, case.path); ai.headers.update(service_headers(issued))
        data = proposal(ai.get(target + "/publishing-context").json())
        event.listen(MaterialAiDraft, "before_insert", held_insert)
        try:
            with ThreadPoolExecutor(2) as pool:
                pending = pool.submit(ai.post, target + "/content-drafts", json=data)
                assert entered.wait(timeout=10)
                def revoke():
                    revoke_started.set()
                    return revoker.post(case.path + "/ai-service-credentials/" + issued["credential"]["id"] + "/revoke",
                        json={"idempotency_key": str(uuid4()), "reason": "Stop in-flight synthetic client"})
                revoked = pool.submit(revoke)
                assert revoke_started.wait(timeout=5); assert not revoked.done()
                release.set()
                assert pending.result(timeout=20).status_code == 201
                assert revoked.result(timeout=20).status_code == 200
        finally:
            release.set(); event.remove(MaterialAiDraft, "before_insert", held_insert)
        assert ai.get(target + "/publishing-context").status_code == 401
        assert ai.post(target + "/content-drafts", json=data).status_code == 401
    with case.database.session() as session:
        assert len(list(session.scalars(select(MaterialAiDraft).where(MaterialAiDraft.material_id == case.material.id)))) == 1


@pytest.mark.parametrize("operation", ["context", "submit"])
def test_postgresql_ai_expiry_rechecked_after_material_lock_wait(review_pg_case, monkeypatch, operation):
    from datetime import timedelta
    from app.auth.service import database_now, _aware
    from app.db.models import AiServiceCredential
    from test_ai_service import issue, service_headers
    from test_ai_content import proposal
    case = review_pg_case; attempted = Event(); expired = Event(); target = case.path.replace("/api/", "/api/ai/")
    with case.client_for() as admin, TestClient(case.app) as ai:
        issued = issue(admin, case.path); ai.headers.update(service_headers(issued))
        data = proposal(ai.get(target + "/publishing-context").json())
        with case.database.session() as session:
            deadline = _aware(session.get(AiServiceCredential, UUID(issued["credential"]["id"])).expires_at)
        monkeypatch.setattr("app.ai_service_access.database_now", lambda session: deadline + timedelta(seconds=1) if expired.is_set() else database_now(session))
        def observe(_, __, statement, parameters, ___, ____):
            if "FROM pbr_materials" in statement and "FOR UPDATE" in statement: attempted.set()
        with case.database.engine.connect() as blocker:
            transaction = blocker.begin()
            blocker.execute(text("SELECT id FROM pbr_materials WHERE id=:id FOR UPDATE"), {"id": case.material.id})
            event.listen(case.database.engine, "before_cursor_execute", observe)
            try:
                with ThreadPoolExecutor(1) as pool:
                    result = pool.submit(ai.get, target + "/publishing-context") if operation == "context" else pool.submit(ai.post, target + "/content-drafts", json=data)
                    try: assert attempted.wait(timeout=10)
                    finally:
                        expired.set(); transaction.rollback()
                    assert result.result(timeout=20).status_code == 401
            finally:
                if transaction.is_active: transaction.rollback()
                event.remove(case.database.engine, "before_cursor_execute", observe)


def test_postgresql_ai_credential_scope_and_revocation_are_permanent(review_pg_case):
    from test_ai_service import issue
    case = review_pg_case
    with case.client_for() as admin:
        issued = issue(admin, case.path); credential_id = issued["credential"]["id"]
        for change in ("token_hash=repeat('a',64)", "expires_at=expires_at + interval '1 hour'", "issuer_session_id=gen_random_uuid()", "actor_id=gen_random_uuid()"):
            with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
                connection.execute(text("UPDATE ai_service_credentials SET " + change + " WHERE id=:id"), {"id": credential_id})
        assert admin.post(case.path + "/ai-service-credentials/" + credential_id + "/revoke", json={"idempotency_key": str(uuid4()), "reason": "Permanent revocation"}).status_code == 200
        for statement in ("UPDATE ai_service_credentials SET revoked_at=NULL WHERE id=:id", "DELETE FROM ai_service_credentials WHERE id=:id", "TRUNCATE ai_service_credentials CASCADE"):
            with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
                connection.execute(text(statement), {"id": credential_id})
        with pytest.raises(DBAPIError): command.downgrade(Config("alembic.ini"), "20260917_0013")
    with case.database.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0020"


def test_postgresql_ai_service_upgrade_from_0013_preserves_prior_records():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260917_0013")
            company_id = uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO companies (id, name) VALUES (:id, 'Prior AI service fixture')"), {"id": company_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            assert inspect(engine).has_table("ai_service_credentials")
            assert "service_credential_id" in {item["name"] for item in inspect(engine).get_columns("material_ai_drafts")}
            with engine.connect() as connection:
                assert connection.execute(text("SELECT name FROM companies WHERE id=:id"), {"id": company_id}).scalar_one() == "Prior AI service fixture"
            command.downgrade(config, "20260917_0013")
            assert not inspect(engine).has_table("ai_service_credentials")
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
            assert current_revision == "20260918_0020"
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
                ).scalar_one() == "20260918_0020"
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
        # The domain-only actor must exist before this test starts holding a brand
        # lock. Lazy first-use actor insertion would make the second request wait
        # on actor creation before reaching the brand lock being measured here.
        if session.get(InternalUser, UUID(int=1)) is None:
            session.add(InternalUser(id=UUID(int=1), display_name="Synthetic domain actor",
                email="domain-actor@example.invalid", role="ADMIN", is_active=True))
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


# Keep populated publication cases after older shared-schema downgrade guards.
# Those guards must each reach the provenance table they are intended to verify.
def _prepare_pg_publication(case):
    from test_publication_preflight import prepare_candidate
    adapter = SimpleNamespace(database=case.database, materials=[case.material], client=lambda _: case.client_for())
    prepare_candidate(adapter, case.technical, case.path)


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_publication_batch_creation_is_atomic_and_exactly_replayable(review_pg_case, same_key):
    from app.db.models import PublicationBatchItem
    from test_publication_preflight import preview
    from test_publication_batches import PATH, creation
    case = review_pg_case; _prepare_pg_publication(case); barrier = Barrier(2)
    with case.client_for() as first, case.client_for() as second:
        body = creation(preview(first, case.material.id))
        other = body if same_key else {**body, "idempotency_key": str(uuid4())}
        def create(client, payload):
            barrier.wait(timeout=15)
            return client.post(PATH, json=payload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, client, payload) for client, payload in ((first, body), (second, other))]
            results = [future.result(timeout=25) for future in futures]
        assert [result.status_code for result in results] == [201, 201]
        assert (results[0].json() == results[1].json()) is same_key
        for result in results:
            batch = result.json()
            assert first.get(PATH + "/" + batch["id"] + "/csv").headers["x-content-sha256"] == batch["csv_sha256"]
    with case.database.session() as session:
        assert len(list(session.scalars(select(PublicationBatchItem).where(PublicationBatchItem.material_id == case.material.id)))) == (1 if same_key else 2)
        assert len(list(session.scalars(select(MaterialAuditEvent).where(MaterialAuditEvent.material_id == case.material.id,
            MaterialAuditEvent.event_type == "PUBLICATION_BATCH_PREPARED")))) == (1 if same_key else 2)


@pytest.mark.parametrize("change", ["content", "brand"])
def test_postgresql_publication_commit_preserves_snapshot_during_competing_edit(review_pg_case, monkeypatch, change):
    from app import publication_preflight
    from test_publication_preflight import preview
    from test_publication_batches import PATH, creation
    from test_catalog_content import content_payload
    case = review_pg_case; _prepare_pg_publication(case)
    entered = Event(); release = Event(); original = publication_preflight._candidate
    calls = 0
    def hold(session, material):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            if not release.wait(15): raise TimeoutError("Publication commit test was not released")
        return original(session, material)
    with case.client_for() as publisher, case.client_for(3) as editor:
        view = preview(publisher, case.material.id); body = creation(view)
        content = editor.get(case.path + "/content").json()
        monkeypatch.setattr(publication_preflight, "_candidate", hold)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(publisher.post, PATH, json=body)
            try:
                assert entered.wait(15)
                if change == "content":
                    writing = pool.submit(editor.post, case.path + "/content", json=content_payload(expected_revision=content["revision"],
                        description="Changed after immutable batch", credits=10, category_ids=[item["id"] for item in content["categories"]]))
                else:
                    writing = pool.submit(editor.patch, f"/api/brands/{case.material.published_brand_id}",
                        json={"name": "Changed after immutable batch"})
                with pytest.raises(TimeoutError): writing.result(timeout=0.15)
            finally: release.set()
            result = pending.result(timeout=25)
            assert result.status_code == 201
            assert writing.result(timeout=25).status_code == 200
        saved = result.json()
        assert saved["items"][0]["row"] == view["items"][0]["row"]
        assert publisher.post(PATH, json=body).json() == saved
        assert publisher.post(PATH, json={**body, "idempotency_key": str(uuid4())}).status_code == 409
        assert publisher.get(PATH + "/" + saved["id"]).json() == saved
        assert preview(publisher, case.material.id)["can_prepare"] is False


def test_postgresql_publication_history_rejects_mutation_cross_material_proof_and_downgrade(review_pg_case):
    from test_publication_preflight import preview
    from test_publication_batches import PATH, creation
    case = review_pg_case; _prepare_pg_publication(case)
    with case.client_for() as client:
        response = client.post(PATH, json=creation(preview(client, case.material.id)))
        assert response.status_code == 201
        batch_id = UUID(response.json()["id"])
    for table, column in (("publication_batches", "reason"), ("publication_batch_items", "snapshot_hash")):
        for sql in (f"UPDATE {table} SET {column}={column}", f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
            with case.database.engine.begin() as connection:
                with pytest.raises(DBAPIError, match="append-only"):
                    connection.execute(text(sql))
    with case.database.session() as session:
        other = PBRMaterial(project_id=case.material.project_id, published_brand_id=case.material.published_brand_id,
            sequence_number=2, assigned_processor_id=case.material.assigned_processor_id, material_name="Other proof fixture",
            main_category_code="G03", technical_identity=case.material.technical_identity + "_OTHER")
        session.add(other); session.commit(); other_id = other.id
    with case.database.engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text("""INSERT INTO publication_batch_items
                (batch_id, material_id, ordinal, generation, revision_hash, content_context_hash, snapshot_hash,
                 technical_check_id, metadata_snapshot_id, technical_approval_id, publication_approval_id, snapshot)
                SELECT batch_id, :other, 2, generation, revision_hash, content_context_hash, snapshot_hash,
                 technical_check_id, metadata_snapshot_id, technical_approval_id, publication_approval_id, snapshot
                FROM publication_batch_items WHERE batch_id=:batch"""), {"other": other_id, "batch": batch_id})
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", case.database.engine.url.render_as_string(hide_password=False)); get_settings.cache_clear()
        with pytest.raises(DBAPIError, match="Publication provenance exists"):
            command.downgrade(Config("alembic.ini"), "20260917_0014")
    with case.database.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0020"


def test_postgresql_publication_upgrade_from_0014_preserves_prior_records_and_empty_downgrade():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260917_0014")
            company_id = uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO companies (id, name) VALUES (:id, 'Prior publication fixture')"), {"id": company_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            assert inspect(engine).has_table("publication_batches") and inspect(engine).has_table("publication_batch_items")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT name FROM companies WHERE id=:id"), {"id": company_id}).scalar_one() == "Prior publication fixture"
            command.downgrade(config, "20260917_0014")
            assert not inspect(engine).has_table("publication_batches") and inspect(engine).has_table("ai_service_credentials")
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


# Policy decisions follow older populated downgrade guards in this shared schema.
def _prepare_pg_policy(case):
    from test_material_approvals import run
    from test_packaging_policy import choose
    with case.database.session() as session:
        session.get(PBRMaterial, case.material.id).workflow_status = "DONE"; session.commit()
    with case.client_for() as client:
        assert run(client, case.path).status_code == 200
        return choose(client, case.path)


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_packaging_first_policy_is_serialized(review_pg_case, same_key):
    from app.db.models import MaterialPackagingPolicy
    from test_material_approvals import run
    from test_packaging_policy import selection
    case = review_pg_case; barrier = Barrier(2)
    with case.database.session() as session:
        session.get(PBRMaterial, case.material.id).workflow_status = "DONE"; session.commit()
    with case.client_for() as first, case.client_for(0 if same_key else 3) as second:
        assert run(first, case.path).status_code == 200
        body = selection(first, case.path)
        other = body if same_key else {**body, "idempotency_key": str(uuid4())}
        def send(client, payload):
            barrier.wait(timeout=10)
            return client.post(case.path + "/packaging-policy/select", json=payload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, first, body), pool.submit(send, second, other)]
            results = [future.result(timeout=30) for future in futures]
        assert [result.status_code for result in results] == [200, 200]
        assert results[0].json() == results[1].json()
    with case.database.session() as session:
        assert len(list(session.scalars(select(MaterialPackagingPolicy).where(MaterialPackagingPolicy.material_id == case.material.id)))) == 1


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_packaging_override_has_one_successor_and_exact_replay(review_pg_case, same_key):
    from app.db.models import MaterialPackagingPolicy
    from test_packaging_policy import override_body
    case = review_pg_case; current = _prepare_pg_policy(case); barrier = Barrier(2)
    with case.client_for() as first, case.client_for(0 if same_key else 3) as second:
        body = override_body(first, case.path, current)
        other = body if same_key else {**body, "idempotency_key": str(uuid4())}
        def send(client, payload):
            barrier.wait(timeout=10)
            return client.post(case.path + "/packaging-policy/override", json=payload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, first, body), pool.submit(send, second, other)]
            results = [future.result(timeout=30) for future in futures]
        assert sorted(result.status_code for result in results) == ([200, 200] if same_key else [200, 409])
        if same_key: assert results[0].json() == results[1].json()
    with case.database.session() as session:
        rows = list(session.scalars(select(MaterialPackagingPolicy).where(MaterialPackagingPolicy.material_id == case.material.id).order_by(MaterialPackagingPolicy.revision)))
        assert len(rows) == 2 and rows[1].previous_id == rows[0].id


@pytest.mark.parametrize("change", ["material", "disable"])
def test_postgresql_packaging_write_serializes_material_and_account_change(review_pg_case, monkeypatch, change):
    from app.api import packaging_policy
    from test_packaging_policy import override_body
    case = review_pg_case; current = _prepare_pg_policy(case)
    entered = Event(); release = Event()
    original = packaging_policy.override_preview
    def hold(*args):
        entered.set()
        if not release.wait(15): raise TimeoutError("Policy test was not released")
        return original(*args)
    with case.client_for() as first, case.client_for(3) as editor:
        body = override_body(first, case.path, current)
        monkeypatch.setattr(packaging_policy, "override_preview", hold)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(first.post, case.path + "/packaging-policy/override", json=body)
            try:
                assert entered.wait(15)
                if change == "material":
                    writing = pool.submit(editor.patch, case.path, json={"material_name": "Changed after policy"})
                else:
                    writing = pool.submit(editor.patch, f"/api/internal-users/{case.users[0].id}", json={"is_active": False})
                with pytest.raises(TimeoutError): writing.result(timeout=0.15)
            finally: release.set()
            assert pending.result(timeout=25).status_code == 200
            assert writing.result(timeout=25).status_code == 200
        if change == "disable":
            assert first.post(case.path + "/packaging-policy/override", json=body).status_code == 401


def test_postgresql_packaging_decisions_require_immutable_contiguous_same_material_history(review_pg_case):
    from app.db.models import MaterialPackagingPolicy
    from test_packaging_policy import override_body
    case = review_pg_case; current = _prepare_pg_policy(case)
    with case.client_for() as client:
        response = client.post(case.path + "/packaging-policy/override", json=override_body(client, case.path, current))
        assert response.status_code == 200
        latest = response.json()["current"]
    for sql in ("UPDATE material_packaging_policies SET reason=reason", "DELETE FROM material_packaging_policies",
        "TRUNCATE material_packaging_policies CASCADE"):
        with case.database.engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"): connection.execute(text(sql))
    # Bulk SQL bypasses ORM hooks: the database must reject a gap, stale predecessor,
    # unchanged policy and a changed storage timezone itself.
    for revision, previous, policy, zone in (
        (4, latest["id"], current["policy"], "Europe/Prague"),
        (3, current["id"], current["policy"], "Europe/Prague"),
        (3, latest["id"], latest["policy"], "Europe/Prague"),
        (3, latest["id"], current["policy"], "UTC"),
    ):
        with case.database.engine.begin() as connection:
            with pytest.raises(DBAPIError, match="extend the current decision"):
                connection.execute(text("""INSERT INTO material_packaging_policies
                    (id, material_id, revision, previous_id, actor_id, policy, storage_timezone, reason, evidence, evidence_hash)
                    VALUES (:id, :material, :revision, :previous, :actor, :policy, :zone, 'Synthetic invalid history', '{}'::jsonb, :hash)"""),
                    {"id": uuid4(), "material": case.material.id, "revision": revision, "previous": UUID(previous),
                        "actor": case.users[0].id, "policy": policy, "zone": zone, "hash": "a" * 64})
    with case.database.session() as session:
        other = PBRMaterial(project_id=case.material.project_id, published_brand_id=case.material.published_brand_id,
            sequence_number=2, assigned_processor_id=case.material.assigned_processor_id, material_name="Other policy fixture",
            main_category_code="G03", technical_identity=case.material.technical_identity + "_OTHER")
        session.add(other); session.commit(); other_id = other.id
    with case.database.session() as session:
        session.add(MaterialPackagingPolicy(material_id=other_id, revision=1, inventory_id=UUID(current["inventory_id"]),
            actor_id=case.users[0].id, policy=current["policy"], storage_timezone="Europe/Prague",
            reason="Cross-material source is forbidden", evidence={}, evidence_hash="a" * 64))
        with pytest.raises(IntegrityError, match="fk_material_packaging_policies_inventory"): session.commit()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", case.database.engine.url.render_as_string(hide_password=False)); get_settings.cache_clear()
        with pytest.raises(DBAPIError, match="Packaging policy provenance exists"):
            command.downgrade(Config("alembic.ini"), "20260917_0015")
    with case.database.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0020"


def test_postgresql_packaging_upgrade_from_0015_preserves_prior_records_and_empty_downgrade():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260917_0015")
            company_id = uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO companies (id, name) VALUES (:id, 'Prior policy fixture')"), {"id": company_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            assert inspect(engine).has_table("material_packaging_policies")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT name FROM companies WHERE id=:id"), {"id": company_id}).scalar_one() == "Prior policy fixture"
                assert connection.execute(text("SELECT count(*) FROM material_packaging_policies")).scalar_one() == 0
            command.downgrade(config, "20260917_0015")
            assert not inspect(engine).has_table("material_packaging_policies") and inspect(engine).has_table("publication_batches")
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


# Execution provenance follows all earlier populated downgrade guards.
def _prepare_pg_packaging_execution(case):
    from test_packaging_policy import choose
    from test_publication_preflight import preview
    from test_publication_batches import PATH, creation
    from test_packaging_ownership import execution_values
    _prepare_pg_publication(case)
    with case.client_for() as client:
        policy = choose(client, case.path)
        response = client.post(PATH, json=creation(preview(client, case.material.id)))
        assert response.status_code == 201
    with case.database.session() as session:
        return execution_values(session, UUID(response.json()["id"]), case.material.id, UUID(policy["id"]), case.users[0].id)


def _packaging_identity_fixture(case, *, status="RUNNING"):
    from app.db.models import MaterialFileOperation
    return MaterialFileOperation(material_id=case.material.id, actor_id=case.users[0].id,
        source_brand_id=case.material.published_brand_id, target_brand_id=case.material.published_brand_id,
        request_key=uuid4(), request_hash="a" * 64, proposal_hash="b" * 64, request_payload={},
        source_context={"folder_path": case.material.folder_path}, target_context={"folder_path": case.material.folder_path + "_NEXT"},
        worker_plan={}, status=status)


def test_postgresql_packaging_owner_claim_is_unique_under_real_concurrent_insert(review_pg_case):
    from app.db.models import MaterialPackagingState
    from test_packaging_ownership import reserve
    case = review_pg_case; values = _prepare_pg_packaging_execution(case)
    # Preserve UUID types and change only the independently bound operation/key.
    other = {**values, "id": uuid4(), "request_key": uuid4()}
    other["worker_request"] = json.loads(json.dumps(values["worker_request"]))
    other["worker_request"]["request"]["operation_id"] = str(other["id"])
    from app.material_review import canonical_hash
    other["worker_request_hash"] = canonical_hash(other["worker_request"]["request"])
    other["worker_request"]["request_hash"] = other["worker_request_hash"]
    barrier = Barrier(2)
    def claim(data):
        with case.database.session() as session:
            barrier.wait(timeout=10)
            try: reserve(session, data); session.commit(); return "claimed"
            except IntegrityError as error:
                assert "uq_packaging_states_active" in str(error.orig)
                session.rollback(); return "blocked"
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(claim, data) for data in (values, other)]
        assert sorted(future.result(timeout=20) for future in futures) == ["blocked", "claimed"]
    with case.database.session() as session:
        assert len(list(session.scalars(select(MaterialPackagingState).where(MaterialPackagingState.material_id == case.material.id)))) == 1


@pytest.mark.parametrize("first", ["concurrent", "packaging", "identity"])
def test_postgresql_packaging_and_identity_are_mutually_exclusive_in_both_directions(review_pg_case, first):
    from app.db.models import MaterialFileOperation, MaterialPackagingState
    from test_packaging_ownership import reserve
    case = review_pg_case; values = _prepare_pg_packaging_execution(case); barrier = Barrier(2)
    def claim(kind, *, wait=False):
        with case.database.session() as session:
            if wait: barrier.wait(timeout=10)
            try:
                if kind == "packaging": reserve(session, values)
                else: session.add(_packaging_identity_fixture(case))
                session.commit(); return "claimed"
            except DBAPIError as error:
                assert "active identity operation" in str(error.orig) or "active packaging execution" in str(error.orig)
                session.rollback(); return "blocked"
    if first == "concurrent":
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(claim, kind, wait=True) for kind in ("packaging", "identity")]
            assert sorted(future.result(timeout=20) for future in futures) == ["blocked", "claimed"]
    else:
        assert claim(first) == "claimed"
        assert claim("identity" if first == "packaging" else "packaging") == "blocked"
    with case.database.session() as session:
        packages = list(session.scalars(select(MaterialPackagingState).where(MaterialPackagingState.material_id == case.material.id)))
        identities = list(session.scalars(select(MaterialFileOperation).where(MaterialFileOperation.material_id == case.material.id)))
        assert len(packages) + len(identities) == 1


def test_postgresql_identity_reactivation_cannot_bypass_packaging_ownership(review_pg_case):
    from test_packaging_ownership import reserve
    case = review_pg_case; values = _prepare_pg_packaging_execution(case)
    with case.database.session() as session:
        identity = _packaging_identity_fixture(case, status="COMPLETED"); session.add(identity); session.commit()
        reserve(session, values); session.commit()
        with pytest.raises(DBAPIError, match="active packaging execution"):
            session.execute(text("UPDATE material_file_operations SET status='RUNNING' WHERE id=:id"), {"id": identity.id})
            session.commit()


def test_postgresql_packaging_provenance_is_append_only_and_ownership_is_preserved(review_pg_case):
    from test_packaging_ownership import reserve, dispatch, finish
    case = review_pg_case; values = _prepare_pg_packaging_execution(case)
    with case.database.session() as session:
        execution = reserve(session, values); action = dispatch(session, execution)
        finish(session, execution, action); session.commit()
    for table in ("material_packaging_executions", "material_packaging_dispatches", "material_packaging_observations"):
        for sql in (f"UPDATE {table} SET created_at=created_at", f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
            with case.database.engine.begin() as connection:
                with pytest.raises(DBAPIError, match="append-only"): connection.execute(text(sql))
    for sql in ("DELETE FROM material_packaging_states", "TRUNCATE material_packaging_states"):
        with case.database.engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"): connection.execute(text(sql))
    with case.database.engine.begin() as connection:
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(text("UPDATE material_packaging_states SET material_id=:other WHERE execution_id=:id"),
                {"other": uuid4(), "id": values["id"]})
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", case.database.engine.url.render_as_string(hide_password=False)); get_settings.cache_clear()
        with pytest.raises(DBAPIError, match="Packaging execution provenance exists"):
            command.downgrade(Config("alembic.ini"), "20260918_0016")
    with case.database.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0020"


def test_postgresql_packaging_inputs_and_dispatch_cannot_commit_without_ownership_progress(review_pg_case):
    from app.db.models import MaterialPackagingExecution, MaterialPackagingDispatch
    from test_packaging_ownership import reserve, dispatch
    case = review_pg_case; values = _prepare_pg_packaging_execution(case)
    with case.database.session() as session:
        session.add(MaterialPackagingExecution(**values))
        with pytest.raises(DBAPIError, match="matching durable ownership"): session.commit()
        session.rollback()
        execution = reserve(session, values); session.commit()
        session.add(MaterialPackagingDispatch(execution_id=execution.id, ordinal=1, actor_id=execution.actor_id,
            issuer_session_id=execution.issuer_session_id, action="EXECUTE", request_key=uuid4(), request_hash="c" * 64,
            reason="Synthetic incomplete dispatch"))
        with pytest.raises(DBAPIError, match="matching durable ownership"): session.commit()
        session.rollback()
        dispatch(session, session.get(MaterialPackagingExecution, values["id"])); session.commit()


def test_postgresql_packaged_state_requires_current_facts_and_terminal_state_cannot_restart(review_pg_case):
    from app.db.models import MaterialPackagingExecution, MaterialPackagingState
    from test_packaging_ownership import reserve, dispatch, finish
    case = review_pg_case; values = _prepare_pg_packaging_execution(case)
    with case.database.session() as session:
        execution = reserve(session, values); action = dispatch(session, execution); session.commit()
        with pytest.raises(DBAPIError, match="inconsistent"):
            finish(session, execution, action, outcome="READY", target="PACKAGED", current=False); session.commit()
        session.rollback()
        finish(session, execution, action, outcome="READY", target="PACKAGED", current=True); session.commit()
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(text("UPDATE material_packaging_states SET status='RUNNING', last_observation_id=NULL WHERE execution_id=:id"),
                {"id": execution.id})
            session.commit()


@pytest.mark.parametrize("change", ["batch", "policy", "source", "folder", "snapshot"])
def test_postgresql_execution_inputs_cannot_substitute_frozen_batch_or_policy(review_pg_case, change):
    from test_packaging_ownership import reserve
    case = review_pg_case; values = _prepare_pg_packaging_execution(case)
    if change == "batch": values["batch_id"] = uuid4()
    elif change == "policy": values["policy_id"] = uuid4()
    elif change == "source": values["worker_request"]["request"]["source_revision_hash"] = "b" * 64
    elif change == "folder": values["worker_request"]["request"]["parts"] = ["different", "folder"]
    else: values["input_snapshot"]["generation"] += 1
    with case.database.session() as session:
        with pytest.raises(DBAPIError, match="Packaging inputs must bind"): reserve(session, values); session.commit()


def test_postgresql_packaging_execution_upgrade_from_0016_and_empty_downgrade():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260918_0016")
            company_id = uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO companies (id, name) VALUES (:id, 'Prior packaging fixture')"), {"id": company_id})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            for suffix in ("executions", "dispatches", "observations", "states"):
                assert inspect(engine).has_table("material_packaging_" + suffix)
            with engine.connect() as connection:
                assert connection.execute(text("SELECT name FROM companies WHERE id=:id"), {"id": company_id}).scalar_one() == "Prior packaging fixture"
            command.downgrade(config, "20260918_0016")
            assert not inspect(engine).has_table("material_packaging_executions") and inspect(engine).has_table("material_packaging_policies")
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


def _pg_reservation_payload(case):
    values = _prepare_pg_packaging_execution(case)
    return {"idempotency_key": str(uuid4()), "batch_id": str(values["batch_id"]),
        "expected_snapshot_hash": values["input_hash"], "expected_policy_id": str(values["policy_id"]),
        "reason": "Reviewed PostgreSQL reservation fixture"}


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_reservation_concurrency_preserves_one_owner_and_exact_replay(review_pg_case, same_key):
    from app.db.models import MaterialPackagingExecution, MaterialPackagingState
    case = review_pg_case; body = _pg_reservation_payload(case); barrier = Barrier(2)
    case.packaging.callback = lambda: barrier.wait(timeout=15)
    with case.client_for() as first, case.client_for() as second:
        other = body if same_key else {**body, "idempotency_key": str(uuid4())}
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(client.post, case.path + "/packaging-executions", json=payload)
                for client, payload in ((first, body), (second, other))]
            results = [future.result(timeout=25) for future in futures]
        assert sorted(result.status_code for result in results) == ([201, 201] if same_key else [201, 409])
        if same_key: assert results[0].json() == results[1].json()
    assert len(case.packaging.calls) == 2
    with case.database.session() as session:
        records = list(session.scalars(select(MaterialPackagingExecution).where(MaterialPackagingExecution.material_id == case.material.id)))
        assert len(records) == 1 and session.get(MaterialPackagingState, records[0].id).status == "RESERVED"


@pytest.mark.parametrize("change,expected", [("material", 409), ("account", 401), ("policy", 409), ("technical", 409)])
def test_postgresql_reservation_rechecks_after_actual_concurrent_preparation_change(review_pg_case, change, expected):
    from app.db.models import MaterialPackagingExecution, MaterialPackagingPolicy
    from app.packaging_policy import policy_view
    from test_packaging_policy import override_body
    from test_material_approvals import run
    case = review_pg_case; body = _pg_reservation_payload(case); entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Reservation preparation test was not released")
    case.packaging.callback = hold
    with case.client_for() as actor, case.client_for(3) as editor:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, case.path + "/packaging-executions", json=body)
            try:
                assert entered.wait(15)
                if change == "material":
                    assert editor.patch(case.path, json={"material_name": "Changed during preparation"}).status_code == 200
                elif change == "account":
                    assert editor.patch("/api/internal-users/" + str(case.users[0].id), json={"is_active": False}).status_code == 200
                elif change == "technical":
                    assert run(editor, case.path).status_code == 200
                else:
                    with case.database.session() as session:
                        policy = policy_view(session.get(MaterialPackagingPolicy, UUID(body["expected_policy_id"])))
                    assert editor.post(case.path + "/packaging-policy/override",
                        json=override_body(editor, case.path, policy)).status_code == 200
            finally: release.set()
            response = pending.result(timeout=20)
            assert response.status_code == expected
    with case.database.session() as session:
        assert session.scalar(select(MaterialPackagingExecution.id).where(MaterialPackagingExecution.material_id == case.material.id)) is None


@pytest.mark.parametrize("change", ["material", "account"])
def test_postgresql_final_reservation_commit_serializes_with_later_domain_change(review_pg_case, monkeypatch, change):
    from app.api import packaging_jobs
    case = review_pg_case; body = _pg_reservation_payload(case)
    entered = Event(); release = Event(); original = packaging_jobs.approved_inputs; calls = 0
    def hold(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs); calls += 1
        if calls == 2:
            entered.set()
            if not release.wait(15): raise TimeoutError("Reservation commit test was not released")
        return result
    monkeypatch.setattr(packaging_jobs, "approved_inputs", hold)
    with case.client_for() as actor, case.client_for(3) as editor:
        with ThreadPoolExecutor(2) as pool:
            pending = pool.submit(actor.post, case.path + "/packaging-executions", json=body)
            try:
                assert entered.wait(15)
                if change == "material":
                    writing = pool.submit(editor.patch, case.path, json={"material_name": "Blocked by new owner"})
                else:
                    writing = pool.submit(editor.patch, "/api/internal-users/" + str(case.users[0].id), json={"is_active": False})
                with pytest.raises(TimeoutError): writing.result(timeout=0.15)
            finally: release.set()
            saved = pending.result(timeout=20)
            assert saved.status_code == 201 and saved.json()["status"] == "RESERVED"
            assert writing.result(timeout=20).status_code == (409 if change == "material" else 200)
        if change == "account": assert actor.get(case.path + "/packaging-executions").status_code == 401
        assert editor.get(case.path + "/packaging-executions/" + saved.json()["id"]).json()["status"] == "RESERVED"


def test_postgresql_concurrent_closure_cannot_release_or_record_twice(review_pg_case, monkeypatch):
    from app.api import packaging_jobs
    from app.db.models import MaterialPackagingDispatch, MaterialPackagingObservation, MaterialPackagingState
    from test_packaging_reservations import close_body
    case = review_pg_case; body = _pg_reservation_payload(case); entered = Event(); release = Event()
    original = packaging_jobs._audit
    def hold(session, item, actor_id, event, details):
        original(session, item, actor_id, event, details)
        if event == "PACKAGING_CLOSED":
            entered.set()
            if not release.wait(15): raise TimeoutError("Closure test was not released")
    with case.client_for() as first, case.client_for(3) as second:
        saved = first.post(case.path + "/packaging-executions", json=body)
        assert saved.status_code == 201
        identifier = UUID(saved.json()["id"]); path = case.path + "/packaging-executions/" + str(identifier) + "/close"
        closing = close_body()
        monkeypatch.setattr(packaging_jobs, "_audit", hold)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(first.post, path, json=closing)
            try:
                assert entered.wait(15)
                busy = second.post(path, json=close_body())
                assert busy.status_code == 409 and busy.json()["detail"]["code"] == "PACKAGING_DISPATCH_BUSY"
            finally: release.set()
            result = pending.result(timeout=20)
            assert result.status_code == 200 and result.json()["status"] == "REJECTED"
        assert first.post(path, json=closing).json() == result.json()
    with case.database.session() as session:
        actions = list(session.scalars(select(MaterialPackagingDispatch).where(MaterialPackagingDispatch.execution_id == identifier)))
        observations = list(session.scalars(select(MaterialPackagingObservation).where(MaterialPackagingObservation.execution_id == identifier)))
        assert len(actions) == len(observations) == 1
        assert observations[0].outcome == "NOT_STARTED" and session.get(MaterialPackagingState, identifier).status == "REJECTED"


def _pg_dispatch_case(case):
    from test_packaging_actions import DispatchStub, FreshInventory
    from test_packaging_policy import choose
    from test_publication_preflight import prepare_candidate, preview
    from test_publication_batches import PATH, creation
    worker = DispatchStub(); inventory = FreshInventory(case.technical)
    case.packaging.prepare = worker.prepare; case.packaging.dispatch = worker.dispatch
    case.inventory.inventory = inventory.inventory
    adapter = SimpleNamespace(database=case.database, materials=[case.material], client=lambda _: case.client_for())
    prepare_candidate(adapter, case.technical, case.path, report=worker.template["report"])
    with case.client_for() as client:
        policy = choose(client, case.path)
        batch = client.post(PATH, json=creation(preview(client, case.material.id))).json()
        saved = client.post(case.path + "/packaging-executions", json={"idempotency_key": str(uuid4()), "batch_id": batch["id"],
            "expected_snapshot_hash": batch["items"][0]["snapshot_hash"], "expected_policy_id": policy["id"], "reason": "PG dispatch fixture"})
        assert saved.status_code == 201
    return SimpleNamespace(worker=worker, inventory=inventory, saved=saved.json(), id=UUID(saved.json()["id"]),
        path=case.path + "/packaging-executions/" + saved.json()["id"])


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_inflight_dispatch_is_committed_replayable_and_has_no_long_transaction(review_pg_case, same_key):
    from test_packaging_reservations import close_body
    case = review_pg_case; item = _pg_dispatch_case(case); entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Dispatch test worker was not released")
    item.worker.on_dispatch = hold; body = close_body()
    with case.client_for() as actor, case.client_for() as replaying, case.client_for(3) as editor:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, item.path + "/run", json=body)
            try:
                assert entered.wait(15)
                assert editor.get(item.path).json()["status"] == "RUNNING"
                second = replaying.post(item.path + "/run", json=body if same_key else close_body())
                assert second.status_code == (200 if same_key else 409)
                if same_key: assert second.json()["status"] == "RUNNING"
                else: assert second.json()["detail"]["code"] == "PACKAGING_DISPATCH_BUSY"
                assert editor.patch(case.path, json={"material_name": "Still owned"}).status_code == 409
                with case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%'") ) == 0
            finally: release.set()
            result = pending.result(timeout=20)
            assert result.status_code == 200 and result.json()["status"] == "PACKAGED", result.json()
        assert actor.post(item.path + "/run", json=body).json() == result.json()
    assert len(item.worker.commands) == 1


@pytest.mark.parametrize("phase", ["source", "worker"])
def test_postgresql_dispatch_rechecks_revocation_without_losing_factual_output(review_pg_case, phase):
    from app.db.models import MaterialPackagingObservation, MaterialPackagingDispatch
    from test_packaging_reservations import close_body
    case = review_pg_case; item = _pg_dispatch_case(case); entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Revocation test was not released")
    if phase == "source": item.inventory.callback = hold
    else: item.worker.on_dispatch = hold
    with case.client_for() as actor, case.client_for(3) as editor:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, item.path + "/run", json=close_body())
            try:
                assert entered.wait(15)
                assert editor.patch("/api/internal-users/" + str(case.users[0].id), json={"is_active": False}).status_code == 200
            finally: release.set()
            assert pending.result(timeout=20).status_code == 401
        current = editor.get(item.path).json()
        assert current["status"] == ("RESERVED" if phase == "source" else "RECOVERY_REQUIRED")
        with case.database.session() as session:
            observations = list(session.scalars(select(MaterialPackagingObservation).where(MaterialPackagingObservation.execution_id == item.id)))
            actions = list(session.scalars(select(MaterialPackagingDispatch).where(MaterialPackagingDispatch.execution_id == item.id)))
            assert len(observations) == len(actions) == (0 if phase == "source" else 1)
            if observations: assert observations[0].outcome == "READY" and not observations[0].actor_current
        if phase == "worker":
            item.worker.on_dispatch = None
            result = editor.post(item.path + "/reconcile", json=close_body(expected_last_dispatch_id=current["last_dispatch_id"]))
            assert result.status_code == 200 and result.json()["status"] == "PACKAGED", result.json()


@pytest.mark.parametrize("change", ["account", "material"])
def test_postgresql_packaging_acceptance_serializes_with_later_changes(review_pg_case, monkeypatch, change):
    from app.api import packaging_jobs
    from test_packaging_reservations import close_body
    case = review_pg_case; item = _pg_dispatch_case(case); entered = Event(); release = Event()
    original = packaging_jobs.current_inputs; calls = 0
    def hold(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs); calls += 1
        if calls == 3:
            entered.set()
            if not release.wait(15): raise TimeoutError("Acceptance test was not released")
        return result
    monkeypatch.setattr(packaging_jobs, "current_inputs", hold)
    with case.client_for() as actor, case.client_for(3) as editor:
        with ThreadPoolExecutor(2) as pool:
            pending = pool.submit(actor.post, item.path + "/run", json=close_body())
            try:
                assert entered.wait(15)
                if change == "account":
                    writing = pool.submit(editor.patch, "/api/internal-users/" + str(case.users[0].id), json={"is_active": False})
                else: writing = pool.submit(editor.patch, case.path, json={"material_name": "Edited after acceptance"})
                with pytest.raises(TimeoutError): writing.result(timeout=0.15)
            finally: release.set()
            result = pending.result(timeout=20)
            assert result.status_code == 200 and result.json()["status"] == "PACKAGED", result.json()
            assert writing.result(timeout=20).status_code == 200


def test_postgresql_late_dispatch_result_cannot_replace_newer_accepted_progress(review_pg_case, monkeypatch):
    from contextlib import contextmanager
    from app.api import packaging_jobs
    from app.db.models import MaterialPackagingObservation, MaterialPackagingState
    from test_packaging_reservations import close_body
    case = review_pg_case; item = _pg_dispatch_case(case); entered = Event(); release = Event(); leases = []
    original = packaging_jobs.packaging_dispatch_lease
    @contextmanager
    def capture(*args, **kwargs):
        with original(*args, **kwargs) as lease:
            leases.append(lease); yield lease
    monkeypatch.setattr(packaging_jobs, "packaging_dispatch_lease", capture)
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Late result test was not released")
    item.worker.on_dispatch = hold
    with case.client_for() as actor, case.client_for(3) as editor:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, item.path + "/run", json=close_body())
            try:
                assert entered.wait(15)
                with case.database.engine.connect() as connection:
                    # Kill only the captured advisory session in this test's own DB.
                    assert connection.scalar(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid=:pid AND datname=current_database()"),
                        {"pid": leases[0]._pid}) is True
                item.worker.on_dispatch = None
                current = editor.get(item.path).json()
                newer = editor.post(item.path + "/reconcile", json=close_body(expected_last_dispatch_id=current["last_dispatch_id"]))
                assert newer.status_code == 200 and newer.json()["status"] == "PACKAGED", newer.json()
            finally: release.set()
            old = pending.result(timeout=20)
            assert old.status_code == 200 and old.json() == newer.json()
        with case.database.session() as session:
            observed = list(session.scalars(select(MaterialPackagingObservation).where(MaterialPackagingObservation.execution_id == item.id)))
            assert len(observed) == 2 and all(row.outcome == "READY" for row in observed)
            state = session.get(MaterialPackagingState, item.id)
            assert str(state.last_dispatch_id) == newer.json()["last_dispatch_id"]
            assert str(state.last_observation_id) == newer.json()["last_observation_id"]


def test_postgresql_uncertain_dispatch_recovers_and_closes_with_nas_offline(review_pg_case):
    from app.packaging_client import PackagingClientError
    from app.inventory_client import InventoryClientError
    from app.db.models import MaterialPackagingObservation
    from test_packaging_reservations import close_body
    case = review_pg_case; item = _pg_dispatch_case(case)
    with case.client_for() as client:
        item.worker.dispatch_failure = PackagingClientError()
        first = client.post(item.path + "/run", json=close_body())
        assert first.status_code == 200 and first.json()["status"] == "RECOVERY_REQUIRED", first.json()
        item.worker.dispatch_failure = None; item.worker.ready = False; item.inventory.failure = InventoryClientError()
        known = client.post(item.path + "/reconcile", json=close_body(expected_last_dispatch_id=first.json()["last_dispatch_id"]))
        assert known.status_code == 200 and known.json()["status"] == "RETRY_REQUIRED", known.json()
        closed = client.post(item.path + "/close", json=close_body(expected_last_dispatch_id=known.json()["last_dispatch_id"]))
        assert closed.status_code == 200 and closed.json()["status"] == "REJECTED", closed.json()
        with case.database.session() as session:
            observed = session.get(MaterialPackagingObservation, UUID(closed.json()["last_observation_id"]))
            assert observed.outcome == "RETRY_REQUIRED" and observed.worker_result["terminal"] == "CLOSED"


@pytest.mark.parametrize("change", ["account-open", "account-stream", "material-open"])
def test_postgresql_download_reauthorizes_without_transaction_across_stream(review_pg_case, monkeypatch, change):
    from app.api import packaging_downloads
    from test_packaging_downloads import DownloadStub, DATA, FILE_PATH
    from test_packaging_reservations import close_body
    case = review_pg_case; item = _pg_dispatch_case(case)
    transfer = DownloadStub(); entered = Event(); release = Event(); blocks = 0
    case.packaging.open_artifact = transfer.open
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Download test was not released")
    def stream_hold():
        nonlocal blocks
        blocks += 1
        if blocks == 2: hold()
    if change == "account-stream":
        transfer.during = stream_hold
        monkeypatch.setattr(packaging_downloads, "RECHECK_BYTES", 4)
    else: transfer.callback = hold
    with case.client_for() as actor, case.client_for(3) as editor:
        assert actor.post(item.path + "/run", json=close_body()).json()["status"] == "PACKAGED"
        files = actor.get(item.path + "/artifacts").json()
        chosen = next(file for file in files["items"] if file["path"] == FILE_PATH)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.get, item.path + "/artifacts/" + chosen["id"], params={"proof_sha256":files["proof_sha256"]})
            try:
                assert entered.wait(15)
                with case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%'")) == 0
                if change.startswith("account"):
                    assert editor.patch("/api/internal-users/" + str(case.users[0].id), json={"is_active":False}).status_code == 200
                else:
                    assert editor.patch(case.path, json={"material_name":"Edited while historical download is open"}).status_code == 200
            finally: release.set()
            if change == "account-stream":
                with pytest.raises(RuntimeError, match="Packaging transfer interrupted"): pending.result(timeout=20)
            else:
                result = pending.result(timeout=20)
                assert result.status_code == (401 if change == "account-open" else 200)
                if change == "material-open": assert result.content == DATA
                else: assert DATA.decode() not in result.text
        assert editor.get(item.path).json()["status"] == "PACKAGED"
    assert transfer.opened == transfer.closed == 1 and len(item.worker.commands) == 1


@pytest.mark.parametrize("change", ["material", "brand"])
def test_postgresql_staging_preview_holds_current_inputs_until_concurrent_edit_commits(review_pg_case, monkeypatch, change):
    from app import publication_staging
    from test_packaging_reservations import close_body
    case = review_pg_case; item = _pg_dispatch_case(case)
    entered = Event(); release = Event()
    original = publication_staging.current_inputs
    def hold(*args, **kwargs):
        result = original(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            if not release.wait(15): raise TimeoutError("Staging preview was not released")
        return result
    with case.client_for() as reader, case.client_for(3) as editor:
        packaged = reader.post(item.path + "/run", json=close_body()).json()
        assert packaged["status"] == "PACKAGED"
        batch_path = "/api/publication-batches/" + packaged["batch_id"]
        batch = reader.get(batch_path).json()
        payload = {"job_id": str(uuid4()), "expected_snapshot_hash": batch["snapshot_hash"],
            "expected_csv_sha256": batch["csv_sha256"], "packages": [{"material_id": str(case.material.id),
                "execution_id": packaged["id"], "expected_observation_id": packaged["last_observation_id"],
                "expected_proof_sha256": packaged["proof_sha256"]}]}
        before = reader.post(batch_path + "/staging-preview", json=payload)
        assert before.status_code == 200, before.json()
        monkeypatch.setattr(publication_staging, "current_inputs", hold)
        with ThreadPoolExecutor(2) as pool:
            pending = pool.submit(reader.post, batch_path + "/staging-preview", json=payload)
            try:
                assert entered.wait(15)
                writing = pool.submit(editor.patch, case.path if change == "material" else "/api/brands/" + str(case.material.published_brand_id),
                    json={"material_name": "Changed after staging preview"} if change == "material" else {"name": "Changed brand after staging preview"})
                with pytest.raises(TimeoutError): writing.result(timeout=.15)
            finally: release.set()
            response = pending.result(timeout=25)
            assert response.status_code == 200 and response.json() == before.json()
            assert writing.result(timeout=25).status_code == 200
        assert reader.post(batch_path + "/staging-preview", json=payload).status_code == 409
        assert reader.get(item.path + "/artifacts").status_code == 200
    assert len(item.worker.commands) == 1 and len(item.inventory.calls) == 2


def _pg_staging_case(case):
    from test_packaging_reservations import close_body
    item = _pg_dispatch_case(case)
    with case.client_for() as client:
        packaged = client.post(item.path + "/run", json=close_body()).json()
        assert packaged["status"] == "PACKAGED"
        batch_path = "/api/publication-batches/" + packaged["batch_id"]
        batch = client.get(batch_path).json()
        preview_body = {"job_id": str(uuid4()), "expected_snapshot_hash": batch["snapshot_hash"],
            "expected_csv_sha256": batch["csv_sha256"], "packages": [{"material_id": str(case.material.id),
                "execution_id": packaged["id"], "expected_observation_id": packaged["last_observation_id"],
                "expected_proof_sha256": packaged["proof_sha256"]}]}
        preview = client.post(batch_path + "/staging-preview", json=preview_body)
        assert preview.status_code == 200, preview.json()
    body = {**preview_body, "batch_id": batch["id"], "expected_plan_sha256": preview.json()["plan_sha256"],
        "idempotency_key": str(uuid4()), "reason": "Synthetic PG staging reservation"}
    return item, body


@pytest.mark.parametrize("same_key", [True, False])
def test_postgresql_staging_reservation_replay_and_exclusive_active_material(review_pg_case, same_key):
    from app.db.models import PublicationStagingOwner
    from test_staging_reservations import PATH, close_payload
    case = review_pg_case; item, body = _pg_staging_case(case)
    barrier = Barrier(2)
    with case.client_for() as first, case.client_for() as second:
        def create(client, payload):
            barrier.wait(timeout=10)
            return client.post(PATH, json=payload)
        other = dict(body) if same_key else {**body, "idempotency_key": str(uuid4())}
        with ThreadPoolExecutor(2) as pool:
            pending = [pool.submit(create, first, body), pool.submit(create, second, other)]
            results = [future.result(timeout=30) for future in pending]
        assert sorted(response.status_code for response in results) == ([201, 201] if same_key else [201, 409])
        if same_key: assert results[0].json() == results[1].json()
        saved = next(response.json() for response in results if response.status_code == 201)
        closure = close_payload(saved)
        barrier = Barrier(2)
        def close(client):
            barrier.wait(timeout=10)
            return client.post(PATH + "/" + saved["id"] + "/close", json=closure)
        with ThreadPoolExecutor(2) as pool:
            pending = [pool.submit(close, client) for client in (first, second)]
            closed = [future.result(timeout=30) for future in pending]
        assert all(response.status_code == 200 for response in closed)
        assert closed[0].json() == closed[1].json() and closed[0].json()["status"] == "CLOSED"
        assert first.patch(case.path, json={"material_name": "Changed after staging close"}).status_code == 200
        assert first.get(item.path + "/artifacts").status_code == 200
    with case.database.session() as session:
        owners = list(session.scalars(select(PublicationStagingOwner).where(PublicationStagingOwner.material_id == case.material.id)))
        assert len(owners) == 1 and not owners[0].active


def test_postgresql_staging_history_and_released_ownership_are_preserved(review_pg_case):
    from test_staging_reservations import PATH, close_payload
    case = review_pg_case; _, body = _pg_staging_case(case)
    with case.client_for() as client:
        response = client.post(PATH, json=body)
        assert response.status_code == 201, response.json()
        saved = response.json()
        assert client.post(PATH + "/" + saved["id"] + "/close", json=close_payload(saved)).status_code == 200
    for table in ("publication_staging_jobs", "publication_staging_items", "publication_staging_closes"):
        for sql in (f"UPDATE {table} SET job_id=job_id" if table != "publication_staging_jobs" else f"UPDATE {table} SET id=id",
                    f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
            with case.database.engine.begin() as connection:
                with pytest.raises(DBAPIError, match="append-only"): connection.execute(text(sql))
    for sql in ("DELETE FROM publication_staging_owners", "TRUNCATE publication_staging_owners"):
        with case.database.engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"): connection.execute(text(sql))
    with case.database.engine.begin() as connection:
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(text("UPDATE publication_staging_owners SET active=true,close_id=NULL WHERE job_id=:id"), {"id": saved["id"]})
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", case.database.engine.url.render_as_string(hide_password=False)); get_settings.cache_clear()
        with pytest.raises(DBAPIError, match="Staging reservation provenance exists"):
            command.downgrade(Config("alembic.ini"), "20260918_0017")
    with case.database.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0020"


def _clone_staging_values(case, saved):
    from app.db.models import PublicationStagingJob, PublicationStagingItem
    from app.material_review import canonical_hash
    with case.database.session() as session:
        original = session.get(PublicationStagingJob, UUID(saved["id"]))
        job = {column.name: getattr(original, column.name) for column in PublicationStagingJob.__table__.columns if column.name != "created_at"}
        original_item = session.get(PublicationStagingItem, (original.id, case.material.id))
        item = {column.name: getattr(original_item, column.name) for column in PublicationStagingItem.__table__.columns}
    identifier = uuid4()
    plan = json.loads(json.dumps(job["plan"]))
    plan["body"]["job_id"] = str(identifier); plan["sha256"] = canonical_hash(plan["body"])
    job.update(id=identifier, request_key=uuid4(), request_hash="e" * 64, plan=plan, plan_sha256=plan["sha256"])
    item["job_id"] = identifier
    return job, item


@pytest.mark.parametrize("omit", ["items", "owners", "release"])
def test_postgresql_staging_cannot_commit_partial_reservation_or_closure(review_pg_case, omit):
    from app.db.models import PublicationStagingJob, PublicationStagingItem, PublicationStagingClose, PublicationStagingState
    from test_staging_reservations import PATH
    case = review_pg_case; _, body = _pg_staging_case(case)
    with case.client_for() as client:
        saved = client.post(PATH, json=body).json()
    job, item = _clone_staging_values(case, saved)
    with case.database.session() as session:
        if omit == "release":
            closed = PublicationStagingClose(job_id=UUID(saved["id"]), actor_id=case.users[0].id,
                issuer_session_id=uuid4(), request_key=uuid4(), request_hash="d" * 64, reason="Incomplete close")
            session.add(closed); session.flush()
            state = session.get(PublicationStagingState, closed.job_id)
            state.status = "CLOSED"; state.close_id = closed.id
        else:
            session.add(PublicationStagingJob(**job)); session.flush()
            session.add(PublicationStagingState(job_id=job["id"], status="RESERVED"))
            if omit == "owners": session.add(PublicationStagingItem(**item))
        with pytest.raises(DBAPIError, match="complete consistent durable ownership"):
            session.commit()


@pytest.mark.parametrize("operation", ["identity", "packaging"])
def test_postgresql_staging_ownership_excludes_direct_competing_claims_in_both_directions(review_pg_case, operation):
    from app.db.models import (MaterialPackagingExecution, PublicationStagingJob, PublicationStagingItem,
        PublicationStagingOwner, PublicationStagingState)
    from test_packaging_ownership import execution_values, reserve
    from test_staging_reservations import PATH, close_payload
    case = review_pg_case; package, body = _pg_staging_case(case)
    with case.client_for() as client:
        saved = client.post(PATH, json=body).json()
        assert client.post(PATH + "/" + saved["id"] + "/close", json=close_payload(saved)).status_code == 200
    job, item = _clone_staging_values(case, saved)
    with case.database.session() as session:
        execution = session.get(MaterialPackagingExecution, package.id)
        competing = execution_values(session, execution.batch_id, case.material.id, execution.policy_id, case.users[0].id)
    barrier = Barrier(2)
    def claim(staging):
        with case.database.session() as session:
            barrier.wait(timeout=10)
            try:
                if staging:
                    session.add(PublicationStagingJob(**job)); session.flush()
                    session.add(PublicationStagingState(job_id=job["id"], status="RESERVED"))
                    session.add(PublicationStagingItem(**item)); session.flush()
                    session.add(PublicationStagingOwner(job_id=job["id"], material_id=case.material.id, active=True))
                elif operation == "identity": session.add(_packaging_identity_fixture(case))
                else: reserve(session, competing)
                session.commit()
                return "claimed"
            except DBAPIError as error:
                assert "active staging reservation" in str(error.orig) or "another operation" in str(error.orig)
                return "blocked"
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(claim, value) for value in (True, False)]
        assert sorted(future.result(timeout=30) for future in futures) == ["blocked", "claimed"]


def test_postgresql_staging_upgrade_from_0017_preserves_prior_data_and_empty_downgrade():
    with isolated_postgresql_database() as database_url:
        previous = os.environ.get("DATABASE_URL"); os.environ["DATABASE_URL"] = database_url; get_settings.cache_clear()
        engine = create_engine(database_url)
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260918_0017")
            identifier = uuid4()
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO companies (id,name) VALUES (:id,'Prior staging fixture')"), {"id": identifier})
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            for suffix in ("jobs", "items", "owners", "closes"):
                assert inspect(engine).has_table("publication_staging_" + suffix)
            with engine.connect() as connection:
                assert connection.execute(text("SELECT name FROM companies WHERE id=:id"), {"id": identifier}).scalar_one() == "Prior staging fixture"
            command.downgrade(config, "20260918_0017")
            assert not inspect(engine).has_table("publication_staging_jobs")
            command.upgrade(config, "head"); command.check(config)
        finally:
            engine.dispose()
            if previous is None: os.environ.pop("DATABASE_URL", None)
            else: os.environ["DATABASE_URL"] = previous
            get_settings.cache_clear()


def test_postgresql_reserved_staging_recheck_holds_ownership_until_transaction_finishes(review_pg_case):
    from fastapi import HTTPException
    from app.publication_staging import reserved_staging
    from test_reserved_staging_inputs import access_for
    from test_staging_reservations import PATH, close_payload
    case = review_pg_case; _, body = _pg_staging_case(case)
    entered = Event(); release = Event()
    with case.client_for() as client, case.client_for(3) as closer:
        response = client.post(PATH, json=body)
        assert response.status_code == 201, response.json()
        saved = response.json()
        def recheck():
            with case.database.session() as session:
                prepared = reserved_staging(session, UUID(saved["id"]), access_for(session, saved["id"]), case.app.state.settings)
                entered.set()
                if not release.wait(15): raise TimeoutError("Reserved staging recheck was not released")
                assert prepared.plan.sha256 == saved["plan_sha256"]
            return prepared
        with ThreadPoolExecutor(2) as pool:
            reading = pool.submit(recheck)
            try:
                assert entered.wait(15)
                closing = pool.submit(closer.post, PATH + "/" + saved["id"] + "/close", json=close_payload(saved))
                with pytest.raises(TimeoutError): closing.result(timeout=.15)
            finally: release.set()
            assert reading.result(timeout=20).plan.sha256 == saved["plan_sha256"]
            closed = closing.result(timeout=20)
            assert closed.status_code == 200 and closed.json()["status"] == "CLOSED", closed.json()
        with case.database.session() as session:
            with pytest.raises(HTTPException) as blocked:
                reserved_staging(session, UUID(saved["id"]), access_for(session, saved["id"]), case.app.state.settings)
            assert blocked.value.status_code == 409 and blocked.value.detail["code"] == "GCS_STAGING_ALREADY_CLOSED"


@pytest.fixture
def staging_history_pg(review_pg_case):
    from test_staging_reservations import PATH
    case = review_pg_case; _, body = _pg_staging_case(case)
    with case.client_for() as client:
        response = client.post(PATH, json=body)
        assert response.status_code == 201, response.json()
    return case, UUID(response.json()["id"])


@pytest.mark.parametrize("missing", ["reservation", "dispatch", "result", "closure"])
def test_postgresql_staging_journal_requires_atomic_matching_progress(staging_history_pg, missing):
    import staging_history_support as journal
    from app.db.models import PublicationStagingJob, PublicationStagingState
    case, identifier = staging_history_pg
    if missing == "reservation":
        # Exercise a complete valid 0018 reservation; the only missing part is new progress.
        with case.database.session() as session:
            journal.close(session, identifier, dispatched=False); session.commit()
        job, item = _clone_staging_values(case, {"id": str(identifier)})
        from app.db.models import PublicationStagingItem, PublicationStagingOwner
        with case.database.session() as session:
            session.add(PublicationStagingJob(**job)); session.flush()
            session.add(PublicationStagingItem(**item)); session.flush()
            session.add(PublicationStagingOwner(job_id=job["id"], material_id=case.material.id, active=True))
            with pytest.raises(DBAPIError, match="requires durable progress"): session.commit()
        return
    with case.database.session() as session:
        dispatched = journal.dispatch(session, identifier, progress=missing != "dispatch")
        if missing == "result": journal.result(session, dispatched, progress=False)
        if missing == "closure":
            journal.close(session, identifier, progress=False)
        with pytest.raises(DBAPIError, match="(progress|closed state)"):
            session.commit()


@pytest.mark.parametrize("change", ["ordinal", "previous", "action", "plan", "closed"])
def test_postgresql_staging_dispatch_cannot_rewrite_or_fork_history(staging_history_pg, change):
    import staging_history_support as journal
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier); session.commit()
        if change == "closed": journal.close(session, identifier); session.commit()
        changes = {"ordinal": {"ordinal": 4}, "previous": {"previous_dispatch_id": uuid4()},
            "action": {"action": "EXECUTE"}, "plan": {"plan_sha256": "f" * 64}, "closed": {}}[change]
        with pytest.raises(DBAPIError):
            journal.dispatch(session, identifier, **changes); session.commit()
        session.rollback()
        assert session.scalar(select(journal.PublicationStagingState.status).where(
            journal.PublicationStagingState.job_id == identifier)) == ("CLOSED" if change == "closed" else "RUNNING")


@pytest.mark.parametrize("kind", ["missing-observation", "uncertain", "wrong-object", "early-marker", "stale", "after-result"])
def test_postgresql_staging_transfer_requires_exact_verified_order(staging_history_pg, kind):
    import staging_history_support as journal
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier)
        intent = journal.transfer(session, first)
        if kind == "uncertain": journal.observe(session, intent, outcome="UNCERTAIN", receipt=None, failure_code="GCS_OUTCOME_UNCERTAIN")
        elif kind not in {"missing-observation", "early-marker"}: journal.observe(session, intent)
        if kind == "stale": journal.dispatch(session, identifier)
        if kind == "after-result": journal.result(session, first)
        session.commit()
        with pytest.raises(DBAPIError):
            if kind == "early-marker":
                session.add(journal.PublicationStagingTransfer(job_id=identifier, dispatch_id=first.id, ordinal=2,
                    kind="MARKER", relative_path="_reawote/complete.json", size=5, sha256="a" * 64))
            else:
                journal.transfer(session, first, 2, **({"sha256": "0" * 64} if kind == "wrong-object" else {}))
            session.commit()


@pytest.mark.parametrize("corruption", ["bucket", "path", "binding", "size", "generation-type", "generation-range", "extra", "null", "failure"])
def test_postgresql_staging_receipt_rejects_unbound_or_malformed_evidence(staging_history_pg, corruption):
    import staging_history_support as journal
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier); intent = journal.transfer(session, first); session.commit()
        receipt = journal.receipt(session, intent)
        if corruption == "bucket": receipt["bucket_name"] = "wrong-bucket"
        elif corruption == "path": receipt["object_name"] += ".changed"
        elif corruption == "binding": receipt["spec"]["binding_sha256"] = "0" * 64
        elif corruption == "size": receipt["spec"]["size"] = str(receipt["spec"]["size"])
        elif corruption == "generation-type": receipt["generation"] = 123
        elif corruption == "generation-range": receipt["generation"] = str(2**63)
        elif corruption == "extra": receipt["untrusted"] = "not allowed"
        elif corruption == "null": receipt = None
        changes = dict(outcome="UNCERTAIN", receipt=None, failure_code="arbitrary diagnostic") if corruption == "failure" else dict(receipt=receipt)
        with pytest.raises(DBAPIError): journal.observe(session, intent, **changes); session.commit()


@pytest.mark.parametrize("acceptance", [None, "inputs_current", "actor_current", "lease_current"])
def test_postgresql_staging_complete_evidence_and_current_authority_are_separate(staging_history_pg, acceptance):
    import staging_history_support as journal
    from test_staging_reservations import PATH, close_payload
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier); marker = journal.complete(session, first)
        result = journal.result(session, first, marker=marker, **({acceptance: False} if acceptance else {})); session.commit()
        state = session.get(journal.PublicationStagingState, identifier)
        assert state.status == ("RECOVERY_REQUIRED" if acceptance else "STAGED_VERIFIED")
        assert result.outcome == "VERIFIED" and state.last_result_id == result.id
        assert session.scalar(select(journal.PublicationStagingOwner.active).where(journal.PublicationStagingOwner.job_id == identifier))
        if acceptance:
            state.status = "STAGED_VERIFIED"
            with pytest.raises(DBAPIError, match="acceptance checks"): session.commit()
            session.rollback()
    with case.client_for() as client:
        saved = client.get(PATH + "/" + str(identifier)).json()
        response = client.post(PATH + "/" + str(identifier) + "/close", json=close_payload(saved))
        assert response.status_code == 409 and response.json()["detail"]["code"] == "GCS_STAGING_ALREADY_DISPATCHED"
        assert client.get(case.path).json()["is_published"] is False


def test_postgresql_staging_result_requires_marker_and_exact_coverage(staging_history_pg):
    import staging_history_support as journal
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier); data = journal.observe(session, journal.transfer(session, first)); session.commit()
        with pytest.raises(DBAPIError, match="exact complete receipt coverage"):
            journal.result(session, first, marker=data); session.commit()


@pytest.mark.parametrize("closed", [False, True])
def test_postgresql_staging_late_facts_preserve_newer_and_closed_progress(staging_history_pg, closed):
    import staging_history_support as journal
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier); intent = journal.transfer(session, first); session.commit()
        latest = journal.dispatch(session, identifier); session.commit()
        if closed: journal.close(session, identifier); session.commit()
        journal.observe(session, intent)
        journal.result(session, first, progress=False); session.commit()
        state = session.get(journal.PublicationStagingState, identifier)
        assert state.last_dispatch_id == latest.id and state.last_result_id is None
        assert state.status == ("CLOSED" if closed else "RUNNING")
        if closed:
            # A final result for the latest dispatch may arrive after explicit closure too.
            journal.result(session, latest, progress=False); session.commit()
            with pytest.raises(DBAPIError, match="closed state"):
                session.execute(text("UPDATE publication_staging_states SET status='RUNNING',close_id=NULL WHERE job_id=:id"), {"id": identifier})


def test_postgresql_staging_journal_is_append_only_and_populated_downgrade_refuses(staging_history_pg):
    import staging_history_support as journal
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier); journal.result(session, first); session.commit()
        with pytest.raises(DBAPIError, match="acknowledge"):
            journal.close(session, identifier, dispatched=False); session.commit()
    for table in ("dispatches", "transfers", "observations", "results"):
        for operation in ("UPDATE publication_staging_{table} SET id=id", "DELETE FROM publication_staging_{table}", "TRUNCATE publication_staging_{table} CASCADE"):
            with case.database.engine.begin() as connection:
                with pytest.raises(DBAPIError, match="append-only"):
                    connection.execute(text(operation.format(table=table)))
    for operation in ("DELETE FROM publication_staging_states", "TRUNCATE publication_staging_states"):
        with case.database.engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"): connection.execute(text(operation))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", case.database.engine.url.render_as_string(hide_password=False)); get_settings.cache_clear()
        with pytest.raises(DBAPIError, match="Staging dispatch provenance exists"):
            command.downgrade(Config("alembic.ini"), "20260918_0018")
    with case.database.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0020"


def test_postgresql_staging_dispatch_commit_serializes_against_competing_dispatch(staging_history_pg):
    import staging_history_support as journal
    case, identifier = staging_history_pg
    entered = Event(); release = Event()
    def held():
        with case.database.session() as session:
            first = journal.dispatch(session, identifier)
            entered.set(); assert release.wait(15)
            session.commit(); return first.id
    def competing():
        with case.database.session() as session:
            # Both competitors propose the first ordinal before either commits.
            try:
                journal.dispatch(session, identifier); session.commit()
            except DBAPIError:
                return "blocked"
            return "accepted"
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(held)
        try:
            assert entered.wait(15); other = pool.submit(competing)
            with pytest.raises(TimeoutError): other.result(timeout=.2)
        finally: release.set()
        identifier_first = first.result(timeout=20)
        assert other.result(timeout=20) == "blocked"
    with case.database.session() as session:
        state = session.get(journal.PublicationStagingState, identifier)
        assert state.last_dispatch_id == identifier_first and state.status == "RUNNING"


def test_postgresql_staging_unsent_closure_and_first_dispatch_cannot_both_commit(staging_history_pg):
    import staging_history_support as journal
    case, identifier = staging_history_pg; barrier = Barrier(2)
    def claim(action):
        with case.database.session() as session:
            barrier.wait(timeout=10)
            try:
                if action == "close": journal.close(session, identifier, dispatched=False)
                else: journal.dispatch(session, identifier)
                session.commit(); return action
            except DBAPIError:
                return "blocked"
    with ThreadPoolExecutor(2) as pool:
        pending = [pool.submit(claim, action) for action in ("close", "dispatch")]
        outcomes = [future.result(timeout=20) for future in pending]
    assert outcomes.count("blocked") == 1
    with case.database.session() as session:
        state = session.get(journal.PublicationStagingState, identifier)
        assert state.status == ("CLOSED" if "close" in outcomes else "RUNNING")


def test_postgresql_staging_0019_backfills_open_and_closed_0018_jobs_without_changing_history():
    from test_staging_reservations import PATH, close_payload
    from app.db.models import PublicationStagingJob
    with isolated_postgresql_database() as database_url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", database_url); get_settings.cache_clear()
        config = Config("alembic.ini"); command.upgrade(config, "head")
        fixture = _review_pg_case(database_url); case = next(fixture)
        try:
            _, body = _pg_staging_case(case)
            with case.client_for() as client:
                closed = client.post(PATH, json=body).json()
                assert client.post(PATH + "/" + closed["id"] + "/close", json=close_payload(closed)).status_code == 200
                # A fresh namespace for the same material can be reserved after release.
                job, _ = _clone_staging_values(case, closed)
                body.update(job_id=str(job["id"]), idempotency_key=str(uuid4()), expected_plan_sha256=job["plan_sha256"])
                opened = client.post(PATH, json=body)
                assert opened.status_code == 201, opened.json()
                opened = opened.json()
            with case.database.session() as session:
                frozen = {str(row.id): row.plan for row in session.scalars(select(PublicationStagingJob))}
            # Remove only the empty 0019 journal. Both complete 0018 reservations persist.
            command.downgrade(config, "20260918_0018")
            assert not inspect(case.database.engine).has_table("publication_staging_states")
            with case.database.engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM publication_staging_jobs")).scalar_one() == 2
                assert connection.execute(text("SELECT count(*) FROM publication_staging_closes")).scalar_one() == 1
                assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260918_0018"
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            with case.client_for() as client:
                assert client.get(PATH + "/" + closed["id"]).json()["status"] == "CLOSED"
                assert client.get(PATH + "/" + opened["id"]).json()["status"] == "RESERVED"
                assert client.post(PATH + "/" + opened["id"] + "/close", json=close_payload(opened)).status_code == 200
            with case.database.session() as session:
                assert {str(row.id): row.plan for row in session.scalars(select(PublicationStagingJob))} == frozen
        finally:
            fixture.close(); get_settings.cache_clear()


def test_postgresql_staging_abandonment_respects_lease_and_retains_late_facts(staging_history_pg):
    import staging_history_support as journal
    from app.staging_dispatch_lease import staging_dispatch_lease
    from test_staging_reservations import PATH
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier); intent = journal.transfer(session, first); session.commit()
        job = session.get(journal.PublicationStagingJob, identifier)
        payload = dict(idempotency_key=str(uuid4()), expected_plan_sha256=job.plan_sha256,
            expected_last_dispatch_id=str(first.id), reason="Acknowledge uncertain synthetic side effects",
            acknowledge_possible_remote_effects=True)
    path = PATH + "/" + str(identifier)
    with case.client_for() as admin:
        with staging_dispatch_lease(case.database.engine, identifier):
            blocked = admin.post(path + "/abandon", json=payload)
            assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "GCS_DISPATCH_BUSY"
        closed = admin.post(path + "/abandon", json=payload)
        assert closed.status_code == 200 and closed.json()["close"]["dispatched"] is True
        assert closed.json()["last_dispatch_id"] == str(first.id) and closed.json()["last_result_id"] is None
        with case.database.session() as session:
            journal.observe(session, session.get(journal.PublicationStagingTransfer, intent.id))
            journal.result(session, session.get(journal.PublicationStagingDispatch, first.id), progress=False); session.commit()
        assert admin.post(path + "/abandon", json=payload).json() == closed.json()
        history = admin.get(path + "/dispatches").json()
        assert history["items"][0]["result"]["outcome"] == "UNCERTAIN"
        transfers = admin.get(path + "/dispatches/" + str(first.id) + "/transfers").json()
        assert transfers["items"][0]["observation"]["outcome"] == "VERIFIED"
        assert admin.patch(case.path, json={"material_name": "Editable after abandoned transfer"}).status_code == 200


def test_postgresql_staging_abandonment_serializes_duplicate_requests(staging_history_pg):
    import staging_history_support as journal
    from test_staging_reservations import PATH
    case, identifier = staging_history_pg
    with case.database.session() as session:
        first = journal.dispatch(session, identifier); session.commit()
        job = session.get(journal.PublicationStagingJob, identifier)
        payload = dict(idempotency_key=str(uuid4()), expected_plan_sha256=job.plan_sha256,
            expected_last_dispatch_id=str(first.id), reason="Concurrent synthetic abandonment",
            acknowledge_possible_remote_effects=True)
    barrier = Barrier(2); path = PATH + "/" + str(identifier) + "/abandon"
    with case.client_for() as one, case.client_for() as two:
        def request(client):
            barrier.wait(timeout=10); return client.post(path, json=payload)
        with ThreadPoolExecutor(2) as pool:
            pending = [pool.submit(request, client) for client in (one, two)]
            responses = [future.result(timeout=20) for future in pending]
        assert 200 in [response.status_code for response in responses]
        for response in responses:
            if response.status_code == 409: assert response.json()["detail"]["code"] == "GCS_DISPATCH_BUSY"
            else: assert response.status_code == 200
        assert one.post(path, json=payload).json() == two.post(path, json=payload).json()
    with case.database.session() as session:
        assert len(list(session.scalars(select(journal.PublicationStagingClose).where(
            journal.PublicationStagingClose.job_id == identifier)))) == 1
        assert len(list(session.scalars(select(MaterialAuditEvent).where(
            MaterialAuditEvent.material_id == case.material.id,
            MaterialAuditEvent.event_type == "PUBLICATION_STAGING_ABANDONED")))) == 1


@pytest.fixture
def staging_runtime_pg(review_pg_case):
    from test_staging_runtime import make_runtime_case
    from test_application_access import PASSWORD, ORIGIN
    case = review_pg_case; package = _pg_dispatch_case(case)
    # This fixture replaces the app to inject the synthetic cloud transport.
    # The shared fixture's factory closes over its original, GCS-disabled app.
    def client_for(index=0):
        client = TestClient(case.app, base_url=ORIGIN)
        response = client.post("/api/auth/login", json={"email": case.users[index].email,
            "password": PASSWORD}, headers={"Origin": ORIGIN})
        assert response.status_code == 200
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
        return client
    case.client_for = client_for
    class Adapter:
        database = case.database
        worker = None  # No metadata/preflight endpoint is used by this fixture.
        @property
        def app(self): return case.app
        @app.setter
        def app(self, value): case.app = value
        def client(self, role):
            assert role == "ADMIN"
            return case.client_for()
    with case.client_for() as client:
        batch = client.get("/api/publication-batches/" + package.saved["batch_id"]).json()
    item = make_runtime_case(SimpleNamespace(case=Adapter(), worker=package.worker, inventory=package.inventory,
        technical=case.technical, material=case.material, material_path=case.path, job_path=package.path, batch=batch))
    return case, item


@pytest.mark.parametrize("change", [None, "revoke", "source"])
def test_postgresql_staging_upload_has_committed_intent_and_no_transaction_over_io(staging_runtime_pg, change):
    import anyio
    from app.db.models import PublicationStagingTransfer, PublicationStagingResult
    from test_staging_runtime import body
    from test_staging_reservations import PATH
    case, item = staging_runtime_pg; entered = Event(); release = Event()
    async def hold(spec):
        if not entered.is_set():
            entered.set()
            if not await anyio.to_thread.run_sync(release.wait, 20): raise TimeoutError("Synthetic upload was not released")
    item.cloud.before = hold; payload = body(item).model_dump(mode="json")
    path = PATH + "/" + item.job["id"]
    with case.client_for() as actor, case.client_for() as replaying, case.client_for(3) as admin:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, path + "/run", json=payload)
            try:
                assert entered.wait(20)
                assert replaying.post(path + "/run", json=payload).json()["status"] == "RUNNING"
                with case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%'")) == 0
                with case.database.session() as session:
                    assert session.scalar(select(PublicationStagingTransfer.id).where(PublicationStagingTransfer.job_id == item.identifier))
                assert admin.patch(case.path, json={"material_name": "Still owned during upload"}).status_code == 409
                if change == "revoke":
                    assert admin.patch("/api/internal-users/" + str(case.users[0].id), json={"is_active": False}).status_code == 200
                elif change == "source": item.inventory.changed = True
            finally: release.set()
            response = pending.result(timeout=30)
            assert response.status_code == (401 if change == "revoke" else 200), response.json()
            if response.status_code == 200:
                assert response.json()["status"] == ("RECOVERY_REQUIRED" if change else "STAGED_VERIFIED")
        assert admin.get(path).json()["status"] == ("RECOVERY_REQUIRED" if change else "STAGED_VERIFIED")
        assert admin.get(case.path).json()["is_published"] is False
    with case.database.session() as session:
        result = session.scalar(select(PublicationStagingResult).where(PublicationStagingResult.job_id == item.identifier))
        assert result.inputs_current is (change is None) and result.actor_current is (change != "revoke")


def test_postgresql_staging_lost_dispatch_session_cannot_accept_and_read_only_recovery_succeeds(staging_runtime_pg):
    from app.db.models import PublicationStagingResult
    from app.dispatch_lease import _key
    from test_staging_runtime import body
    from test_staging_reservations import PATH
    case, item = staging_runtime_pg
    key = _key(item.identifier, b"reawote/staging-dispatch/v1/") & ((1 << 64) - 1)
    async def lose(spec):
        if spec.relative_path != "_reawote/complete.json": return
        with case.database.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            pid = connection.scalar(text("""SELECT pid FROM pg_locks WHERE locktype='advisory'
                AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
                AND classid::bigint=:high AND objid::bigint=:low AND objsubid=1 AND granted"""),
                {"high": key >> 32, "low": key & 0xffffffff})
            assert pid is not None and connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    item.cloud.after = lose; path = PATH + "/" + item.job["id"]
    with case.client_for() as client:
        first = client.post(path + "/run", json=body(item).model_dump(mode="json"))
        assert first.status_code == 200 and first.json()["status"] == "RECOVERY_REQUIRED", first.json()
        assert "_reawote/complete.json" in item.cloud.objects
        item.cloud.after = None; uploads = len(item.cloud.calls)
        recovered = client.post(path + "/reconcile", json=body(item,
            expected_last_dispatch_id=UUID(first.json()["last_dispatch_id"])).model_dump(mode="json"))
        assert recovered.status_code == 200 and recovered.json()["status"] == "STAGED_VERIFIED", recovered.json()
        assert all(method == "reconcile" for method, _ in item.cloud.calls[uploads:])
    with case.database.session() as session:
        results = list(session.scalars(select(PublicationStagingResult).where(PublicationStagingResult.job_id == item.identifier)))
        assert len(results) == 2 and sum(result.lease_current for result in results) == 1


def test_postgresql_staging_old_response_cannot_replace_new_backend_reconciliation(staging_runtime_pg):
    import anyio
    from app.main import create_app as authenticated_app
    from app.db.models import PublicationStagingResult, PublicationStagingState
    from app.dispatch_lease import _key
    from test_staging_runtime import body
    from test_staging_reservations import PATH
    case, item = staging_runtime_pg; entered = Event(); release = Event()
    async def hold(spec):
        if spec.relative_path == "_reawote/complete.json" and not entered.is_set():
            entered.set()
            if not await anyio.to_thread.run_sync(release.wait, 25): raise TimeoutError("Late marker response was not released")
    item.cloud.after = hold; path = PATH + "/" + item.job["id"]
    with case.client_for() as original:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(original.post, path + "/run", json=body(item).model_dump(mode="json"))
            try:
                assert entered.wait(20)
                first = original.get(path).json()
                key = _key(item.identifier, b"reawote/staging-dispatch/v1/") & ((1 << 64) - 1)
                with case.database.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    pid = connection.scalar(text("""SELECT pid FROM pg_locks WHERE locktype='advisory'
                        AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
                        AND classid::bigint=:high AND objid::bigint=:low AND objsubid=1 AND granted"""),
                        {"high": key >> 32, "low": key & 0xffffffff})
                    assert pid is not None and connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
                case.app = authenticated_app(item.settings, case.database, inventory_client=item.inventory,
                    technical_client=item.technical, packaging_client=item.worker, gcs_client=item.cloud)
                with case.client_for() as replacement:
                    current = replacement.post(path + "/reconcile", json=body(item,
                        expected_last_dispatch_id=UUID(first["last_dispatch_id"])).model_dump(mode="json"))
                    assert current.status_code == 200 and current.json()["status"] == "STAGED_VERIFIED", current.json()
                    accepted = current.json()
            finally: release.set()
            late = pending.result(timeout=20)
            assert late.status_code == 200 and late.json() == accepted
    with case.database.session() as session:
        state = session.get(PublicationStagingState, item.identifier)
        assert str(state.last_dispatch_id) == accepted["last_dispatch_id"] and state.status == "STAGED_VERIFIED"
        results = list(session.scalars(select(PublicationStagingResult).where(PublicationStagingResult.job_id == item.identifier)))
        assert len(results) == 2
        old = next(result for result in results if str(result.dispatch_id) == first["last_dispatch_id"])
        assert old.outcome == "UNCERTAIN" and not old.lease_current and not old.inputs_current


@pytest.mark.parametrize("change", [None, "company", "link", "role", "revoke"])
def test_postgresql_notion_comparison_rechecks_concurrent_changes_without_holding_transaction(review_pg_case, change):
    import anyio
    from app.main import create_app as authenticated_app
    from app.notion_reader import configuration_from_settings
    from test_application_access import PASSWORD, ORIGIN
    from test_notion_preview import enabled_settings
    from test_notion_reader import Server, schema, page
    case = review_pg_case; server = Server(); identifier = str(uuid4())
    settings = enabled_settings(case.app.state.settings)
    reader = server.client(configuration_from_settings(settings))
    with case.database.session() as session:
        company = session.get(Project, case.material.project_id).company
        company.notion_page_id = identifier; session.commit(); company_id = company.id
    case.app = authenticated_app(settings, case.database, notion_reader=reader)
    def client_for(index=0):
        client = TestClient(case.app, base_url=ORIGIN)
        login = client.post("/api/auth/login", json={"email": case.users[index].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        assert login.status_code == 200
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]})
        return client
    entered = Event(); release = Event()
    async def hold(request):
        if "/data_sources/" in str(request.url): return server.response(schema())
        entered.set()
        if not await anyio.to_thread.run_sync(release.wait, 15): raise TimeoutError("Synthetic Notion response was not released")
        document = page(); document["id"] = identifier
        return server.response(document)
    server.hook = hold; path = f"/api/companies/{company_id}"
    with client_for() as actor, client_for(3) as admin:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, path + "/notion-preview", json={"expected_page_id": identifier})
            try:
                assert entered.wait(15)
                with case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%'")) == 0
                busy = admin.post(path + "/notion-preview", json={"expected_page_id": identifier})
                assert busy.status_code == 503 and busy.json()["detail"]["code"] == "NOTION_BUSY"
                if change == "company": response = admin.patch(path, json={"name": "New concurrent local name"})
                elif change == "link": response = admin.patch(path, json={"notion_page_id": str(uuid4())})
                elif change == "role": response = admin.patch(f"/api/internal-users/{case.users[0].id}", json={"role": "PROCESSOR"})
                elif change == "revoke": response = admin.patch(f"/api/internal-users/{case.users[0].id}", json={"is_active": False})
                if change is not None: assert response.status_code == 200
            finally: release.set()
            result = pending.result(timeout=20)
            # Account edits atomically revoke sessions, including a role change.
            assert result.status_code == (200 if change is None else 401 if change in {"role", "revoke"} else 409)
            if change is not None: assert "Synthetic česká company" not in result.text
        assert len(server.requests) == 2
        stored = admin.get(path).json()
        assert stored["name"] != "Synthetic česká company" and stored["website"] is None


def _pg_company_event(case, *, field="name", value="Audited synthetic name"):
    from app.company_history import append_company_change, company_snapshot
    with case.database.session() as session:
        identifier = session.get(Project, case.material.project_id).company_id
        company = session.scalar(select(Company).where(Company.id == identifier).with_for_update())
        before = company_snapshot(company); setattr(company, field, value)
        event = append_company_change(session, company, case.users[0].id, before)
        session.commit(); return event


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
def test_postgresql_company_history_cannot_be_changed_or_removed(review_pg_case, operation):
    case = review_pg_case; event = _pg_company_event(case)
    statements = {"update": "UPDATE company_change_events SET reason='Rewrite' WHERE id=:id",
        "delete": "DELETE FROM company_change_events WHERE id=:id", "truncate": "TRUNCATE company_change_events"}
    with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
        connection.execute(text(statements[operation]), {"id": event.id})
    with case.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM company_change_events WHERE id=:id"), {"id": event.id}) == 1


@pytest.mark.parametrize("change", ["skip-version", "wrong-after", "extra-before", "wrong-company", "false-creation", "raw-source", "unbound-key", "bad-hash"])
def test_postgresql_company_history_rejects_invalid_evidence(review_pg_case, change):
    from app.db.models import CompanyChangeEvent
    from app.material_review import canonical_hash
    case = review_pg_case; previous = _pg_company_event(case)
    values = {column.key: getattr(previous, column.key) for column in CompanyChangeEvent.__table__.columns if column.key not in {"id", "created_at"}}
    values.update(version=2, before_snapshot=previous.before_snapshot.copy(), after_snapshot=previous.after_snapshot.copy())
    if change == "skip-version": values["version"] = 3
    elif change == "wrong-after": values["after_snapshot"]["name"] = "Not the stored company"
    elif change == "extra-before": values["before_snapshot"]["raw"] = "Unmapped data"
    elif change == "wrong-company": values["before_snapshot"]["id"] = str(uuid4())
    elif change == "false-creation": values["action"] = "CREATED"; values["before_snapshot"] = {}
    elif change == "raw-source": values["source"] = {"raw": "Unmapped data"}
    elif change == "unbound-key": values["request_key"] = uuid4()
    values["before_hash"] = canonical_hash(values["before_snapshot"])
    values["after_hash"] = "INVALID" if change == "bad-hash" else canonical_hash(values["after_snapshot"])
    with pytest.raises(DBAPIError), case.database.session() as session:
        session.add(CompanyChangeEvent(**values)); session.commit()


def test_postgresql_concurrent_company_changes_keep_order_and_exact_before_values(review_pg_case):
    from app.db.models import CompanyChangeEvent
    case = review_pg_case; barrier = Barrier(2)
    def changing(field, value):
        barrier.wait(10); return _pg_company_event(case, field=field, value=value)
    with ThreadPoolExecutor(2) as pool:
        calls = [pool.submit(changing, "legal_name", "Legal synthetic company"), pool.submit(changing, "country", "CZ")]
        changes = sorted([call.result(timeout=15) for call in calls], key=lambda item: item.version)
    assert [change.version for change in changes] == [1, 2]
    assert changes[0].after_snapshot == changes[1].before_snapshot
    with case.database.session() as session:
        company = session.get(Company, changes[0].company_id)
        assert company.country == "CZ" and company.legal_name == "Legal synthetic company"
        assert len(list(session.scalars(select(CompanyChangeEvent).where(CompanyChangeEvent.company_id == company.id)))) == 2


def test_postgresql_company_0020_preserves_0019_data_and_refuses_history_loss():
    from app.company_history import append_company_change, company_snapshot
    from app.db.models import CompanyChangeEvent
    with isolated_postgresql_database() as database_url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", database_url); get_settings.cache_clear()
        config = Config("alembic.ini"); command.upgrade(config, "20260918_0019")
        database = Database(database_url)
        try:
            with database.session() as session:
                actor = InternalUser(display_name="Synthetic history actor", email=f"{uuid4().hex}@example.invalid", role="ADMIN")
                company = Company(name="Existing company", notion_page_id=str(uuid4()))
                session.add_all([actor, company]); session.commit(); frozen = company_snapshot(company)
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            with database.session() as session:
                assert company_snapshot(session.get(Company, company.id)) == frozen
                assert not list(session.scalars(select(CompanyChangeEvent)))
            command.downgrade(config, "20260918_0019")
            assert not inspect(database.engine).has_table("company_change_events")
            command.upgrade(config, "head")
            with database.session() as session:
                stored = session.scalar(select(Company).where(Company.id == company.id).with_for_update())
                before = company_snapshot(stored); stored.country = "CZ"
                event = append_company_change(session, stored, actor.id, before); session.commit()
                assert event.version == 1 and event.action == "UPDATED"
            with pytest.raises(DBAPIError, match="Company history exists"):
                command.downgrade(config, "20260918_0019")
            with database.engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260918_0020"
                assert connection.scalar(text("SELECT count(*) FROM company_change_events")) == 1
        finally: database.dispose(); get_settings.cache_clear()


@pytest.mark.parametrize("change", [None, "extra", "active", "unchanged", "page", "hash", "timestamp", "missing"])
def test_postgresql_company_adoption_evidence_is_bound_to_selected_fields_and_page(review_pg_case, change):
    from app.company_history import append_company_change, company_snapshot
    case = review_pg_case; linked = str(uuid4())
    with case.database.session() as session:
        company = session.get(Project, case.material.project_id).company
        company.notion_page_id = linked; session.commit(); identifier = company.id
    source = dict(page_id=linked, data_source_id=str(uuid4()), database_id=str(uuid4()),
        last_edited_time="2026-09-18T12:00:00Z", mapping_sha256="a" * 64, observation_sha256="b" * 64,
        selected_fields=["country"])
    if change == "extra": source["raw"] = "Unmapped property"
    if change == "active": source["selected_fields"] = ["is_active"]
    if change == "unchanged": source["selected_fields"] = ["name"]
    if change == "page": source["page_id"] = str(uuid4())
    if change == "hash": source["mapping_sha256"] = "not-a-hash"
    if change == "timestamp": source["last_edited_time"] = "2026-99-18T12:00:00Z"
    if change == "missing": source.pop("database_id")
    def record():
        with case.database.session() as session:
            company = session.scalar(select(Company).where(Company.id == identifier).with_for_update())
            before = company_snapshot(company); company.country = "CZ"
            result = append_company_change(session, company, case.users[0].id, before, action="NOTION_ADOPTED",
                reason="Adopt reviewed country", source=source, request_key=uuid4(), request_hash="c" * 64)
            session.commit(); return result
    if change is None: assert record().source == source
    else:
        with pytest.raises(DBAPIError): record()
        with case.database.session() as session: assert session.get(Company, identifier).country is None


def test_postgresql_company_api_concurrent_edits_are_audited_in_commit_order(review_pg_case):
    case = review_pg_case; barrier = Barrier(2)
    with case.database.session() as session: identifier = session.get(Project, case.material.project_id).company_id
    path = f"/api/companies/{identifier}"
    with case.client_for() as first, case.client_for(3) as second:
        def update(client, values): barrier.wait(10); return client.patch(path, json=values)
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(update, first, {"country": "CZ"}), pool.submit(update, second, {"legal_name": "Concurrent legal name"})]
            assert [future.result(timeout=15).status_code for future in futures] == [200, 200]
        history = first.get(path + "/history").json()["items"]
        assert [item["version"] for item in history] == [2, 1]
        assert history[0]["before"] == history[1]["after"]
        assert history[0]["after"]["country"] == "CZ" and history[0]["after"]["legal_name"] == "Concurrent legal name"
        assert {item["actor_id"] for item in history} == {str(case.users[0].id), str(case.users[3].id)}


def test_postgresql_company_create_collision_has_one_record_and_one_audit_event(review_pg_case):
    from app.db.models import CompanyChangeEvent
    case = review_pg_case; barrier = Barrier(2); linked = str(uuid4())
    with case.client_for() as first, case.client_for(3) as second:
        def create(client):
            barrier.wait(10); return client.post("/api/companies", json={"name": "Concurrent company", "notion_page_id": linked})
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(create, first), pool.submit(create, second)]
            responses = [future.result(timeout=15) for future in futures]
        assert sorted(response.status_code for response in responses) == [201, 409]
    with case.database.session() as session:
        company = session.scalar(select(Company).where(Company.notion_page_id == linked))
        events = list(session.scalars(select(CompanyChangeEvent).where(CompanyChangeEvent.company_id == company.id)))
        assert len(events) == 1 and events[0].version == 1 and events[0].action == "CREATED"


def _notion_adoption_pg(case):
    from app.main import create_app as authenticated_app
    from app.notion_reader import configuration_from_settings
    from test_application_access import PASSWORD, ORIGIN
    from test_notion_preview import enabled_settings
    from test_notion_reader import Server, schema, page
    settings = enabled_settings(case.app.state.settings); page_id = str(uuid4())
    with case.database.session() as session:
        company = session.get(Project, case.material.project_id).company
        company.notion_page_id = page_id; company.country = "CZ"; session.commit(); identifier = company.id
    document = page(); document["id"] = page_id
    def server_app():
        server = Server()
        async def respond(request): return server.response(schema() if "/data_sources/" in str(request.url) else document)
        server.hook = respond
        reader = server.client(configuration_from_settings(settings))
        return server, authenticated_app(settings, case.database, notion_reader=reader)
    def client_for(app, index=0):
        client = TestClient(app, base_url=ORIGIN)
        login = client.post("/api/auth/login", json={"email": case.users[index].email, "password": PASSWORD}, headers={"Origin": ORIGIN})
        assert login.status_code == 200
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]})
        return client
    path = f"/api/companies/{identifier}"
    def command(client):
        response = client.post(path + "/notion-preview", json={"expected_page_id": page_id})
        assert response.status_code == 200
        value = response.json()
        return {"request_key": str(uuid4()), "expected_page_id": page_id, "expected_local_sha256": value["local_sha256"],
            "expected_observation_sha256": value["source"]["observation_sha256"],
            "selected_fields": ["country", "name"], "reason": "Reviewed synthetic concurrent values"}
    return SimpleNamespace(settings=settings, identifier=identifier, path=path, document=document,
        server_app=server_app, client_for=client_for, command=command)


@pytest.mark.parametrize("change", [None, "company", "link", "role", "revoke", "read-failure"])
def test_postgresql_notion_adoption_rechecks_concurrent_changes_without_io_transaction(review_pg_case, change):
    import anyio
    from app.db.models import CompanyChangeEvent
    from test_notion_reader import schema
    case = review_pg_case; item = _notion_adoption_pg(case); server, app = item.server_app()
    entered = Event(); release = Event()
    async def hold(request):
        if "/data_sources/" in str(request.url): return server.response(schema())
        entered.set()
        if not await anyio.to_thread.run_sync(release.wait, 15): raise TimeoutError("Synthetic adoption read was not released")
        return server.response(item.document, status=503 if change == "read-failure" else 200)
    with item.client_for(app) as actor, item.client_for(app, 3) as admin:
        body = item.command(actor); server.hook = hold
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, item.path + "/notion-adopt", json=body)
            try:
                assert entered.wait(15)
                with case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%'")) == 0
                if change == "company": response = admin.patch(item.path, json={"legal_name": "New concurrent legal name"})
                elif change == "link": response = admin.patch(item.path, json={"notion_page_id": str(uuid4())})
                elif change == "role": response = admin.patch(f"/api/internal-users/{case.users[0].id}", json={"role": "PROCESSOR"})
                elif change == "revoke": response = admin.patch(f"/api/internal-users/{case.users[0].id}", json={"is_active": False})
                if change not in {None, "read-failure"}: assert response.status_code == 200
            finally: release.set()
            result = pending.result(timeout=20)
            assert result.status_code == (200 if change is None else 503 if change == "read-failure" else 401 if change in {"role", "revoke"} else 409)
        stored = admin.get(item.path).json()
        assert stored["country"] == (None if change is None else "CZ")
    with case.database.session() as session:
        records = list(session.scalars(select(CompanyChangeEvent).where(CompanyChangeEvent.company_id == item.identifier, CompanyChangeEvent.action == "NOTION_ADOPTED")))
        assert len(records) == (1 if change is None else 0)


@pytest.mark.parametrize("different_command", [False, True])
def test_postgresql_notion_adoption_two_backends_serialize_replays_and_stale_comparisons(review_pg_case, different_command):
    import anyio
    from app.db.models import CompanyChangeEvent
    from test_notion_reader import schema
    case = review_pg_case; item = _notion_adoption_pg(case)
    first_server, first_app = item.server_app(); second_server, second_app = item.server_app()
    ready = Barrier(2)
    def hook(server):
        async def hold(request):
            if "/data_sources/" in str(request.url): return server.response(schema())
            await anyio.to_thread.run_sync(ready.wait, 15)
            return server.response(item.document)
        return hold
    with item.client_for(first_app) as first, item.client_for(second_app, 3 if different_command else 0) as second:
        body = item.command(first); other = dict(body)
        if different_command: other["request_key"] = str(uuid4())
        first_server.hook = hook(first_server); second_server.hook = hook(second_server)
        with ThreadPoolExecutor(2) as pool:
            pending = [pool.submit(first.post, item.path + "/notion-adopt", json=body),
                pool.submit(second.post, item.path + "/notion-adopt", json=other)]
            responses = [future.result(timeout=25) for future in pending]
        assert sorted(response.status_code for response in responses) == ([200, 409] if different_command else [200, 200])
        if not different_command: assert responses[0].json() == responses[1].json()
        else: assert next(response for response in responses if response.status_code == 409).json()["detail"]["code"] == "NOTION_LOCAL_CHANGED"
    with case.database.session() as session:
        records = list(session.scalars(select(CompanyChangeEvent).where(CompanyChangeEvent.company_id == item.identifier)))
        assert len(records) == 1 and records[0].action == "NOTION_ADOPTED"


@pytest.mark.parametrize("failed_read", [False, True])
def test_postgresql_notion_adoption_late_response_returns_committed_replay_before_any_stale_error(review_pg_case, failed_read):
    import anyio
    from app.main import create_app as authenticated_app
    from test_notion_reader import schema
    case = review_pg_case; item = _notion_adoption_pg(case)
    server, app = item.server_app(); _, replacement = item.server_app(); entered = Event(); release = Event()
    async def hold(request):
        if "/data_sources/" in str(request.url): return server.response(schema())
        entered.set()
        if not await anyio.to_thread.run_sync(release.wait, 20): raise TimeoutError("Late synthetic read was not released")
        return server.response(item.document, status=503 if failed_read else 200)
    with item.client_for(app) as original, item.client_for(replacement) as retrying, item.client_for(replacement, 3) as admin:
        body = item.command(original); server.hook = hold
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(original.post, item.path + "/notion-adopt", json=body)
            try:
                assert entered.wait(15)
                committed = retrying.post(item.path + "/notion-adopt", json=body)
                assert committed.status_code == 200
                assert admin.patch(item.path, json={"name": "Later independent local edit", "notion_page_id": None}).status_code == 200
            finally: release.set()
            late = pending.result(timeout=25)
            assert late.status_code == 200 and late.json() == committed.json()
        disabled = authenticated_app(item.settings.model_copy(update={"notion_enabled": False}), case.database)
        with item.client_for(disabled) as after_restart:
            replay = after_restart.post(item.path + "/notion-adopt", json=body)
            assert replay.status_code == 200 and replay.json() == committed.json()
            history = after_restart.get(item.path + "/history").json()["items"]
            assert [entry["action"] for entry in history] == ["UPDATED", "NOTION_ADOPTED"]
