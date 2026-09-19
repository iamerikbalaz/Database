"""Real PostgreSQL fences local retirement against staging and evidence rewrites."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models import (MaterialPackagingExecution, MaterialPackagingObservation, MaterialPackagingState,
    MaterialPackagingRetirement as Intent, MaterialPackagingRetirementDispatch as Dispatch,
    MaterialPackagingRetirementObservation as Observation,
    PublicationStagingJob, PublicationStagingItem, PublicationStagingOwner, PublicationStagingState)
from app.material_review import canonical_hash
from app.packaging_contract import PreparedPackaging
from app.packaging_retirement_contract import PackagingRetirementCommand
from test_materials_postgresql import (POSTGRES_TEST_ADMIN_URL, migrated_postgresql_url, review_pg_case,
    isolated_postgresql_database, _review_pg_case, _pg_staging_case, _pg_dispatch_case, _clone_staging_values)
from test_staging_reservations import PATH, close_payload

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="Isolated PostgreSQL is required")


def values(case, package):
    with case.database.session() as session:
        item = session.get(MaterialPackagingExecution, package.id)
        state = session.get(MaterialPackagingState, item.id)
        observed = session.get(MaterialPackagingObservation, state.last_observation_id)
        bound = PreparedPackaging.model_validate(item.worker_request)
        identifier = uuid4()
        action = PackagingRetirementCommand(retirement_id=str(identifier), proof_sha256=observed.proof_sha256)
        files = observed.worker_result["stored"]["payload"]["files"]
        return {"id": identifier, "material_id": item.material_id, "execution_id": item.id,
            "accepted_observation_id": observed.id, "actor_id": case.users[0].id, "issuer_session_id": uuid4(),
            "request_key": uuid4(), "request_hash": canonical_hash({"synthetic_retirement": str(identifier)}),
            "reason": "Explicit synthetic local-copy retirement", "worker_request_hash": item.worker_request_hash,
            "plan_hash": bound.request.plan_hash, "proof_sha256": observed.proof_sha256,
            "worker_command_hash": canonical_hash(action.document(bound)),
            "file_count": len(files), "byte_count": sum(item["size"] for item in files)}


@pytest.fixture
def retirement_pg(review_pg_case):
    package, staging = _pg_staging_case(review_pg_case)
    return review_pg_case, package, staging, values(review_pg_case, package)


def save_intent(case, payload):
    with case.database.session() as session:
        session.add(Intent(**payload)); session.commit()


def action_values(intent, previous=None, **changes):
    return {"id": uuid4(), "retirement_id": intent["id"], "ordinal": 1 if previous is None else previous["ordinal"] + 1,
        "previous_dispatch_id": None if previous is None else previous["id"], "action": "EXECUTE" if previous is None else "RECONCILE",
        "actor_id": intent["actor_id"], "issuer_session_id": uuid4(), "request_key": uuid4(), "request_hash": "a" * 64,
        "reason": "Synthetic explicit retirement command", "worker_command_hash": intent["worker_command_hash"], **changes}


def add_action(case, intent, previous=None, **changes):
    payload = action_values(intent, previous, **changes)
    with case.database.session() as session:
        session.add(Dispatch(**payload)); session.commit()
    return payload


def receipt(intent):
    return {"schema_version": 1, "status": "REMOVED", "operation_id": str(intent["execution_id"]),
        "request_hash": intent["worker_request_hash"], "plan_hash": intent["plan_hash"], "proof_sha256": intent["proof_sha256"],
        "retirement_id": str(intent["id"]), "retirement_request_hash": intent["worker_command_hash"],
        "file_count": intent["file_count"], "byte_count": intent["byte_count"]}


def observed(intent, action, *, removed=True, **changes):
    return {"id": uuid4(), "retirement_id": intent["id"], "dispatch_id": action["id"],
        "outcome": "REMOVED" if removed else "UNCERTAIN", "receipt": receipt(intent) if removed else None,
        "failure_code": None if removed else "PACKAGING_UNAVAILABLE", "actor_current": False, "lease_current": False, **changes}


def test_retirement_keeps_original_packaged_history_and_accepts_late_facts(retirement_pg):
    case, package, _, intent = retirement_pg
    with case.database.session() as session:
        before = copy.deepcopy(session.get(MaterialPackagingObservation, intent["accepted_observation_id"]).worker_result)
    save_intent(case, intent); first = add_action(case, intent); second = add_action(case, intent, first)
    with case.database.session() as session:
        session.add(Observation(**observed(intent, second))); session.commit()
        session.add(Observation(**observed(intent, first, removed=False))); session.commit()
        assert len(list(session.scalars(select(Observation).where(Observation.retirement_id == intent["id"])))) == 2
        assert session.scalar(select(Observation.id).where(Observation.retirement_id == intent["id"], Observation.outcome == "REMOVED"))
        assert session.get(MaterialPackagingState, package.id).status == "PACKAGED"
        assert session.get(MaterialPackagingObservation, intent["accepted_observation_id"]).worker_result == before
    with pytest.raises(DBAPIError): add_action(case, intent, second)


@pytest.mark.parametrize("field", ["material_id", "execution_id", "accepted_observation_id", "worker_request_hash", "plan_hash", "proof_sha256", "file_count", "byte_count"])
def test_retirement_requires_exact_accepted_copy_bindings(retirement_pg, field):
    case, _, _, intent = retirement_pg; changed = dict(intent)
    if field.endswith("_id"): changed[field] = uuid4()
    elif field.endswith("count"): changed[field] += 1
    else: changed[field] = "f" * 64
    with pytest.raises(DBAPIError): save_intent(case, changed)
    save_intent(case, intent)


@pytest.mark.parametrize("table", [Intent.__tablename__, Dispatch.__tablename__, Observation.__tablename__])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
def test_retirement_provenance_is_append_only(retirement_pg, table, operation):
    case, _, _, intent = retirement_pg; save_intent(case, intent); action = add_action(case, intent)
    with case.database.session() as session:
        session.add(Observation(**observed(intent, action))); session.commit()
    statement = f"UPDATE {table} SET id=id" if operation == "UPDATE" else f"DELETE FROM {table}" if operation == "DELETE" else f"TRUNCATE {table} CASCADE"
    with case.database.engine.begin() as connection:
        with pytest.raises(DBAPIError, match="append-only"): connection.execute(text(statement))


@pytest.mark.parametrize("change", ["gap", "previous", "first-action", "command", "duplicate-key"])
def test_dispatch_cannot_fork_or_replace_retirement_history(retirement_pg, change):
    case, _, _, intent = retirement_pg; save_intent(case, intent); first = add_action(case, intent)
    changed = action_values(intent, first)
    if change == "gap": changed["ordinal"] = 3
    elif change == "previous": changed["previous_dispatch_id"] = uuid4()
    elif change == "first-action": changed["action"] = "EXECUTE"
    elif change == "command": changed["worker_command_hash"] = "b" * 64
    else: changed["request_key"] = first["request_key"]
    with case.database.session() as session:
        session.add(Dispatch(**changed))
        with pytest.raises(DBAPIError): session.commit()
    assert add_action(case, intent, first)["ordinal"] == 2


@pytest.mark.parametrize("field", ["schema_version", "status", "operation_id", "request_hash", "plan_hash", "proof_sha256", "retirement_id", "retirement_request_hash", "file_count", "byte_count", "extra", "boolean"])
def test_observation_cannot_substitute_a_plausible_retirement_receipt(retirement_pg, field):
    case, _, _, intent = retirement_pg; save_intent(case, intent); action = add_action(case, intent)
    wrong = receipt(intent)
    if field in {"schema_version", "file_count", "byte_count"}: wrong[field] += 1
    elif field == "boolean": wrong["schema_version"] = True
    elif field == "extra": wrong["diagnostic"] = "Synthetic invalid diagnostic"
    elif field == "status": wrong[field] = "READY"
    elif field.endswith("_id"): wrong[field] = str(uuid4())
    else: wrong[field] = "f" * 64
    with case.database.session() as session:
        session.add(Observation(**observed(intent, action, receipt=wrong)))
        with pytest.raises(DBAPIError): session.commit()
    with case.database.session() as session:
        session.add(Observation(**observed(intent, action))); session.commit()


def test_active_staging_blocks_retirement_until_explicit_closure(retirement_pg):
    case, _, body, intent = retirement_pg
    with case.client_for() as client:
        saved = client.post(PATH, json=body); assert saved.status_code == 201
        with pytest.raises(DBAPIError): save_intent(case, intent)
        assert client.post(PATH + "/" + saved.json()["id"] + "/close", json=close_payload(saved.json())).status_code == 200
    save_intent(case, intent)


def staging_clone(case, body):
    with case.client_for() as client:
        saved = client.post(PATH, json=body); assert saved.status_code == 201
        assert client.post(PATH + "/" + saved.json()["id"] + "/close", json=close_payload(saved.json())).status_code == 200
    return _clone_staging_values(case, saved.json())


def add_staging(session, case, job, item, *, owner=True):
    session.add(PublicationStagingJob(**job)); session.flush()
    session.add(PublicationStagingState(job_id=job["id"], status="RESERVED"))
    session.add(PublicationStagingItem(**item)); session.flush()
    if owner: session.add(PublicationStagingOwner(job_id=job["id"], material_id=case.material.id, active=True))


def test_direct_staging_item_and_later_owner_cannot_bypass_retirement(retirement_pg):
    case, _, body, intent = retirement_pg; job, item = staging_clone(case, body)
    # An item inserted first in the same transaction must not permit its later
    # owner claim once a retirement intent has been added.
    with case.database.session() as session:
        add_staging(session, case, job, item, owner=False)
        session.add(Intent(**intent)); session.flush()
        session.add(PublicationStagingOwner(job_id=job["id"], material_id=case.material.id, active=True))
        with pytest.raises(DBAPIError, match="retired local copy"): session.commit()
    save_intent(case, intent)
    with case.database.session() as session:
        with pytest.raises(DBAPIError, match="retired local copy"): add_staging(session, case, job, item)


@pytest.mark.parametrize("first", ["concurrent", "retirement", "staging"])
def test_real_separate_sessions_cannot_claim_staging_and_retirement_together(retirement_pg, first):
    case, _, body, intent = retirement_pg; job, item = staging_clone(case, body); barrier = Barrier(2)
    def claim(retire):
        with case.database.session() as session:
            if first == "concurrent": barrier.wait(timeout=10)
            try:
                if retire: session.add(Intent(**intent))
                else: add_staging(session, case, job, item)
                session.commit(); return True
            except DBAPIError:
                session.rollback(); return False
    if first == "concurrent":
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(claim, value) for value in (True, False)]
            results = [future.result(timeout=30) for future in futures]
        assert sorted(results) == [False, True]
    else:
        assert claim(first == "retirement") is True
        assert claim(first != "retirement") is False
    with case.database.session() as session:
        retired = session.get(Intent, intent["id"]) is not None
        owner = session.get(PublicationStagingOwner, (job["id"], case.material.id))
        assert retired != (owner is not None and owner.active)


def test_0025_upgrade_preserves_0024_accepted_history_and_populated_downgrade_refuses():
    with isolated_postgresql_database() as url, pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url); get_settings.cache_clear()
        try:
            config = Config("alembic.ini"); command.upgrade(config, "20260918_0024")
            with contextmanager(_review_pg_case)(url) as case:
                # Seed a real accepted 0024 package without invoking the newer
                # staging/download routes, which now require the 0025 fence.
                from test_packaging_reservations import close_body
                package = _pg_dispatch_case(case)
                with case.client_for() as client:
                    assert client.post(package.path + "/run", json=close_body()).json()["status"] == "PACKAGED"
                intent = values(case, package)
                with case.database.session() as session:
                    before = copy.deepcopy(session.get(MaterialPackagingObservation, intent["accepted_observation_id"]).worker_result)
                command.upgrade(config, "head"); command.current(config); command.heads(config); command.check(config)
                command.downgrade(config, "20260918_0024")
                assert not inspect(case.database.engine).has_table(Intent.__tablename__)
                command.upgrade(config, "head"); command.check(config)
                save_intent(case, intent)
                with pytest.raises(DBAPIError, match="Retirement evidence exists"): command.downgrade(config, "20260918_0024")
                with case.database.session() as session:
                    assert session.scalar(text("SELECT version_num FROM alembic_version")) == "20260919_0025"
                    assert session.get(MaterialPackagingState, package.id).status == "PACKAGED"
                    assert session.get(MaterialPackagingObservation, intent["accepted_observation_id"]).worker_result == before
        finally: get_settings.cache_clear()


@pytest.fixture
def retirement_runtime_pg(retirement_pg):
    from app.main import create_app
    from test_application_access import ORIGIN, PASSWORD
    from test_packaging_retirement_actions import RemovalStub
    case, package, staging, intent = retirement_pg
    removal = RemovalStub(); case.packaging.retire = removal.retire
    settings = case.app.state.settings.model_copy(update={"packaging_retirement_enabled":True})
    app = create_app(settings, case.database, inventory_client=case.inventory, technical_client=case.technical,
        packaging_client=case.packaging)
    def client_for(index=0):
        client = TestClient(app, base_url=ORIGIN)
        response = client.post("/api/auth/login", json={"email":case.users[index].email,"password":PASSWORD}, headers={"Origin":ORIGIN})
        assert response.status_code == 200
        client.headers.update({"Origin":ORIGIN,"X-CSRF-Token":response.json()["csrf_token"]})
        return client
    body = {"idempotency_key":str(uuid4()), "expected_observation_id":str(intent["accepted_observation_id"]),
        "expected_proof_sha256":intent["proof_sha256"], "acknowledgement":"REMOVE_LOCAL_COPY", "reason":"Retire synthetic PostgreSQL package"}
    return SimpleNamespace(case=case, package=package, staging=staging, removal=removal, client=client_for,
        path=package.path + "/retirement", body=body)


def test_retirement_api_commits_before_io_and_same_actor_replay_does_not_dispatch(retirement_runtime_pg):
    item = retirement_runtime_pg; entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(20): raise TimeoutError("Synthetic retirement was not released")
    item.removal.callback = hold
    with item.client() as actor, item.client() as replaying, item.client(3) as other:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, item.path, json=item.body)
            try:
                assert entered.wait(15)
                current = other.get(item.path).json()["retirement"]
                assert current["status"] == "RUNNING"
                exact = replaying.post(item.path, json=item.body)
                assert exact.status_code == 201 and exact.json() == current
                assert other.post(item.path, json={**item.body,"idempotency_key":str(uuid4())}).status_code == 409
                assert other.get(item.package.path + "/artifacts").status_code == 409
                assert other.post(PATH, json=item.staging).status_code == 409
                with item.case.database.engine.connect() as connection:
                    assert connection.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state LIKE 'idle in transaction%'")) == 0
            finally: release.set()
            completed = pending.result(timeout=30)
            assert completed.status_code == 201 and completed.json()["status"] == "REMOVED"
    assert len(item.removal.calls) == 1


@pytest.mark.parametrize("revoke", ["disable", "demote"])
def test_retirement_api_real_account_revocation_keeps_facts_and_another_admin_can_recover(retirement_runtime_pg, revoke):
    from app.packaging_client import PackagingClientError
    from test_packaging_retirement_actions import recover_body
    item = retirement_runtime_pg; entered = Event(); release = Event()
    def hold():
        entered.set()
        if not release.wait(20): raise TimeoutError("Synthetic retirement was not released")
    item.removal.callback = hold; item.removal.failure = PackagingClientError()
    with item.client() as actor, item.client(3) as admin:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, item.path, json=item.body)
            try:
                assert entered.wait(15)
                changed = admin.patch("/api/internal-users/" + str(item.case.users[0].id),
                    json={"is_active":False} if revoke == "disable" else {"role":"LEADERSHIP"})
                assert changed.status_code == 200
            finally: release.set()
            # The real account endpoint revokes every session on either role
            # change or disable. The in-flight actor therefore loses its session.
            assert pending.result(timeout=30).status_code == 401
        saved = admin.get(item.path).json()["retirement"]
        assert saved["status"] == "RECOVERY_REQUIRED"
        item.removal.callback = None; item.removal.failure = None
        result = admin.post(item.path + "/reconcile", json=recover_body(saved))
        assert result.status_code == 200 and result.json()["status"] == "REMOVED"
    assert item.removal.calls[0] == item.removal.calls[1]
    with item.case.database.session() as session:
        rows = list(session.scalars(select(Dispatch).where(Dispatch.retirement_id == UUID(saved["id"])).order_by(Dispatch.ordinal)))
        assert rows[0].actor_id != rows[1].actor_id
        original = session.scalar(select(Observation).where(Observation.dispatch_id == rows[0].id))
        assert original.actor_current is False and original.outcome == "UNCERTAIN"


def test_retirement_api_late_uncertain_result_after_actual_lease_loss_cannot_undo_verified_removal(retirement_runtime_pg, monkeypatch):
    from app.api import packaging_retirement as api
    from app.packaging_client import PackagingClientError
    from test_packaging_retirement_actions import recover_body
    item = retirement_runtime_pg; entered = Event(); release = Event(); leases = []; real = api.packaging_dispatch_lease
    @contextmanager
    def capture(*args, **kwargs):
        with real(*args, **kwargs) as lease: leases.append(lease); yield lease
    monkeypatch.setattr(api, "packaging_dispatch_lease", capture)
    def hold_first():
        if len(item.removal.calls) == 1:
            entered.set()
            if not release.wait(20): raise TimeoutError("Synthetic retirement was not released")
            raise PackagingClientError()
    item.removal.callback = hold_first
    with item.client() as actor, item.client(3) as admin:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(actor.post, item.path, json=item.body)
            try:
                assert entered.wait(15)
                with item.case.database.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    assert connection.scalar(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid=:pid AND datname=current_database()"),
                        {"pid":leases[0]._pid}) is True
                saved = admin.get(item.path).json()["retirement"]
                recovered = admin.post(item.path + "/reconcile", json=recover_body(saved))
                assert recovered.status_code == 200 and recovered.json()["status"] == "REMOVED"
            finally: release.set()
            late = pending.result(timeout=30)
            assert late.status_code == 201 and late.json() == recovered.json()
        assert admin.get(item.path).json()["retirement"] == recovered.json()
    with item.case.database.session() as session:
        rows = list(session.scalars(select(Dispatch).where(Dispatch.retirement_id == UUID(saved["id"])).order_by(Dispatch.ordinal)))
        old = session.scalar(select(Observation).where(Observation.dispatch_id == rows[0].id))
        newer = session.scalar(select(Observation).where(Observation.dispatch_id == rows[1].id))
        assert old.outcome == "UNCERTAIN" and old.lease_current is False and newer.outcome == "REMOVED"
