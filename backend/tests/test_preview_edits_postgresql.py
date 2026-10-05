"""Real PostgreSQL durability and ownership contracts for PREVIEW edits."""
from contextlib import contextmanager
from copy import deepcopy
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import get_settings
from app.db.models import MaterialAuditEvent
from app.db.preview_edit_models import PreviewEditOperation, PreviewEditOwner
from app.local_filesystem import LocalFilesError
from app.material_identity import require_brand_idle, require_folder_idle, require_material_idle
from test_materials_postgresql import (
    POSTGRES_TEST_ADMIN_URL, _review_pg_case, isolated_postgresql_database,
    migrated_postgresql_url,  # noqa: F401 -- shared isolated PostgreSQL fixture
)

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")
PATH = "/api/material-preview-edits"


class PreviewFiles:
    """No local filesystem writes: assert authorization is committed before I/O."""
    def __init__(self):
        self.database = None
        self.calls = 0
        self.interrupt = True

    def snapshot(self, _folder):
        return {"names": ["SPHERE_1.png", "DETAIL_2.png"], "directory_identity": {"volume": 1, "file": 2},
                "files": [{"name": name, "size": 12, "sha256": "a" * 64, "identity": {"volume": 1, "file": index + 3}}
                          for index, name in enumerate(["SPHERE_1.png", "DETAIL_2.png"])]}

    def execute(self, identifier, frozen):
        self.calls += 1
        with self.database.session() as session:
            operation = session.get(PreviewEditOperation, identifier)
            owner = session.get(PreviewEditOwner, UUID(frozen["material_id"]))
            assert operation is not None and operation.plan["items"][0] == frozen
            assert owner is not None and owner.operation_id == identifier
        if self.interrupt:
            raise LocalFilesError("SYNTHETIC_PREVIEW_INTERRUPTION")
        return {"status": "COMPLETED", "error_code": None, "renamed": 1, "deleted": 0}


@pytest.fixture
def preview_pg_case(migrated_postgresql_url, monkeypatch):
    import app.api.preview_edits as preview_api
    import app.main as main
    adapter = PreviewFiles()
    factory = preview_api.build_preview_edits_router
    monkeypatch.setattr(preview_api, "LocalPreviewEdits", lambda _library: adapter)
    monkeypatch.setattr(main, "build_preview_edits_router", lambda database, settings, _library=None: factory(database, settings, object()))
    with contextmanager(_review_pg_case)(migrated_postgresql_url) as case:
        adapter.database = case.database
        case.preview_files = adapter
        yield case


def apply_body(client, case):
    material = client.get(case.path).json()
    body = {"materials": [{"id": str(case.material.id), "expected_updated_at": material["updated_at"]}],
            "action": "RENAME", "filename": "DETAIL_2.png", "new_name": "DETAIL_3.png"}
    result = client.post(PATH + "/plan", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["can_apply"]
    return {**body, "expected_proposal_hash": result.json()["proposal_hash"],
            "idempotency_key": str(uuid4()), "confirmed": True}


def test_preview_authorization_is_durable_and_recovery_releases_only_its_owner(preview_pg_case):
    case = preview_pg_case
    with case.client_for() as client, case.client_for(1) as other:
        body = apply_body(client, case)
        result = client.post(PATH, json=body)
        assert result.status_code == 200, result.text
        identifier = UUID(result.json()["id"])
        assert result.json()["status"] == "RECOVERY_REQUIRED"
        with case.database.session() as session:
            owner = session.get(PreviewEditOwner, case.material.id)
            assert owner.operation_id == identifier
            for check in (lambda: require_material_idle(session, case.material.id),
                          lambda: require_brand_idle(session, case.material.published_brand_id),
                          lambda: require_folder_idle(session, case.material.folder_path.upper()),
                          lambda: require_folder_idle(session, case.material.folder_path + "/PREVIEW"),
                          lambda: require_folder_idle(session, "library")):
                with pytest.raises(HTTPException) as error:
                    check()
                assert error.value.status_code == 409
            require_material_idle(session, case.material.id, preview_operation_id=identifier)
            require_folder_idle(session, case.material.folder_path, preview_operation_id=identifier)
        assert other.get(f"{PATH}/{identifier}").status_code == 404
        assert other.post(f"{PATH}/{identifier}/resume", json={"confirmed": True}).status_code == 404
        assert client.post(PATH, json={**body, "new_name": "OTHER_3.png"}).status_code == 409
        assert case.preview_files.calls == 1
        active = client.get(PATH, params={"material_id": str(case.material.id)}).json()["items"]
        assert [item["id"] for item in active] == [str(identifier)]
        case.preview_files.interrupt = False
        resumed = client.post(f"{PATH}/{identifier}/resume", json={"confirmed": True})
        assert resumed.status_code == 200 and resumed.json()["status"] == "COMPLETED", resumed.text
        replay = client.post(PATH, json=body)
        assert replay.status_code == 200 and replay.json() == resumed.json()
        assert case.preview_files.calls == 2
        assert client.get(PATH, params={"material_id": str(case.material.id)}).json()["items"] == []
        with case.database.session() as session:
            assert session.get(PreviewEditOwner, case.material.id) is None
            events = session.scalars(select(MaterialAuditEvent).where(MaterialAuditEvent.material_id == case.material.id,
                                    MaterialAuditEvent.event_type == "PREVIEW_FILES_EDITED")).all()
            assert len(events) == 1
            assert events[0].actor_id == case.users[0].id
            require_material_idle(session, case.material.id)


def receipt(case, **overrides):
    return {"id": uuid4(), "actor_id": case.users[0].id, "request_key": uuid4(), "request_hash": "a" * 64,
            "proposal_hash": "b" * 64, "request_payload": {}, "plan": {"items": []}, "items": [], "status": "RUNNING", **overrides}


def test_preview_database_constraints_retain_authorization_and_terminal_receipts(preview_pg_case):
    case = preview_pg_case
    values = receipt(case)
    with case.database.engine.begin() as connection:
        connection.execute(PreviewEditOperation.__table__.insert().values(**values))
    for statement in ("UPDATE preview_edit_operations SET request_hash=repeat('c',64) WHERE id=:id",
                      "UPDATE preview_edit_operations SET plan='{}'::jsonb WHERE id=:id",
                      "DELETE FROM preview_edit_operations WHERE id=:id",
                      "TRUNCATE preview_edit_operations CASCADE"):
        with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
            connection.execute(text(statement), {"id": values["id"]})
    with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
        connection.execute(PreviewEditOperation.__table__.insert().values(**{**values, "id": uuid4()}))
    with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
        connection.execute(PreviewEditOperation.__table__.insert().values(**receipt(case, status="UNKNOWN")))
    with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
        connection.execute(PreviewEditOperation.__table__.insert().values(**receipt(case, actor_id=uuid4())))
    with case.database.engine.begin() as connection:
        connection.execute(text("UPDATE preview_edit_operations SET status='RECOVERY_REQUIRED', items='[{\"status\":\"RECOVERY_REQUIRED\"}]'::jsonb WHERE id=:id"), {"id": values["id"]})
        connection.execute(text("UPDATE preview_edit_operations SET status='COMPLETED' WHERE id=:id"), {"id": values["id"]})
    for statement in ("UPDATE preview_edit_operations SET status='RUNNING' WHERE id=:id",
                      "UPDATE preview_edit_operations SET items='[]'::jsonb WHERE id=:id"):
        with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
            connection.execute(text(statement), {"id": values["id"]})
    with case.database.session() as session:
        operation = session.get(PreviewEditOperation, values["id"])
        assert operation.status == "COMPLETED" and operation.request_hash == values["request_hash"]
        operation.items = [{"rewritten": True}]
        with pytest.raises(ValueError, match="immutable"):
            session.flush()


def test_preview_owner_has_one_material_claim_and_required_existing_references(preview_pg_case):
    case = preview_pg_case
    first, second = receipt(case), receipt(case)
    claim = {"material_id": case.material.id, "operation_id": first["id"],
             "brand_id": case.material.published_brand_id, "folder_path": case.material.folder_path}
    with case.database.engine.begin() as connection:
        connection.execute(PreviewEditOperation.__table__.insert(), [first, second])
        connection.execute(PreviewEditOwner.__table__.insert().values(**claim))
    with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
        connection.execute(PreviewEditOwner.__table__.insert().values(**{**claim, "operation_id": second["id"]}))
    for field in ("material_id", "operation_id", "brand_id"):
        with pytest.raises(IntegrityError), case.database.engine.begin() as connection:
            connection.execute(PreviewEditOwner.__table__.update().where(PreviewEditOwner.material_id == case.material.id).values(**{field: uuid4()}))
    with case.database.session() as session:
        assert session.get(PreviewEditOwner, case.material.id).operation_id == first["id"]


def test_0042_upgrade_preserves_0041_materials_and_blocks_history_loss():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20261001_0041")
            with contextmanager(_review_pg_case)(url) as case:
                with case.database.engine.connect() as connection:
                    before = deepcopy(connection.execute(text("SELECT to_jsonb(m) FROM pbr_materials m ORDER BY id")).scalars().all())
                command.upgrade(config, "head"); command.check(config)
                with case.database.engine.connect() as connection:
                    assert connection.execute(text("SELECT to_jsonb(m)-'is_draft'-'deleted_at' FROM pbr_materials m ORDER BY id")).scalars().all() == before
                    assert connection.scalar(text("SELECT count(*) FROM preview_edit_operations")) == 0
                command.downgrade(config, "20261001_0041")
                assert not inspect(case.database.engine).has_table("preview_edit_operations")
                command.upgrade(config, "head")
                with case.database.engine.begin() as connection:
                    connection.execute(PreviewEditOperation.__table__.insert().values(**receipt(case)))
                with pytest.raises(RuntimeError, match="history or recovery ownership"):
                    command.downgrade(config, "20261001_0041")
                with case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261005_0044"
                    assert connection.scalar(text("SELECT count(*) FROM preview_edit_operations")) == 1
        finally:
            get_settings.cache_clear()
