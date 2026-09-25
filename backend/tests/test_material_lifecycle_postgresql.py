"""Lifecycle constraints and independent-session races on owned PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.db.models import MaterialLifecycleEvent, MaterialLifecycleState, PBRMaterial
from test_material_archives import attach, prepare, apply, path
from test_materials_postgresql import (
    POSTGRES_TEST_ADMIN_URL, isolated_postgresql_database, migrated_postgresql_url,
    review_pg_case, _review_pg_case, _current_head, staging_history_pg, _pg_reservation_payload,
)

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="Requires isolated PostgreSQL test configuration")


@pytest.fixture
def lifecycle_case(review_pg_case):
    return attach(review_pg_case)


def test_postgresql_archive_restore_and_replay_preserve_the_material(lifecycle_case):
    case = lifecycle_case
    with case.client_for() as client:
        before = client.get(case.path).json()
        payload = prepare(client, case.material.id)
        archived = apply(client, case.material.id, payload)
        assert archived.status_code == 200, archived.json()
        assert client.get(case.path).status_code == 404
        assert apply(client, case.material.id, prepare(client, case.material.id, "RESTORE")).status_code == 200
        assert apply(client, case.material.id, payload).json() == archived.json()
        after = client.get(case.path).json()
        for key in ("id", "sequence_number", "technical_identity", "folder_path", "assigned_processor_id"):
            assert after[key] == before[key]
    with case.database.session() as session:
        state = session.get(MaterialLifecycleState, case.material.id)
        assert state.version == 2 and not state.is_archived
        assert [item.version for item in session.scalars(select(MaterialLifecycleEvent).where(
            MaterialLifecycleEvent.material_id == case.material.id).order_by(MaterialLifecycleEvent.version))] == [1, 2]


@pytest.mark.parametrize("dispatched,bypass_api", [(False, False), (True, False), (True, True)])
def test_postgresql_closed_staging_attempt_still_blocks_archive(staging_history_pg, monkeypatch, dispatched, bypass_api):
    import staging_history_support as journal
    from app.api import material_archives
    case, job_id = staging_history_pg
    with case.database.session() as session:
        if dispatched: journal.dispatch(session, job_id)
        journal.close(session, job_id, dispatched=dispatched); session.commit()
    with case.client_for() as client:
        view = client.get(path(case.material.id) + "/preview?action=ARCHIVE").json()
        assert view["can_apply"] is (not dispatched)
        if dispatched: assert view["blocked_code"] == "MATERIAL_LIFECYCLE_EXTERNAL_STATE_BLOCKED"
        body = {"action": "ARCHIVE", "request_key": str(uuid4()), "expected_version": view["version"],
            "expected_input_sha256": view["input_sha256"], "reason": "Reviewed synthetic closure", "acknowledge": True}
        if bypass_api: monkeypatch.setattr(material_archives, "_eligible", lambda *args: None)
        response = apply(client, case.material.id, body)
        assert response.status_code == (503 if bypass_api else 409 if dispatched else 200), response.json()
    with case.database.session() as session:
        assert (session.get(MaterialLifecycleState, case.material.id) is None) is dispatched


@pytest.mark.parametrize("same_actor", [False, True])
def test_postgresql_competing_commands_have_one_transition(lifecycle_case, same_actor):
    case = lifecycle_case; ready = Barrier(2)
    with case.client_for() as first, case.client_for(0 if same_actor else 3) as second:
        payload = prepare(first, case.material.id)
        other = payload if same_actor else {**payload, "request_key": str(uuid4())}
        def send(client, body):
            ready.wait(timeout=10)
            return apply(client, case.material.id, body)
        with ThreadPoolExecutor(2) as pool:
            requests = [pool.submit(send, first, payload), pool.submit(send, second, other)]
            results = [item.result(timeout=20) for item in requests]
        assert sorted(item.status_code for item in results) == ([200, 200] if same_actor else [200, 409])
        if same_actor: assert results[0].json() == results[1].json()
    with case.database.session() as session:
        assert session.get(MaterialLifecycleState, case.material.id).version == 1


def test_postgresql_archive_racing_edit_rechecks_the_locked_preview(lifecycle_case):
    case = lifecycle_case; ready = Barrier(2)
    with case.client_for() as archiver, case.client_for(3) as editor:
        payload = prepare(archiver, case.material.id)
        def archive():
            ready.wait(timeout=10)
            return apply(archiver, case.material.id, payload)
        def edit():
            ready.wait(timeout=10)
            return editor.patch(case.path, json={"material_name": "Concurrent reviewed edit"})
        with ThreadPoolExecutor(2) as pool:
            saved = pool.submit(archive); changed = pool.submit(edit)
            result = (saved.result(timeout=20).status_code, changed.result(timeout=20).status_code)
        assert result in {(200, 404), (409, 200)}


@pytest.mark.parametrize("owner_first", [False, True])
def test_postgresql_archive_and_packaging_reservation_cannot_both_win(lifecycle_case, monkeypatch, owner_first):
    from app.api import packaging_jobs
    from app.db.models import MaterialPackagingExecution
    case = lifecycle_case; reservation = _pg_reservation_payload(case)
    entered = Event(); release = Event(); calls = 0
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Synthetic reservation was not released")
    if owner_first:
        original = packaging_jobs.approved_inputs
        def locked(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs); calls += 1
            if calls == 2: hold()
            return result
        monkeypatch.setattr(packaging_jobs, "approved_inputs", locked)
    else: case.packaging.callback = hold
    with case.client_for() as reserving, case.client_for(3) as archiver:
        payload = prepare(archiver, case.material.id)
        with ThreadPoolExecutor(2) as pool:
            reservation_result = pool.submit(reserving.post, case.path + "/packaging-executions", json=reservation)
            try:
                assert entered.wait(15)
                archive_result = pool.submit(apply, archiver, case.material.id, payload)
                if owner_first:
                    with pytest.raises(TimeoutError): archive_result.result(timeout=0.15)
                else: assert archive_result.result(timeout=10).status_code == 200
            finally: release.set()
            assert reservation_result.result(timeout=20).status_code == (201 if owner_first else 404)
            assert archive_result.result(timeout=20).status_code == (409 if owner_first else 200)
    with case.database.session() as session:
        assert (session.get(MaterialLifecycleState, case.material.id) is None) is owner_first
        assert bool(list(session.scalars(select(MaterialPackagingExecution).where(
            MaterialPackagingExecution.material_id == case.material.id)))) is owner_first


def test_postgresql_archive_commit_serializes_with_account_revocation(lifecycle_case, monkeypatch):
    from app.api import material_archives
    case = lifecycle_case; entered = Event(); release = Event()
    with case.client_for() as actor, case.client_for(3) as admin:
        payload = prepare(actor, case.material.id); original = material_archives._eligible
        def hold(*args):
            result = original(*args); entered.set()
            if not release.wait(15): raise TimeoutError("Synthetic archive was not released")
            return result
        monkeypatch.setattr(material_archives, "_eligible", hold)
        with ThreadPoolExecutor(2) as pool:
            pending = pool.submit(apply, actor, case.material.id, payload)
            try:
                assert entered.wait(10)
                revoking = pool.submit(admin.patch, "/api/internal-users/" + str(case.users[0].id), json={"is_active": False})
                with pytest.raises(TimeoutError): revoking.result(timeout=0.15)
            finally: release.set()
            assert pending.result(timeout=20).status_code == 200
            assert revoking.result(timeout=20).status_code == 200
        assert actor.get(path(case.material.id) + "/commands/" + payload["request_key"]).status_code == 401
        assert apply(actor, case.material.id, payload).status_code == 401
        assert len(admin.get(path(case.material.id) + "/history").json()["items"]) == 1


@pytest.mark.parametrize("restore", [False, True])
def test_postgresql_archive_during_inventory_io_rejects_late_observation(lifecycle_case, restore):
    from test_material_review import scan
    case = lifecycle_case; entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(15): raise TimeoutError("Synthetic inventory was not released")
    case.inventory.callback = hold
    with case.client_for(1) as reader, case.client_for() as admin:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(scan, reader, case.path, generation=0)
            try:
                assert entered.wait(10)
                assert apply(admin, case.material.id, prepare(admin, case.material.id)).status_code == 200
                if restore: assert apply(admin, case.material.id, prepare(admin, case.material.id, "RESTORE")).status_code == 200
            finally: release.set()
            assert pending.result(timeout=20).status_code == (409 if restore else 404)


@pytest.mark.parametrize("table,statement", [
    ("material_lifecycle_events", "UPDATE material_lifecycle_events SET reason='replacement' WHERE material_id=:id"),
    ("material_lifecycle_events", "DELETE FROM material_lifecycle_events WHERE material_id=:id"),
    ("material_lifecycle_events", "TRUNCATE material_lifecycle_events CASCADE"),
    ("material_lifecycle_states", "DELETE FROM material_lifecycle_states WHERE material_id=:id"),
    ("material_lifecycle_states", "TRUNCATE material_lifecycle_states CASCADE"),
])
def test_postgresql_lifecycle_evidence_cannot_be_erased(lifecycle_case, table, statement):
    case = lifecycle_case
    with case.client_for() as client: assert apply(client, case.material.id, prepare(client, case.material.id)).status_code == 200
    with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
        connection.execute(text(statement), {"id": case.material.id})
    with case.database.engine.connect() as connection:
        assert connection.scalar(text(f"SELECT count(*) FROM {table} WHERE material_id=:id"), {"id": case.material.id}) == 1


@pytest.mark.parametrize("version,archived", [(1, True), (2, False), (2, True), (0, False)])
def test_postgresql_lifecycle_state_without_exact_evidence_cannot_commit(lifecycle_case, version, archived):
    case = lifecycle_case
    with pytest.raises(DBAPIError), case.database.engine.begin() as connection:
        connection.execute(text("INSERT INTO material_lifecycle_states(material_id,version,is_archived,changed_at) VALUES (:id,:version,:archived,clock_timestamp())"),
            {"id": case.material.id, "version": version, "archived": archived})
    with case.database.session() as session: assert session.get(MaterialLifecycleState, case.material.id) is None


def test_postgresql_lifecycle_prior_schema_preserved_and_populated_downgrade_refused():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        config = Config("alembic.ini"); command.upgrade(config, "20260918_0022")
        fixture = _review_pg_case(url); case = next(fixture)
        try:
            with case.database.session() as session:
                original = tuple(session.execute(text("SELECT technical_identity, sequence_number, folder_path, assigned_processor_id FROM pbr_materials WHERE id=:id"), {"id": case.material.id}).one())
            command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
            with case.database.session() as session:
                assert not list(session.scalars(select(MaterialLifecycleEvent)))
                assert session.get(MaterialLifecycleState, case.material.id) is None
                material = session.get(PBRMaterial, case.material.id)
                assert original == (material.technical_identity, material.sequence_number, material.folder_path, material.assigned_processor_id)
            command.downgrade(config, "20260918_0022")
            assert not inspect(case.database.engine).has_table("material_lifecycle_states")
            command.upgrade(config, "head"); attach(case)
            with case.client_for() as client: assert apply(client, case.material.id, prepare(client, case.material.id)).status_code == 200
            with pytest.raises(DBAPIError, match="Material lifecycle history exists"):
                command.downgrade(config, "20260918_0022")
            with case.database.engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == _current_head()
                assert connection.scalar(text("SELECT count(*) FROM material_lifecycle_events")) == 1
        finally: fixture.close(); get_settings.cache_clear()
