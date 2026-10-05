"""Real PostgreSQL authorization durability, idempotency and retained audit contracts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.material_deletion_models import MaterialDeletionOperation, MaterialDeletionOwner
from app.db.models import InternalUser, MaterialAuditEvent, PBRMaterial
from app.local_filesystem import LocalFilesError
from test_materials_postgresql import (
    POSTGRES_TEST_ADMIN_URL, _review_pg_case, migrated_postgresql_url,  # noqa: F401
)

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")
PATH = "/api/material-deletions"


class DeletionFiles:
    """Synthetic source adapter; reads independent DB connection before any pretend I/O."""
    def __init__(self):
        self.database = None
        self.calls = 0
        self.interrupt = False
        self.callback = None

    def snapshot(self, _folder):
        return {"directory_identity": [1, 2, 3, True], "entries": [
            {"path": "PREVIEW/SPHERE_1.png", "kind": "file", "size": 20}]}

    def execute(self, identifier, frozen):
        self.calls += 1
        with self.database.session() as session:
            operation = session.get(MaterialDeletionOperation, identifier)
            owner = session.get(MaterialDeletionOwner, UUID(frozen["material_id"]))
            assert operation is not None and frozen in operation.plan["items"]
            assert owner is not None and owner.operation_id == identifier
        if self.callback:
            self.callback()
        if self.interrupt:
            raise LocalFilesError("SYNTHETIC_DELETION_INTERRUPTION")
        return {"status": "COMPLETED", "error_code": None}


@pytest.fixture
def deletion_pg_case(migrated_postgresql_url, monkeypatch):
    import app.api.material_deletions as api
    import app.main as main
    adapter = DeletionFiles()
    factory = api.build_material_deletions_router
    monkeypatch.setattr(api, "LocalMaterialDeletion", lambda _library: adapter)
    monkeypatch.setattr(main, "build_material_deletions_router",
                        lambda database, settings, _library=None: factory(database, settings, object()))
    with contextmanager(_review_pg_case)(migrated_postgresql_url) as case:
        adapter.database = case.database
        case.deletion_files = adapter
        yield case


def apply_body(client, case, mode="RECORD_AND_FILES"):
    material = client.get(case.path).json()
    body = {"materials": [{"id": str(case.material.id), "expected_updated_at": material["updated_at"]}], "mode": mode}
    plan = client.post(PATH + "/plan", json=body)
    assert plan.status_code == 200 and plan.json()["can_apply"], plan.text
    return {**body, "idempotency_key": str(uuid4()), "confirmed": True,
            "expected_proposal_hash": plan.json()["proposal_hash"]}


def test_deletion_authorization_commits_before_io_and_recovery_is_idempotent(deletion_pg_case):
    case = deletion_pg_case
    case.deletion_files.interrupt = True
    with case.client_for() as client, case.client_for(1) as processor:
        body = apply_body(client, case)
        result = client.post(PATH, json=body)
        assert result.status_code == 200 and result.json()["status"] == "RECOVERY_REQUIRED", result.text
        identifier = result.json()["id"]
        assert processor.get(PATH + "/" + identifier).status_code == 403
        assert processor.post(PATH + "/" + identifier + "/resume", json={"confirmed": True}).status_code == 403
        assert client.patch(case.path, json={"assigned_processor_id": str(case.users[2].id)}).status_code == 409
        assert client.get(PATH, params={"material_ids": str(case.material.id)}).json()["items"][0]["id"] == identifier
        case.deletion_files.interrupt = False
        resumed = client.post(PATH + "/" + identifier + "/resume", json={"confirmed": True})
        assert resumed.status_code == 200 and resumed.json()["status"] == "COMPLETED", resumed.text
        assert client.post(PATH, json=body).json() == resumed.json()
        assert client.get(case.path).status_code == 404
        assert client.get(PATH, params={"material_ids": str(case.material.id)}).json() == {"items": []}
        assert case.deletion_files.calls == 2
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.material.id) is None
        assert session.get(MaterialDeletionOwner, case.material.id) is None
        assert session.scalar(select(func.count()).select_from(MaterialAuditEvent).where(
            MaterialAuditEvent.material_id == case.material.id, MaterialAuditEvent.event_type == "MATERIAL_DELETED")) == 1


def test_concurrent_exact_delete_retries_create_one_receipt_and_move_once(deletion_pg_case):
    case = deletion_pg_case
    with case.client_for() as first, case.client_for() as second:
        body = apply_body(first, case)
        barrier = Barrier(2)
        def apply(client):
            barrier.wait(timeout=15)
            return client.post(PATH, json=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(apply, client) for client in (first, second)]
            responses = [future.result(timeout=30) for future in futures]
        assert all(item.status_code == 200 for item in responses), [item.text for item in responses]
        assert responses[0].json() == responses[1].json()
        assert responses[0].json()["status"] == "COMPLETED"
        assert case.deletion_files.calls == 1
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialDeletionOperation).where(
            MaterialDeletionOperation.actor_id == case.users[0].id,
            MaterialDeletionOperation.request_key == UUID(body["idempotency_key"]))) == 1


def test_revoked_admin_after_io_keeps_durable_receipt_for_another_admin(deletion_pg_case):
    case = deletion_pg_case
    def revoke():
        with case.database.session() as session:
            session.get(InternalUser, case.users[0].id).role = "PROCESSOR"
            session.commit()
    with case.client_for() as client, case.client_for(3) as replacement:
        body = apply_body(client, case)
        case.deletion_files.callback = revoke
        response = client.post(PATH, json=body)
        assert response.status_code == 403, response.text
        pending = replacement.get(PATH, params={"material_ids": str(case.material.id)}).json()["items"]
        assert len(pending) == 1 and pending[0]["status"] == "RUNNING"
        assert replacement.get(case.path).status_code == 200
        case.deletion_files.callback = None
        resumed = replacement.post(PATH + "/" + pending[0]["id"] + "/resume", json={"confirmed": True})
        assert resumed.status_code == 200 and resumed.json()["status"] == "COMPLETED", resumed.text
    with case.database.session() as session:
        event = session.scalar(select(MaterialAuditEvent).where(
            MaterialAuditEvent.material_id == case.material.id, MaterialAuditEvent.event_type == "MATERIAL_DELETED"))
        assert event.actor_id == case.users[3].id
        assert event.result["audit"]["authorized_actor_id"] == str(case.users[0].id)


def test_database_guards_preserve_tombstone_and_completed_deletion_receipt(deletion_pg_case):
    case = deletion_pg_case
    with case.client_for() as client:
        body = apply_body(client, case, mode="RECORD_ONLY")
        result = client.post(PATH, json=body)
        assert result.status_code == 200 and result.json()["status"] == "COMPLETED", result.text
        operation_id = UUID(result.json()["id"])
    for statement, identifier in (
        ("UPDATE pbr_materials SET deleted_at=NULL WHERE id=:id", case.material.id),
        ("UPDATE pbr_materials SET material_name='REWRITTEN' WHERE id=:id", case.material.id),
        ("DELETE FROM pbr_materials WHERE id=:id", case.material.id),
        ("UPDATE material_deletion_operations SET status='RUNNING' WHERE id=:id", operation_id),
        ("UPDATE material_deletion_operations SET request_hash=repeat('c',64) WHERE id=:id", operation_id),
        ("DELETE FROM material_deletion_operations WHERE id=:id", operation_id),
        ("TRUNCATE material_deletion_operations CASCADE", operation_id),
    ):
        with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
            connection.execute(text(statement), {"id": identifier})
    with case.database.session() as session:
        retained = session.scalar(select(PBRMaterial).where(PBRMaterial.id == case.material.id)
                                  .execution_options(include_deleted_materials=True))
        assert retained.deleted_at is not None and retained.folder_path == case.material.folder_path
        assert session.get(PBRMaterial, case.material.id) is retained  # Identity map guard is also required.
    # A new session never leaks the tombstone through an identity-map shortcut.
    with case.database.session() as session:
        assert session.get(PBRMaterial, case.material.id) is None


def test_pending_ownership_requires_real_unique_material_and_operation(deletion_pg_case):
    case = deletion_pg_case
    case.deletion_files.interrupt = True
    with case.client_for() as client:
        response = client.post(PATH, json=apply_body(client, case))
        assert response.status_code == 200, response.text
        identifier = UUID(response.json()["id"])
    owner = {"material_id": case.material.id, "operation_id": identifier,
             "brand_id": case.material.published_brand_id, "folder_path": case.material.folder_path}
    with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
        connection.execute(MaterialDeletionOwner.__table__.insert().values(**owner))
    for field in ("material_id", "operation_id", "brand_id"):
        with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
            connection.execute(MaterialDeletionOwner.__table__.update().where(
                MaterialDeletionOwner.material_id == case.material.id).values(**{field: uuid4()}))
