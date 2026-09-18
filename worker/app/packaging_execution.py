"""Recoverable local packaging execution; callers still own authorization and jobs."""
from contextlib import contextmanager, ExitStack
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat
import time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.file_journal import FileJournal, JournalError
from app.inventory import _safe_name
from app.packaging_assembly import PackagingAssemblyError, assemble_packages
from app.packaging_convert import PackagingConversionError
from app.packaging_lease import PackagingLeaseError, open_execution_lease
from app.packaging_plan import PackagingPlanError, _digest, _hash, build_packaging_plan
from app.packaging_stage import PackagingStageError, _identity, _private_root, _remove_owned, stage_packaging_inputs
from app.packaging_store import PackagingStoreError, StoredPackages, recover_packages, retain_packages
from app.packaging_zip import PackagingZipError
from app.preflight import ZipPolicy
from app.secure_filesystem import _directory_flags, secure_filesystem_access_supported

MAX_ATTEMPTS = 32


class PackagingExecutionError(RuntimeError):
    """Fixed codes only; no paths, report contents or process diagnostics."""


def _check(condition, code="PACKAGING_EXECUTION_INVALID"):
    if not condition: raise PackagingExecutionError(code)


@dataclass(frozen=True)
class ExecutionLimits:
    seconds: int = 1800
    staged_bytes: int = 8 * 1024**3
    generated_bytes: int = 16 * 1024**3
    retained_bytes: int = 16 * 1024**3


@dataclass(frozen=True)
class PackagingRequest:
    operation_id: UUID
    parts: tuple[str, ...]
    source_revision_hash: str
    technical_report_hash: str
    approval_context_hash: str
    policy: str
    storage_timezone: str
    plan_hash: str
    limits: ExecutionLimits = ExecutionLimits()

    def document(self):
        return {"version": 1, "operation_id": str(self.operation_id), "parts": list(self.parts),
            "source_revision_hash": self.source_revision_hash, "technical_report_hash": self.technical_report_hash,
            "approval_context_hash": self.approval_context_hash, "policy": self.policy,
            "storage_timezone": self.storage_timezone, "plan_hash": self.plan_hash, "limits": asdict(self.limits)}

    @property
    def sha256(self): return _digest(self.document())


@dataclass(frozen=True)
class ExecutionRoots:
    materials: Path
    workspace: Path
    artifacts: Path
    journal: Path


@dataclass(frozen=True)
class ExecutionResult:
    operation_id: UUID
    request_hash: str
    status: str
    attempt: int
    stored: StoredPackages | None


def _validate_request(request):
    _check(isinstance(request, PackagingRequest) and isinstance(request.operation_id, UUID) and request.operation_id.version == 4
        and isinstance(request.parts, tuple) and 1 <= len(request.parts) <= 16
        and all(isinstance(part, str) and _safe_name(part) for part in request.parts))
    _check(all(_hash(value) for value in (request.source_revision_hash, request.technical_report_hash,
        request.approval_context_hash, request.plan_hash)))
    _check(request.policy in {item.value for item in ZipPolicy}
        and isinstance(request.storage_timezone, str) and 0 < len(request.storage_timezone) <= 100)
    try: ZoneInfo(request.storage_timezone)
    except (ValueError, ZoneInfoNotFoundError): raise PackagingExecutionError("PACKAGING_EXECUTION_TIMEZONE_INVALID") from None
    limits = request.limits
    _check(isinstance(limits, ExecutionLimits) and type(limits.seconds) is int and 1 <= limits.seconds <= 3600
        and type(limits.staged_bytes) is int and 0 < limits.staged_bytes <= 256 * 1024**3
        and all(type(value) is int and 0 < value <= 128 * 1024**3 for value in (limits.generated_bytes, limits.retained_bytes)))


def prepare_packaging_request(report: dict, *, operation_id: UUID, parts: tuple[str, ...],
    expected_source_revision_hash: str, expected_technical_report_hash: str, approval_context_hash: str,
    policy: str, storage_timezone: str, limits: ExecutionLimits = ExecutionLimits()) -> PackagingRequest:
    """Pure preparation, with independently approved digests; no authorization implied."""
    try:
        _check(isinstance(report, dict))
        # Backend stores the report and its inventory separately.
        technical = {key: value for key, value in report.items() if key != "inventory"}
        json.dumps(technical, allow_nan=False)
        _check(_digest(technical) == expected_technical_report_hash, "PACKAGING_EXECUTION_REPORT_CHANGED")
        plan = build_packaging_plan(report, expected_source_revision_hash=expected_source_revision_hash, policy=policy)
        request = PackagingRequest(operation_id, parts, expected_source_revision_hash, expected_technical_report_hash,
            approval_context_hash, policy, storage_timezone, plan.sha256, limits)
        _validate_request(request)
        _check(parts[-1] == plan.identity, "PACKAGING_EXECUTION_IDENTITY_MISMATCH")
        return request
    except PackagingExecutionError: raise
    except (PackagingPlanError, ValueError, TypeError, KeyError, RecursionError):
        raise PackagingExecutionError("PACKAGING_EXECUTION_INPUT_INVALID") from None


def _id(value, length=2):
    return isinstance(value, list) and len(value) == length and all(type(number) is int and number >= 0 for number in value)


def _private(fd):
    info = os.fstat(fd)
    _check(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
        "PACKAGING_EXECUTION_PRIVATE_ROOT_REQUIRED")
    return list(_identity(info))


@contextmanager
def _roots(roots):
    _check(secure_filesystem_access_supported(), "PACKAGING_EXECUTION_PLATFORM_UNSUPPORTED")
    _check(isinstance(roots, ExecutionRoots))
    paths = (roots.materials, roots.workspace, roots.artifacts, roots.journal)
    for path in paths:
        _check(isinstance(path, Path) and path.is_absolute() and len(path.parts) > 1
            and all(_safe_name(part) for part in path.parts[1:]))
    for index, path in enumerate(paths):
        for other in paths[index + 1:]:
            _check(not path.is_relative_to(other) and not other.is_relative_to(path), "PACKAGING_EXECUTION_ROOT_OVERLAP")
    # Recovery never opens/stat/resolves the NAS. Private roots use no-follow
    # descriptor walks, so an aliased/linking private root is refused.
    with ExitStack() as cleanup:
        handles = {key: cleanup.enter_context(_private_root(getattr(roots, key))) for key in ("workspace", "artifacts", "journal")}
        identities = {key: _private(fd) for key, fd in handles.items()}
        _check(len({tuple(value) for value in identities.values()}) == 3, "PACKAGING_EXECUTION_ROOT_OVERLAP")
        binding = {"paths_hash": _digest([str(path) for path in paths]), "identities": identities}
        def verify():
            for key, fd in handles.items():
                _check(_private(fd) == identities[key], "PACKAGING_EXECUTION_ROOT_CHANGED")
                with _private_root(getattr(roots, key)) as named:
                    _check(_private(named) == identities[key], "PACKAGING_EXECUTION_ROOT_CHANGED")
        verify()
        yield handles, binding, verify
        verify()


def _name(request, number):
    return "execution-" + str(request.operation_id) + "-" + str(number).zfill(2)


def _record(value, request, binding, directory_identity):
    corrupt = "PACKAGING_EXECUTION_CORRUPT_STATE"
    _check(isinstance(value, dict), corrupt)
    version = value.get("version")
    _check(type(version) is int and version in {1, 2}, corrupt)
    fields = {"version", "kind", "request", "request_hash", "roots",
        "directory_identity", "lease_identity", "status", "attempts", "result"}
    _check(set(value) == fields | ({"dispatch"} if version == 2 else set()) and value["kind"] == "PBR_PACKAGING_EXECUTION", corrupt)
    if version == 2:
        from app.packaging_dispatch import validate_record_dispatch
        validate_record_dispatch(value)
    _check(value["request"] == request.document() and value["request_hash"] == request.sha256
        and _digest(value["request"]) == request.sha256, "PACKAGING_EXECUTION_REQUEST_CONFLICT")
    _check(value["roots"] == binding and value["directory_identity"] == directory_identity, "PACKAGING_EXECUTION_ROOT_CHANGED")
    _check(_id(value["lease_identity"], 3) and value["status"] in {"RESERVED", "WORKING", "RETAINED", "READY", "RETRY_REQUIRED"}, corrupt)
    attempts = value["attempts"]
    _check(isinstance(attempts, list) and 1 <= len(attempts) <= MAX_ATTEMPTS, corrupt)
    for number, item in enumerate(attempts, 1):
        _check(isinstance(item, dict) and set(item) == {"number", "workspace", "identity", "cleaned"}
            and type(item["number"]) is int and item["number"] == number and item["workspace"] == _name(request, number)
            and (item["identity"] is None or _id(item["identity"])) and type(item["cleaned"]) is bool, corrupt)
        if number < len(attempts): _check(item["cleaned"], corrupt)
    active = attempts[-1]
    if value["status"] == "RESERVED": _check(active["identity"] is None and not active["cleaned"], corrupt)
    elif value["status"] in {"WORKING", "RETAINED"}: _check(active["identity"] is not None and not active["cleaned"], corrupt)
    else: _check(active["cleaned"], corrupt)
    result = value["result"]
    if value["status"] in {"RETAINED", "READY"}:
        _check(isinstance(result, dict) and set(result) == {"proof_sha256", "retention_attempt"}
            and _hash(result["proof_sha256"]) and type(result["retention_attempt"]) is int
            and 1 <= result["retention_attempt"] <= MAX_ATTEMPTS, corrupt)
    else: _check(result is None, corrupt)
    return value


def _attempt(request, number):
    return {"number": number, "workspace": _name(request, number), "identity": None, "cleaned": False}


@contextmanager
def _operation(request, handles, binding, verify_roots, *, create):
    parent = handles["journal"]; name = str(request.operation_id); created = False
    if create:
        try: os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent); created = True
        except FileExistsError: pass
    try: fd = os.open(name, _directory_flags(), dir_fd=parent)
    except FileNotFoundError: raise PackagingExecutionError("PACKAGING_EXECUTION_NOT_FOUND") from None
    try:
        identity = _private(fd); journal = FileJournal(fd)
        def verify():
            verify_roots()
            _check(_private(fd) == identity and _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == tuple(identity),
                "PACKAGING_EXECUTION_ROOT_CHANGED")
            with os.scandir(fd) as items:
                for item in items:
                    _check(item.name in {"execution.lock", "state.json", "state.pending"}, "PACKAGING_EXECUTION_UNKNOWN_FILE")
        verify()
        record = journal.read()
        if not created:
            _check(record is not None, "PACKAGING_EXECUTION_UNKNOWN_OPERATION")
            _record(record, request, binding, identity)
        with open_execution_lease(fd, create=created, expected_identity=None if created else tuple(record["lease_identity"])) as lease:
            verify()
            if created:
                _check(record is None, "PACKAGING_EXECUTION_UNKNOWN_OPERATION")
                record = {"version": 1, "kind": "PBR_PACKAGING_EXECUTION", "request": request.document(), "request_hash": request.sha256,
                    "roots": binding, "directory_identity": identity, "lease_identity": list(lease.identity), "status": "RESERVED",
                    "attempts": [_attempt(request, 1)], "result": None}
                journal.write(record)
            else:
                record = _record(journal.read(), request, binding, identity)
                _check(record["lease_identity"] == list(lease.identity), "PACKAGING_EXECUTION_CORRUPT_STATE")
            yield journal, record, created, verify
            verify()
    finally: os.close(fd)


def _exists(fd, name):
    try: return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError: return None


def _check_old_workspaces(handles, record):
    for item in record["attempts"]:
        if item["cleaned"]:
            _check(_exists(handles["workspace"], item["workspace"]) is None, "PACKAGING_EXECUTION_WORKSPACE_CHANGED")


def _cleanup(request, handles, record, verify):
    verify(); _check_old_workspaces(handles, record)
    item = record["attempts"][-1]; parent = handles["workspace"]
    found = _exists(parent, item["workspace"])
    if item["cleaned"]: return
    if found is not None:
        _check(item["identity"] is not None and _identity(found) == tuple(item["identity"])
            and stat.S_ISDIR(found.st_mode), "PACKAGING_EXECUTION_RECOVERY_REQUIRED")
        fd = os.open(item["workspace"], _directory_flags(), dir_fd=parent)
        try:
            _check(_private(fd) == item["identity"], "PACKAGING_EXECUTION_WORKSPACE_CHANGED")
            allowed = {"packaging-" + str(request.operation_id), "artifacts-" + str(request.operation_id)}
            with os.scandir(fd) as children:
                for child in children:
                    _check(child.name in allowed, "PACKAGING_EXECUTION_UNKNOWN_WORKSPACE_FILE")
        finally: os.close(fd)
        _remove_owned(parent, item["workspace"], tuple(item["identity"])); os.fsync(parent)
    item["cleaned"] = True
    verify()


class _Deadline:
    def __init__(self, seconds): self.until = time.monotonic() + seconds
    def remaining(self, maximum=3600):
        value = self.until - time.monotonic()
        _check(value > 0, "PACKAGING_EXECUTION_TIME_LIMIT")
        return min(maximum, value)


def _recover(request, roots, handles, journal, record, verify, deadline):
    verify(); _check_old_workspaces(handles, record)
    stored = None
    try:
        stored = recover_packages(artifact_root=roots.artifacts, operation_id=request.operation_id,
            request_hash=request.sha256, plan_hash=request.plan_hash, max_seconds=deadline.remaining())
    except PackagingStoreError as error:
        if str(error) not in {"PACKAGING_STORE_NOT_FOUND", "PACKAGING_STORE_INCOMPLETE"}: raise
        _check(record["status"] not in {"RETAINED", "READY"}, "PACKAGING_EXECUTION_RESULT_MISSING")
    if stored is not None:
        result = {"proof_sha256": stored.proof_sha256, "retention_attempt": stored.attempt}
        _check(record["result"] is None or record["result"] == result, "PACKAGING_EXECUTION_RESULT_CHANGED")
        if record["status"] != "READY":
            _check(record["status"] in {"WORKING", "RETAINED"}, "PACKAGING_EXECUTION_UNEXPECTED_RESULT")
            record["result"] = result; record["status"] = "RETAINED"; verify(); journal.write(record)
    _cleanup(request, handles, record, verify)
    record["status"] = "READY" if stored is not None else "RETRY_REQUIRED"
    verify(); journal.write(record)
    return ExecutionResult(request.operation_id, request.sha256, record["status"], len(record["attempts"]), stored)


@contextmanager
def _errors():
    try: yield
    except PackagingExecutionError: raise
    except (PackagingLeaseError, PackagingStageError, PackagingAssemblyError, PackagingConversionError,
            PackagingStoreError, PackagingZipError) as error:
        code = str(error)
        raise PackagingExecutionError(code if re.fullmatch(r"PACKAGING_[A-Z0-9_]{1,100}", code) else "PACKAGING_EXECUTION_FAILED") from None
    except (JournalError, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise PackagingExecutionError("PACKAGING_EXECUTION_FAILED") from None


def reconcile_packaging(request: PackagingRequest, *, roots: ExecutionRoots) -> ExecutionResult:
    """Verify retained results and clean recorded work; never copy/convert NAS input."""
    with _errors():
        _validate_request(request); deadline = _Deadline(request.limits.seconds)
        with _roots(roots) as (handles, binding, verify_roots):
            with _operation(request, handles, binding, verify_roots, create=False) as (journal, record, _, verify):
                _check(record["version"] == 1, "PACKAGING_DISPATCH_REQUIRED")
                return _recover(request, roots, handles, journal, record, verify, deadline)


def execute_packaging(request: PackagingRequest, report: dict, *, roots: ExecutionRoots, retry: bool = False) -> ExecutionResult:
    """Caller must reserve material ownership and freshly authorize each new attempt.

    Exact replay reconciles existing work only. Incomplete work requires retry=True
    after the caller's fresh approval/account checks. No HTTP or DB gate is implied.
    """
    with _errors():
        _validate_request(request); _check(type(retry) is bool)
        current = prepare_packaging_request(report, operation_id=request.operation_id, parts=request.parts,
            expected_source_revision_hash=request.source_revision_hash, expected_technical_report_hash=request.technical_report_hash,
            approval_context_hash=request.approval_context_hash, policy=request.policy, storage_timezone=request.storage_timezone, limits=request.limits)
        _check(current == request, "PACKAGING_EXECUTION_PLAN_CHANGED")
        deadline = _Deadline(request.limits.seconds)
        with _roots(roots) as (handles, binding, verify_roots):
            with _operation(request, handles, binding, verify_roots, create=True) as (journal, record, created, verify):
                _check(record["version"] == 1, "PACKAGING_DISPATCH_REQUIRED")
                return _execute_locked(request, report, roots, handles, journal, record, created, verify, deadline, retry=retry)


def _execute_locked(request, report, roots, handles, journal, record, created, verify, deadline, *, retry):
    if created:
        _check(_exists(handles["artifacts"], str(request.operation_id)) is None, "PACKAGING_EXECUTION_UNEXPECTED_RESULT")
    if not created:
        result = _recover(request, roots, handles, journal, record, verify, deadline)
        if result.status == "READY" or not retry: return result
        _check(len(record["attempts"]) < MAX_ATTEMPTS, "PACKAGING_EXECUTION_ATTEMPT_LIMIT")
        record["attempts"].append(_attempt(request, len(record["attempts"]) + 1))
        record["status"] = "RESERVED"; verify(); journal.write(record)
    item = record["attempts"][-1]
    verify(); _check_old_workspaces(handles, record)
    _check(_exists(handles["workspace"], item["workspace"]) is None, "PACKAGING_EXECUTION_WORKSPACE_EXISTS")
    os.mkdir(item["workspace"], 0o700, dir_fd=handles["workspace"]); os.fsync(handles["workspace"])
    fd = os.open(item["workspace"], _directory_flags(), dir_fd=handles["workspace"])
    try: item["identity"] = _private(fd)
    finally: os.close(fd)
    record["status"] = "WORKING"; verify(); journal.write(record)
    workspace = roots.workspace / item["workspace"]
    with stage_packaging_inputs(roots.materials, request.parts, report, expected_source_revision_hash=request.source_revision_hash,
            policy=request.policy, workspace_root=workspace, operation_id=request.operation_id,
            max_seconds=deadline.remaining(600), max_bytes=request.limits.staged_bytes) as inputs:
        verify()
        with assemble_packages(inputs, workspace_root=workspace, storage_timezone=request.storage_timezone,
                max_seconds=deadline.remaining(), max_bytes=request.limits.generated_bytes) as bundle:
            verify()
            stored = retain_packages(bundle, artifact_root=roots.artifacts, request_hash=request.sha256,
                max_seconds=deadline.remaining(), max_bytes=request.limits.retained_bytes)
            record["result"] = {"proof_sha256": stored.proof_sha256, "retention_attempt": stored.attempt}
            record["status"] = "RETAINED"; verify(); journal.write(record)
    _cleanup(request, handles, record, verify)
    record["status"] = "READY"; verify(); journal.write(record)
    return ExecutionResult(request.operation_id, request.sha256, "READY", len(record["attempts"]), stored)
