"""Fast process-local lease checks; PostgreSQL behavior is tested separately."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import UUID, uuid1, uuid4

import pytest
from sqlalchemy import create_engine

from app.packaging_dispatch_lease import PackagingLeaseError, packaging_dispatch_lease
from app.staging_dispatch_lease import StagingLeaseError, staging_dispatch_lease


@pytest.fixture(params=["PACKAGING", "GCS"])
def domain(request):
    return SimpleNamespace(prefix=request.param,
        acquire=packaging_dispatch_lease if request.param == "PACKAGING" else staging_dispatch_lease,
        error=PackagingLeaseError if request.param == "PACKAGING" else StagingLeaseError)


@pytest.fixture
def engine():
    value = create_engine("sqlite:///:memory:")
    yield value
    value.dispose()


def test_sqlite_requires_explicit_test_opt_in(domain, engine):
    with pytest.raises(domain.error, match="ownership") as failure:
        with domain.acquire(engine, uuid4()): pytest.fail("SQLite is not a distributed lease")
    assert failure.value.code == domain.prefix + "_DATABASE_UNSUPPORTED"


@pytest.mark.parametrize("value", [None, "invalid", str(uuid4()), UUID(int=0), uuid1()])
def test_invalid_execution_is_rejected_before_lease(domain, engine, value):
    with pytest.raises(ValueError):
        with domain.acquire(engine, value, allow_test_sqlite=True): pytest.fail("Invalid execution accepted")


def test_same_execution_is_non_reentrant_but_other_execution_can_run(domain, engine):
    identifier = uuid4()
    with domain.acquire(engine, identifier, allow_test_sqlite=True) as first:
        first.require_owned()
        with pytest.raises(domain.error) as failure:
            with domain.acquire(engine, identifier, allow_test_sqlite=True): pytest.fail("Nested lease accepted")
        assert failure.value.code == domain.prefix + "_DISPATCH_BUSY"
        with domain.acquire(engine, uuid4(), allow_test_sqlite=True) as other: other.require_owned()
        first.require_owned()
    with pytest.raises(domain.error) as failure: first.require_owned()
    assert failure.value.code == domain.prefix + "_DISPATCH_LEASE_LOST"
    with domain.acquire(engine, identifier, allow_test_sqlite=True) as retry: retry.require_owned()


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_exception_releases_lease_and_does_not_replace_original_error(domain, engine, error):
    identifier = uuid4()
    with pytest.raises(error):
        with domain.acquire(engine, identifier, allow_test_sqlite=True): raise error("Synthetic cancellation")
    with domain.acquire(engine, identifier, allow_test_sqlite=True) as retry: retry.require_owned()


def test_two_threads_have_only_one_owner(domain, engine):
    identifier = uuid4(); barrier = Barrier(2); rejected = Event()
    def claim():
        barrier.wait(timeout=5)
        try:
            with domain.acquire(engine, identifier, allow_test_sqlite=True) as lease:
                assert rejected.wait(5); lease.require_owned(); return "claimed"
        except domain.error as failure:
            assert failure.code == domain.prefix + "_DISPATCH_BUSY"
            rejected.set(); return "blocked"
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        assert sorted(future.result(timeout=10) for future in futures) == ["blocked", "claimed"]


def test_identical_uuid_in_distinct_operation_domains_does_not_share_a_test_lease(engine):
    identifier = uuid4()
    with packaging_dispatch_lease(engine, identifier, allow_test_sqlite=True) as packaging:
        with staging_dispatch_lease(engine, identifier, allow_test_sqlite=True) as staging:
            packaging.require_owned(); staging.require_owned()
