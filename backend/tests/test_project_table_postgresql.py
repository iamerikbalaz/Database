"""Project folder migration preserves legacy history/receipts and concurrent edits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from app.core.config import get_settings
from app.db.models import ResourceCommand, ResourceChangeEvent
from app.material_review import canonical_hash
from app.schemas import ProjectRead
from test_materials_postgresql import (_review_pg_case, isolated_postgresql_database, review_pg_case,
    migrated_postgresql_url, POSTGRES_TEST_ADMIN_URL)  # noqa: F401

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="isolated PostgreSQL required")


def test_project_folder_migration_preserves_old_receipts_and_history():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260925_0027")
            with contextmanager(_review_pg_case)(url) as case:
                key = uuid4(); identifier = case.material.project_id
                with case.database.session() as session:
                    raw = dict(session.execute(text("SELECT * FROM projects WHERE id=:id"), {"id": identifier}).mappings().one())
                    response = ProjectRead.model_validate(raw).model_dump(mode="json", exclude_unset=True)
                    assert "folder_path" not in response
                    snapshot = {name: value for name, value in response.items() if name not in {"created_at", "updated_at"}}
                    payload = {"notes": "Historical command"}
                    session.add(ResourceChangeEvent(kind="PROJECT", project_id=identifier, actor_id=case.users[0].id,
                        version=1, action="CREATED", before_snapshot={}, after_snapshot=snapshot,
                        before_hash=canonical_hash({}), after_hash=canonical_hash(snapshot)))
                    session.add(ResourceCommand(kind="PROJECT", project_id=identifier, actor_id=case.users[0].id,
                        request_key=key, action="UPDATED", privilege="CATALOG",
                        request_hash=canonical_hash({"schema_version": 1, "kind": "PROJECT", "action": "UPDATED", "target_id": str(identifier), "payload": payload}),
                        response_snapshot=response, response_hash=canonical_hash(response)))
                    session.commit()
                command.upgrade(config, "head"); command.check(config)
                with case.client_for() as client:
                    path = f"/api/projects/{identifier}"
                    replay = client.patch(path, json=payload, headers={"Idempotency-Key": str(key)})
                    assert replay.status_code == 200 and replay.json() == response
                    assert client.get("/api/resource-commands/" + str(key)).json()["response"] == response
                    current = client.get(path).json(); assert current["folder_path"] is None
                    saved = client.patch(path, json={"folder_path": r"R:\0. PROJECTS\0246_EXAMPLE_SCANNING_FABRICS_032026", "expected_updated_at": current["updated_at"]}, headers={"Idempotency-Key": str(uuid4())})
                    assert saved.status_code == 200, saved.text
                    history = client.get(path + "/history").json()["items"]
                    assert history[1]["after"] == snapshot and history[0]["after"]["folder_path"] == saved.json()["folder_path"]
                with pytest.raises(RuntimeError, match="Project folder references"):
                    command.downgrade(config, "20260925_0027")
        finally: get_settings.cache_clear()


def test_project_table_competing_actors_require_current_revision(review_pg_case):
    case = review_pg_case; barrier = Barrier(2); path = f"/api/projects/{case.material.project_id}"
    with case.client_for() as client: current = client.get(path).json()
    def change(actor):
        with case.client_for(actor) as client:
            barrier.wait(timeout=10)
            return client.patch(path, json={"expected_updated_at": current["updated_at"], "notes": f"Actor {actor}"}, headers={"Idempotency-Key": str(uuid4())}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(change, [0, 3]))
    assert sorted(results) == [200, 409]
