"""Real PostgreSQL migration, exact-snapshot triggers and competing cell writes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.models import PBRMaterial, OnlineCategory, ResourceChangeEvent
from test_materials_postgresql import (_review_pg_case, isolated_postgresql_database,
    migrated_postgresql_url, review_pg_case, POSTGRES_TEST_ADMIN_URL)  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def test_migration_preserves_legacy_rows_and_seeds_exact_vocabulary():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260925_0026")
            with contextmanager(_review_pg_case)(url) as case:
                with case.database.engine.begin() as connection:
                    original = dict(connection.execute(text("SELECT * FROM pbr_materials WHERE id=:id"), {"id": case.material.id}).mappings().one())
                command.upgrade(config, "head"); command.check(config)
                with case.database.session() as session:
                    material = session.get(PBRMaterial, case.material.id)
                    assert material.checked_status == "no" and material.note is None
                    assert len(list(session.scalars(select(OnlineCategory)))) == 75
                    assert all(getattr(material, key) == value for key, value in original.items())
                command.downgrade(config, "20260925_0026")
                command.upgrade(config, "head"); command.check(config)
                with case.client_for() as client:
                    before = client.get(case.path).json()
                    key = str(uuid4()); payload = {"expected_updated_at": before["updated_at"], "note": "Real migration check\nSecond line"}
                    saved = client.patch(case.path + "/table", json=payload, headers={"Idempotency-Key": key})
                    assert saved.status_code == 200, saved.text
                    assert client.patch(case.path + "/table", json=payload, headers={"Idempotency-Key": key}).json() == saved.json()
                    assert client.get(case.path + "/history").json()["items"][0]["after"]["note"] == payload["note"]
                    assert client.get("/api/resource-commands/" + key).json()["response"] == saved.json()
                with pytest.raises(RuntimeError, match="Export human checks"):
                    command.downgrade(config, "20260925_0026")
        finally:
            get_settings.cache_clear()


def test_two_actors_cannot_overwrite_the_same_table_revision(review_pg_case):
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as client:
        before = client.get(case.path).json()
    def update(actor):
        with case.client_for(actor) as client:
            barrier.wait(timeout=10)
            result = client.patch(case.path + "/table", json={"expected_updated_at": before["updated_at"], "note": f"Actor {actor}"}, headers={"Idempotency-Key": str(uuid4())})
            return result.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [0, 3]))
    assert sorted(results) == [200, 409]
    with case.database.session() as session:
        events = list(session.scalars(select(ResourceChangeEvent).where(ResourceChangeEvent.material_id == case.material.id)))
        assert len(events) == 1 and events[0].after_snapshot["note"] in {"Actor 0", "Actor 3"}


def test_upgrade_keeps_old_material_receipts_replayable_and_history_readable():
    from app.db.models import ResourceCommand
    from app.material_review import canonical_hash
    from app.schemas import PBRMaterialRead
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260925_0026")
            with contextmanager(_review_pg_case)(url) as case:
                key = uuid4()
                with case.database.session() as session:
                    raw = dict(session.execute(text("SELECT * FROM pbr_materials WHERE id=:id"), {"id": case.material.id}).mappings().one())
                    response = PBRMaterialRead.model_validate(raw).model_dump(mode="json", exclude_unset=True)
                    assert "checked_status" not in response and "note" not in response
                    snapshot = {name: value for name, value in response.items() if name not in {"created_at", "updated_at"}}
                    payload = {"material_name": response["material_name"]}
                    session.add(ResourceChangeEvent(kind="MATERIAL", material_id=case.material.id, actor_id=case.users[0].id,
                        version=1, action="CREATED", before_snapshot={}, after_snapshot=snapshot,
                        before_hash=canonical_hash({}), after_hash=canonical_hash(snapshot)))
                    session.add(ResourceCommand(kind="MATERIAL", material_id=case.material.id, actor_id=case.users[0].id,
                        request_key=key, action="UPDATED", privilege="MATERIAL_NAME",
                        request_hash=canonical_hash({"schema_version": 1, "kind": "MATERIAL", "action": "UPDATED", "target_id": str(case.material.id), "payload": payload}),
                        response_snapshot=response, response_hash=canonical_hash(response)))
                    session.commit()
                command.upgrade(config, "head"); command.check(config)
                with case.client_for() as client:
                    receipt = client.get("/api/resource-commands/" + str(key))
                    assert receipt.status_code == 200 and receipt.json()["response"] == response
                    replay = client.patch(case.path, json=payload, headers={"Idempotency-Key": str(key)})
                    assert replay.status_code == 200 and replay.json() == response
                    assert replay.headers["Idempotency-Replayed"] == "true"
                    current = client.get(case.path).json()
                    saved = client.patch(case.path + "/table", json={"expected_updated_at": current["updated_at"], "note": "New version"}, headers={"Idempotency-Key": str(uuid4())})
                    assert saved.status_code == 200, saved.text
                    events = client.get(case.path + "/history").json()["items"]
                    assert len(events) == 2 and events[1]["after"] == snapshot
                    assert events[0]["after"]["note"] == "New version"
        finally:
            get_settings.cache_clear()
