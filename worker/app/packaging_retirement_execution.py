"""Retirement under the existing recorded execution lease, without source IO."""
import os

from app.packaging_execution import (_check, _check_old_workspaces, _Deadline, _errors,
    _operation, _roots, _validate_request)
from app.packaging_retirement import retire_packages
from app.packaging_stage import _identity


def retire_execution(request, *, roots, retirement_id, expected_proof_sha256):
    """Internal caller must hold durable application retirement authorization."""
    with _errors():
        _validate_request(request); deadline = _Deadline(120)
        with _roots(roots) as (handles, binding, verify_roots):
            with _operation(request, handles, binding, verify_roots, create=False) as (journal, record, _, verify):
                _check(record["version"] == 2, "PACKAGING_DISPATCH_REQUIRED")
                _check(record["status"] == "READY" and record["dispatch"]["terminal"] in {"OPEN", "CLOSED"},
                    "PACKAGING_RETIREMENT_NOT_READY")
                _check(record["result"]["proof_sha256"] == expected_proof_sha256, "PACKAGING_RETIREMENT_PROOF_MISMATCH")
                verify(); _check_old_workspaces(handles, record)
                result = retire_packages(artifact_root=roots.artifacts, operation_id=request.operation_id,
                    request_hash_value=request.sha256, plan_hash=request.plan_hash,
                    expected_proof_sha256=expected_proof_sha256, retirement_id=retirement_id,
                    expected_root_identity=_identity(os.fstat(handles["artifacts"])), max_seconds=deadline.remaining(120))
                verify()
                return result
