"""Global configuration races and immutable policies on isolated PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from test_global_packaging_settings import SETTINGS, update
from test_materials_postgresql import (POSTGRES_TEST_ADMIN_URL, isolated_postgresql_database,
    migrated_postgresql_url, review_pg_case)
from test_publication_batches import creation
from test_publication_preflight import prepare_candidate, preview

pytestmark = [pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="Requires isolated PostgreSQL test configuration"),
    pytest.mark.parametrize("review_pg_case", ["isolated-history"], indirect=True)]


@pytest.mark.parametrize("same_key", [True, False])
def test_global_settings_versions_serialize_competing_writes(review_pg_case, same_key):
    case = review_pg_case; barrier = Barrier(2)
    with case.client_for() as one, case.client_for(0 if same_key else 3) as two:
        first = update(); second = first if same_key else update()
        def send(client, body):
            barrier.wait(timeout=10)
            return client.post(SETTINGS, json=body)
        with ThreadPoolExecutor(2) as pool:
            tasks = [pool.submit(send, one, first), pool.submit(send, two, second)]
            responses = [task.result(timeout=20) for task in tasks]
        assert sorted(item.status_code for item in responses) == ([200, 200] if same_key else [200, 409])
        if same_key: assert responses[0].json() == responses[1].json()
        assert one.get(SETTINGS).json()["version"] == 1
    with case.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM packaging_settings_revisions")) == 1
    for statement in ("UPDATE packaging_settings_revisions SET storage_timezone='UTC'",
        "DELETE FROM packaging_settings_revisions", "TRUNCATE packaging_settings_revisions"):
        with case.database.engine.begin() as connection, pytest.raises(DBAPIError): connection.execute(text(statement))


def test_batch_locks_settings_then_preserves_its_rule_after_a_competing_update(review_pg_case, monkeypatch):
    from app import publication_preflight
    case = review_pg_case
    adapter = SimpleNamespace(database=case.database, materials=[case.material], client=lambda _: case.client_for())
    prepare_candidate(adapter, case.technical, case.path, human_approvals=False)
    entered = Event(); release = Event(); original = publication_preflight._candidate
    def hold(*args, **kwargs):
        entered.set()
        if not release.wait(15): raise TimeoutError("Settings race was not released")
        return original(*args, **kwargs)
    with case.client_for() as publisher, case.client_for(3) as editor:
        body = creation(preview(publisher, case.material.id), reason=None)
        monkeypatch.setattr(publication_preflight, "_candidate", hold)
        with ThreadPoolExecutor(2) as pool:
            batch_request = pool.submit(publisher.post, "/api/publication-batches", json=body)
            try:
                assert entered.wait(15)
                setting_request = pool.submit(editor.post, SETTINGS, json=update(cutoff_date="2099-01-01"))
                with pytest.raises(TimeoutError): setting_request.result(timeout=0.15)
            finally: release.set()
            batch = batch_request.result(timeout=25)
            assert batch.status_code == 201, batch.json()
            assert setting_request.result(timeout=25).status_code == 200
        monkeypatch.setattr(publication_preflight, "_candidate", original)
        old_policy = publisher.get(case.path + "/packaging-policy").json()["current"]
        newer = publisher.post("/api/publication-batches", json=creation(preview(publisher, case.material.id), reason=None))
        assert newer.status_code == 201, newer.json()
        new_policy = publisher.get(case.path + "/packaging-policy").json()["current"]
        assert new_policy["revision"] == 2 and new_policy["policy"] != old_policy["policy"]
        original_batch = batch.json()
        reserved = publisher.post(case.path + "/packaging-executions", json={"idempotency_key": str(uuid4()),
            "batch_id": original_batch["id"], "expected_snapshot_hash": original_batch["items"][0]["snapshot_hash"],
            "reason": "Use original frozen settings"})
        assert reserved.status_code == 201, reserved.json()
        assert reserved.json()["policy_id"] == old_policy["id"]
        assert publisher.get("/api/publication-batches/" + original_batch["id"]).json() == original_batch
    with case.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM material_approvals")) == 0
        assert connection.scalar(text("SELECT count(*) FROM material_content_approvals")) == 0


def test_automatic_policy_chain_rejects_fabricated_legacy_same_method(review_pg_case):
    case = review_pg_case
    adapter = SimpleNamespace(database=case.database, materials=[case.material], client=lambda _: case.client_for())
    prepare_candidate(adapter, case.technical, case.path, human_approvals=False)
    with case.client_for() as client:
        assert client.post("/api/publication-batches", json=creation(preview(client, case.material.id))).status_code == 201
    with case.database.engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(text("""INSERT INTO material_packaging_policies
            (id, material_id, revision, previous_id, actor_id, policy, storage_timezone, reason, evidence, evidence_hash)
            SELECT :id, material_id, revision + 1, id, actor_id, policy, storage_timezone, reason, '{}'::jsonb, evidence_hash
            FROM material_packaging_policies WHERE material_id=:material ORDER BY revision DESC LIMIT 1"""),
            {"id": uuid4(), "material": case.material.id})
