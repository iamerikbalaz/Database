"""Internal staging namespace; a lease alone never authorizes cloud writes."""
from app.dispatch_lease import DispatchLeaseError, dispatch_lease


class StagingLeaseError(DispatchLeaseError):
    prefix = "GCS"


def staging_dispatch_lease(engine, job_id, *, allow_test_sqlite=False):
    return dispatch_lease(engine, job_id, namespace=b"reawote/staging-dispatch/v1/",
        error_type=StagingLeaseError, allow_test_sqlite=allow_test_sqlite)
