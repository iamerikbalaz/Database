"""Explicit, proof-bound retirement of retained copies; never source files.

This internal primitive needs a durable authorized caller holding execution
ownership. The opt-in private service is not application-user authorization.
"""
from contextlib import contextmanager
import json
import os
import stat
from uuid import UUID

from app.file_journal import JournalError
from app.inventory import _signature
from app import packaging_store as storage
from app.packaging_stage import PackagingStageError, _descendant, _identity
from app.secure_filesystem import _metadata_flags


def _check(condition, code="PACKAGING_RETIREMENT_INVALID"):
    if not condition: raise storage.PackagingStoreError(code)


def _id(value, length=2):
    return isinstance(value, list) and len(value) == length and all(type(item) is int and item >= 0 for item in value)


def _uuid(value):
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (ValueError, TypeError, AttributeError): return False


def request_hash(operation_id, request_hash, plan_hash, proof_sha256, retirement_id):
    return storage._digest({"schema_version": 1, "operation_id": str(operation_id),
        "request_hash": request_hash, "plan_hash": plan_hash, "proof_sha256": proof_sha256,
        "retirement_id": str(retirement_id)})


def _receipt(record):
    retained, retirement = record["retained"], record["retirement"]
    return {"schema_version": 1, "status": "REMOVED", "operation_id": retained["operation_id"],
        "request_hash": retained["request_hash"], "plan_hash": retained["plan_hash"],
        "proof_sha256": retained["proof_sha256"], "retirement_id": retirement["id"],
        "retirement_request_hash": retirement["request_hash"],
        "file_count": len(retained["payload"]["files"]),
        "byte_count": sum(item["size"] for item in retained["payload"]["files"])}


def validate_record(value, operation_id, request_hash_value, plan_hash):
    """Strict version-2 tombstone; its original version-1 proof stays immutable."""
    corrupt = "PACKAGING_RETIREMENT_CORRUPT_STATE"
    _check(isinstance(value, dict) and set(value) == {"version", "kind", "status", "retained", "retirement", "receipt"}
        and type(value["version"]) is int and value["version"] == 2 and value["kind"] == "PBR_PACKAGED_RETIREMENT"
        and value["status"] in {"REMOVING", "REMOVED"}, corrupt)
    retained = value["retained"]
    _check(isinstance(retained, dict) and type(retained.get("version")) is int and retained["version"] == 1, corrupt)
    storage._record(retained, operation_id, request_hash_value, plan_hash)
    _check(retained["status"] == "READY", corrupt)
    retirement = value["retirement"]
    _check(isinstance(retirement, dict) and set(retirement) == {"id", "request_hash", "root_identity", "operation_identity", "files", "directories"}
        and _uuid(retirement["id"]) and _id(retirement["root_identity"]) and _id(retirement["operation_identity"]), corrupt)
    expected = request_hash(operation_id, request_hash_value, plan_hash, retained["proof_sha256"], retirement["id"])
    _check(retirement["request_hash"] == expected, corrupt)
    files, directories = retirement["files"], retirement["directories"]
    proof = retained["payload"]
    _check(isinstance(files, list) and len(files) == len(proof["files"])
        and isinstance(directories, list) and len(directories) == len(proof["directories"]), corrupt)
    for item, original in zip(files, proof["files"], strict=True):
        _check(isinstance(item, dict) and set(item) == {"path", "signature"}
            and item["path"] == original["path"] and _id(item["signature"], 7), corrupt)
        signature = item["signature"]
        _check(signature[0] == retained["directory_identity"][0] and stat.S_ISREG(signature[2])
            and stat.S_IMODE(signature[2]) == 0o400 and signature[3] == 1 and signature[4] == original["size"], corrupt)
    for item, path in zip(directories, proof["directories"], strict=True):
        _check(isinstance(item, dict) and set(item) == {"path", "identity"} and item["path"] == path
            and _id(item["identity"]) and item["identity"][0] == retained["directory_identity"][0], corrupt)
    expected_receipt = _receipt(value) if value["status"] == "REMOVED" else None
    _check(value["receipt"] == expected_receipt and storage._digest(value["receipt"]) == storage._digest(expected_receipt), corrupt)
    return value


def reject_retired_packages(*, artifact_root, operation_id, request_hash_value, plan_hash, expected_root_identity):
    """Fence a delayed dispatch before it can rewrite execution history.

    The caller holds execution ownership, so no retirement can begin between
    this check and its command journal write. Do not open or rehash artifact bytes.
    """
    try:
        with storage._operation(artifact_root, operation_id, create=False,
                expected_root_identity=expected_root_identity) as (journal, _, verify):
            record = journal.read()
            if record is not None: storage._record(record, operation_id, request_hash_value, plan_hash)
            verify()
    except storage.PackagingStoreError as error:
        if str(error) != "PACKAGING_STORE_NOT_FOUND": raise


@contextmanager
def _errors():
    try: yield
    except storage.PackagingStoreError: raise
    except (PackagingStageError, JournalError, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise storage.PackagingStoreError("PACKAGING_RETIREMENT_FAILED") from None


def _file(root, item, deadline, *, expected=None):
    parts = tuple(item["path"].split("/"))
    with _descendant(root, parts[:-1]) as parent:
        fd = os.open(parts[-1], _metadata_flags(), dir_fd=parent)
        try:
            before = os.fstat(fd)
            if expected is not None:
                _check(list(_signature(before)) == expected, "PACKAGING_RETIREMENT_FILE_CHANGED")
            storage._verify_file(fd, item, deadline)
            _check(_signature(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)) == _signature(before),
                "PACKAGING_RETIREMENT_FILE_CHANGED")
            return list(_signature(before))
        finally: os.close(fd)


def _directory_identity(root, path):
    with _descendant(root, tuple(path.rstrip("/").split("/"))) as fd:
        info = os.fstat(fd)
        _check(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "PACKAGING_RETIREMENT_DIRECTORY_CHANGED")
        return list(_identity(info))


def _snapshot(journal, retained, deadline, verify):
    proof = retained["payload"]
    with storage._directory(journal, "ready", retained["directory_identity"]) as root:
        actual = storage._tree(root, deadline)
        expected = {item["path"] for item in proof["files"]} | set(proof["directories"])
        _check(actual == expected, "PACKAGING_RETIREMENT_TREE_CHANGED")
        files = []
        for item in proof["files"]:
            verify(); deadline.check()
            files.append({"path": item["path"], "signature": _file(root, item, deadline)})
        directories = []
        for path in proof["directories"]:
            verify(); deadline.check()
            directories.append({"path": path, "identity": _directory_identity(root, path)})
        return files, directories


def _remaining(root, record, deadline, verify):
    proof, retirement = record["retained"]["payload"], record["retirement"]
    actual = storage._tree(root, deadline)
    expected = {item["path"] for item in proof["files"]} | set(proof["directories"])
    _check(actual <= expected, "PACKAGING_RETIREMENT_TREE_CHANGED")
    for item in retirement["directories"]:
        if item["path"] not in actual: continue
        verify(); deadline.check()
        _check(_directory_identity(root, item["path"]) == item["identity"], "PACKAGING_RETIREMENT_DIRECTORY_CHANGED")
    for item, saved in zip(proof["files"], retirement["files"], strict=True):
        if item["path"] not in actual: continue
        verify(); deadline.check(); _file(root, item, deadline, expected=saved["signature"])
    return actual


def _remove(journal, record, deadline, verify):
    retained, retirement = record["retained"], record["retirement"]
    if storage._exists(journal.fd, "ready") is None: return
    _check(record["status"] == "REMOVING", "PACKAGING_RETIREMENT_TREE_CHANGED")
    with storage._directory(journal, "ready", retained["directory_identity"]) as root:
        actual = _remaining(root, record, deadline, verify)
        for item in retirement["files"]:
            if item["path"] not in actual: continue
            verify(); deadline.check(); parts = tuple(item["path"].split("/"))
            with _descendant(root, parts[:-1]) as parent:
                info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                _check(list(_signature(info)) == item["signature"], "PACKAGING_RETIREMENT_FILE_CHANGED")
                os.unlink(parts[-1], dir_fd=parent); os.fsync(parent)
        for item in sorted(retirement["directories"], key=lambda item: item["path"].count("/"), reverse=True):
            if item["path"] not in actual: continue
            verify(); deadline.check(); parts = tuple(item["path"].rstrip("/").split("/"))
            with _descendant(root, parts[:-1]) as parent:
                info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                _check(stat.S_ISDIR(info.st_mode) and list(_identity(info)) == item["identity"],
                    "PACKAGING_RETIREMENT_DIRECTORY_CHANGED")
                os.rmdir(parts[-1], dir_fd=parent); os.fsync(parent)
    verify(); deadline.check()
    info = os.stat("ready", dir_fd=journal.fd, follow_symlinks=False)
    _check(stat.S_ISDIR(info.st_mode) and list(_identity(info)) == retained["directory_identity"],
        "PACKAGING_RETIREMENT_DIRECTORY_CHANGED")
    os.rmdir("ready", dir_fd=journal.fd); os.fsync(journal.fd)


def retire_packages(*, artifact_root, operation_id: UUID, request_hash_value: str, plan_hash: str,
    expected_proof_sha256: str, retirement_id: UUID, expected_root_identity: tuple[int, int], max_seconds: float = 120) -> dict:
    """Remove an exact accepted copy once, preserving durable intent and proof.

    Execution ownership and current application authorization are caller duties.
    This never reads NAS and cannot clean incomplete or unknown operations. A
    replay must use the same retirement ID and all original proof bindings.
    """
    with _errors():
        _check(isinstance(operation_id, UUID) and operation_id.version == 4
            and isinstance(retirement_id, UUID) and retirement_id.version == 4
            and all(storage._hash(item) for item in (request_hash_value, plan_hash, expected_proof_sha256))
            and isinstance(expected_root_identity, tuple) and _id(list(expected_root_identity))
            and type(max_seconds) in {int, float} and 0 < max_seconds <= 120)
        deadline = storage._Deadline(max_seconds)
        digest = request_hash(operation_id, request_hash_value, plan_hash, expected_proof_sha256, retirement_id)
        with storage._operation(artifact_root, operation_id, create=False,
                expected_root_identity=expected_root_identity) as (journal, _, verify_operation):
            record = journal.read(); _check(record is not None, "PACKAGING_RETIREMENT_UNKNOWN_OPERATION")
            if record.get("version") == 2:
                validate_record(record, operation_id, request_hash_value, plan_hash)
                _check(record["retirement"]["id"] == str(retirement_id) and record["retirement"]["request_hash"] == digest,
                    "PACKAGING_RETIREMENT_REQUEST_CONFLICT")
            else:
                retained = storage._record(record, operation_id, request_hash_value, plan_hash)
                _check(retained["status"] == "READY", "PACKAGING_RETIREMENT_NOT_READY")
                _check(retained["proof_sha256"] == expected_proof_sha256, "PACKAGING_RETIREMENT_PROOF_MISMATCH")
                _check(storage._exists(journal.fd, "incoming") is None, "PACKAGING_RETIREMENT_TREE_CHANGED")
                files, directories = _snapshot(journal, retained, deadline, verify_operation)
                record = {"version": 2, "kind": "PBR_PACKAGED_RETIREMENT", "status": "REMOVING",
                    "retained": retained, "retirement": {"id": str(retirement_id), "request_hash": digest,
                        "root_identity": list(expected_root_identity), "operation_identity": list(_identity(os.fstat(journal.fd))),
                        "files": files, "directories": directories}, "receipt": None}
                validate_record(record, operation_id, request_hash_value, plan_hash)
                verify_operation(); deadline.check(); journal.write(record)

            def verify():
                verify_operation()
                _check(record["retirement"]["root_identity"] == list(expected_root_identity)
                    and record["retirement"]["operation_identity"] == list(_identity(os.fstat(journal.fd))),
                    "PACKAGING_RETIREMENT_ROOT_CHANGED")
                _check(storage._exists(journal.fd, "incoming") is None, "PACKAGING_RETIREMENT_TREE_CHANGED")
                if record["status"] == "REMOVED":
                    _check(storage._exists(journal.fd, "ready") is None, "PACKAGING_RETIREMENT_TREE_CHANGED")
            verify()
            if record["status"] != "REMOVED":
                _remove(journal, record, deadline, verify)
                record["status"] = "REMOVED"; record["receipt"] = _receipt(record)
                verify(); deadline.check(); journal.write(record)
            verify()
            return json.loads(json.dumps(record["receipt"]))
