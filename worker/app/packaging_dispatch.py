"""Durable ordered commands fence delayed retries and permanent job closure."""
from dataclasses import dataclass
import os
from uuid import UUID

from app.packaging_execution import (ExecutionResult, _check, _Deadline, _errors, _execute_locked,
    _exists, _operation, _recover, _roots, _validate_request, prepare_packaging_request)
from app.packaging_retirement import reject_retired_packages
from app.packaging_stage import _identity


@dataclass(frozen=True)
class PackagingDispatch:
    id: UUID
    ordinal: int
    action: str

    def document(self): return {"id": str(self.id), "ordinal": self.ordinal, "action": self.action}


@dataclass(frozen=True)
class DispatchedPackaging:
    dispatch: PackagingDispatch
    terminal: str
    result: ExecutionResult


def validate_dispatch(value):
    _check(isinstance(value, PackagingDispatch) and isinstance(value.id, UUID) and value.id.version == 4
        and type(value.ordinal) is int and 1 <= value.ordinal <= 2**31 - 1
        and type(value.action) is str and value.action in {"EXECUTE", "RETRY", "RECONCILE", "CLOSE"}, "PACKAGING_DISPATCH_INVALID")
    _check(value.action != "EXECUTE" or value.ordinal == 1, "PACKAGING_DISPATCH_INVALID")
    _check(value.action not in {"RETRY", "RECONCILE"} or value.ordinal > 1, "PACKAGING_DISPATCH_INVALID")


def validate_record_dispatch(record):
    corrupt = "PACKAGING_EXECUTION_CORRUPT_STATE"
    value = record["dispatch"]
    _check(isinstance(value, dict) and set(value) == {"command", "terminal"}
        and value["terminal"] in {"OPEN", "CLOSING", "CLOSED"}, corrupt)
    command = value["command"]
    _check(isinstance(command, dict) and set(command) == {"id", "ordinal", "action"}, corrupt)
    try:
        parsed = PackagingDispatch(UUID(command["id"]), command["ordinal"], command["action"])
        validate_dispatch(parsed)
        _check(parsed.document() == command, corrupt)
    except (ValueError, TypeError, AttributeError):
        _check(False, corrupt)
    _check(not (command["action"] == "CLOSE" and value["terminal"] == "OPEN"), corrupt)
    _check(not (command["action"] in {"EXECUTE", "RETRY"} and value["terminal"] != "OPEN"), corrupt)
    if value["terminal"] == "CLOSED": _check(record["status"] in {"READY", "RETRY_REQUIRED"}, corrupt)


def dispatch_packaging(request, command, *, roots, report=None):
    """Persist the command under the inherited execution lease before any work.

    Equal commands only recover; lower ordinals or changed IDs never run. Closing
    intent permanently fences conversion, including after a crash during cleanup.
    """
    with _errors():
        _validate_request(request); validate_dispatch(command)
        if command.action in {"EXECUTE", "RETRY"}:
            expected = prepare_packaging_request(report, operation_id=request.operation_id, parts=request.parts,
                expected_source_revision_hash=request.source_revision_hash, expected_technical_report_hash=request.technical_report_hash,
                approval_context_hash=request.approval_context_hash, policy=request.policy, storage_timezone=request.storage_timezone,
                limits=request.limits)
            _check(expected == request, "PACKAGING_EXECUTION_PLAN_CHANGED")
        else: _check(report is None, "PACKAGING_DISPATCH_INVALID")
        deadline = _Deadline(request.limits.seconds)
        with _roots(roots) as (handles, binding, verify_roots):
            with _operation(request, handles, binding, verify_roots, create=True) as (journal, record, created, verify):
                if created:
                    _check(_exists(handles["artifacts"], str(request.operation_id)) is None, "PACKAGING_EXECUTION_UNEXPECTED_RESULT")
                else:
                    reject_retired_packages(artifact_root=roots.artifacts, operation_id=request.operation_id,
                        request_hash_value=request.sha256, plan_hash=request.plan_hash,
                        expected_root_identity=_identity(os.fstat(handles["artifacts"])))
                previous = record.get("dispatch")
                exact = False
                terminal = previous["terminal"] if previous else "OPEN"
                if previous:
                    last = previous["command"]
                    _check(command.ordinal >= last["ordinal"], "PACKAGING_DISPATCH_STALE")
                    if command.ordinal == last["ordinal"]:
                        _check(command.document() == last, "PACKAGING_DISPATCH_CONFLICT")
                        exact = True
                if command.action in {"EXECUTE", "RETRY"}:
                    _check(terminal == "OPEN", "PACKAGING_EXECUTION_CLOSED")
                    _check(not created or command.action == "EXECUTE", "PACKAGING_DISPATCH_ORDER")
                if not exact:
                    if command.action == "CLOSE" and terminal == "OPEN": terminal = "CLOSING"
                    record["version"] = 2
                    record["dispatch"] = {"command": command.document(), "terminal": terminal}
                    verify(); journal.write(record)
                if command.action in {"EXECUTE", "RETRY"} and not exact:
                    result = _execute_locked(request, report, roots, handles, journal, record, created, verify, deadline,
                        retry=command.action == "RETRY")
                else:
                    # An absent worker journal becomes a known incomplete first
                    # attempt; a delayed old EXECUTE will now be refused.
                    result = _recover(request, roots, handles, journal, record, verify, deadline)
                if command.action == "CLOSE" and terminal != "CLOSED":
                    terminal = "CLOSED"; record["dispatch"]["terminal"] = terminal
                    verify(); journal.write(record)
                return DispatchedPackaging(command, terminal, result)
