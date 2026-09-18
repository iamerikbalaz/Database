"""Real independent sessions and process death, using only disposable test DBs."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.packaging_dispatch_lease import PackagingLeaseError, packaging_dispatch_lease
from test_materials_postgresql import isolated_postgresql_database, POSTGRES_TEST_ADMIN_URL

pytestmark = pytest.mark.skipif(POSTGRES_TEST_ADMIN_URL is None, reason="Requires isolated PostgreSQL test configuration")


@pytest.fixture
def engines():
    with isolated_postgresql_database() as url:
        values = [create_engine(url, pool_pre_ping=True) for _ in range(2)]
        try: yield values
        finally:
            for engine in values: engine.dispose()


def test_pg_dispatch_lease_excludes_another_engine_without_an_open_transaction(engines):
    identifier = uuid4(); barrier = Barrier(2); rejected = Event()
    def claim(engine):
        barrier.wait(timeout=10)
        try:
            with packaging_dispatch_lease(engine, identifier) as lease:
                assert rejected.wait(10); lease.require_owned()
                with engine.connect() as observer:
                    row = observer.execute(text("SELECT state,xact_start FROM pg_stat_activity WHERE pid=:pid"), {"pid": lease._pid}).one()
                    assert row.state == "idle" and row.xact_start is None
                return "claimed"
        except PackagingLeaseError as failure:
            assert failure.code == "PACKAGING_DISPATCH_BUSY"
            rejected.set(); return "blocked"
    with ThreadPoolExecutor(2) as pool:
        pending = [pool.submit(claim, engine) for engine in engines]
        assert sorted(item.result(timeout=20) for item in pending) == ["blocked", "claimed"]


def test_pg_dispatch_leases_for_distinct_executions_can_coexist(engines):
    with packaging_dispatch_lease(engines[0], uuid4()) as first:
        with packaging_dispatch_lease(engines[1], uuid4()) as second:
            assert first._pid != second._pid
            first.require_owned(); second.require_owned()


@pytest.mark.parametrize("error", [RuntimeError, SQLAlchemyError, KeyboardInterrupt])
def test_pg_dispatch_closes_physical_session_after_exception_and_can_reacquire(engines, error):
    identifier = uuid4()
    with pytest.raises(error):
        with packaging_dispatch_lease(engines[0], identifier) as lease:
            pid = lease._pid
            raise error("Synthetic dispatch interruption")
    with engines[1].connect() as observer:
        assert observer.scalar(text("SELECT count(*) FROM pg_locks WHERE pid=:pid AND locktype='advisory'"), {"pid": pid}) == 0
    with packaging_dispatch_lease(engines[0], identifier) as retry:
        assert retry._pid != pid; retry.require_owned()
    with pytest.raises(PackagingLeaseError) as failure: lease.require_owned()
    assert failure.value.code == "PACKAGING_DISPATCH_LEASE_LOST"


def test_pg_killed_dedicated_session_is_never_silently_reconnected(engines):
    identifier = uuid4()
    with packaging_dispatch_lease(engines[0], identifier) as lease:
        with engines[1].connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
            # Only the PID just created by this test in this disposable DB.
            assert observer.scalar(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE pid=:pid AND datname=current_database()"), {"pid": lease._pid}) is True
        with pytest.raises(PackagingLeaseError) as failure: lease.require_owned()
        assert failure.value.code == "PACKAGING_DISPATCH_LEASE_LOST"
        with packaging_dispatch_lease(engines[1], identifier) as replacement: replacement.require_owned()
        with pytest.raises(PackagingLeaseError): lease.require_owned()


def test_pg_unexpected_unlock_is_detected_even_when_session_stays_connected(engines):
    identifier = uuid4()
    with packaging_dispatch_lease(engines[0], identifier) as lease:
        assert lease._connection.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": lease._key}) is True
        with pytest.raises(PackagingLeaseError) as failure: lease.require_owned()
        assert failure.value.code == "PACKAGING_DISPATCH_LEASE_LOST"
        with packaging_dispatch_lease(engines[1], identifier) as replacement: replacement.require_owned()


def test_pg_actual_dispatch_process_death_releases_session_lock(engines):
    identifier = uuid4()
    script = """import os,sys
from uuid import UUID
from sqlalchemy import create_engine
from app.packaging_dispatch_lease import packaging_dispatch_lease
engine=create_engine(os.environ['LEASE_TEST_DB'])
with packaging_dispatch_lease(engine, UUID(os.environ['LEASE_TEST_ID'])) as lease:
    lease.require_owned()
    print('OWNED', flush=True)
    sys.stdin.readline()
"""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(Path.cwd()),
        "LEASE_TEST_DB": engines[0].url.render_as_string(hide_password=False), "LEASE_TEST_ID": str(identifier)}
    process = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=env)
    try:
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(process.stdout.readline)
            try: assert pending.result(timeout=10).strip() == "OWNED"
            except BaseException:
                process.kill(); process.wait(timeout=5); raise
        with pytest.raises(PackagingLeaseError) as failure:
            with packaging_dispatch_lease(engines[1], identifier): pytest.fail("Live process lost its lease")
        assert failure.value.code == "PACKAGING_DISPATCH_BUSY"
        process.kill(); process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while True:
            try:
                with packaging_dispatch_lease(engines[1], identifier) as recovered: recovered.require_owned()
                break
            except PackagingLeaseError as failure:
                assert failure.code == "PACKAGING_DISPATCH_BUSY" and time.monotonic() < deadline
                time.sleep(0.02)
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=5)
        process.stdin.close(); process.stdout.close()
