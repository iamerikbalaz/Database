"""Stable packaging namespace on the shared dedicated-session lease."""
from app.dispatch_lease import DispatchLeaseError, dispatch_lease


class PackagingLeaseError(DispatchLeaseError):
    prefix = "PACKAGING"


def packaging_dispatch_lease(engine, execution_id, *, allow_test_sqlite=False):
    return dispatch_lease(engine, execution_id, namespace=b"reawote/packaging-dispatch/v1/",
        error_type=PackagingLeaseError, allow_test_sqlite=allow_test_sqlite)
