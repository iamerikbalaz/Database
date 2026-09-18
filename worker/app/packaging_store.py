"""Durable, idempotent retention of verified local packaging results."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from uuid import UUID
import zipfile
import zlib

from app.file_journal import FileJournal, JournalError, MAX_STATE_BYTES, rename_noreplace
from app.inventory import _signature
from app.packaging_assembly import PreparedPackages
from app.packaging_plan import _digest, _relative
from app.packaging_stage import PackagingStageError, _descendant, _identity, _mkdir, _private_root, _remove_owned
from app.secure_filesystem import _directory_flags, _metadata_flags, secure_filesystem_access_supported


class PackagingStoreError(RuntimeError):
    """Fixed codes, no source contents or local paths."""


def _check(condition, code="PACKAGING_STORE_INVALID"):
    if not condition: raise PackagingStoreError(code)


def _hash(value): return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


class _Deadline:
    def __init__(self, seconds):
        _check(type(seconds) in {int, float} and 0 < seconds <= 3600)
        self.until = time.monotonic() + seconds
    def check(self): _check(time.monotonic() < self.until, "PACKAGING_STORE_TIME_LIMIT")
    def remaining(self): self.check(); return self.until - time.monotonic()


def _payload(bundle):
    files = [{"path": item.name, "size": item.size, "sha256": item.sha256} for item in bundle._files]
    files.extend({"path": item.path, "size": item.size, "sha256": item.sha256} for item in bundle.plan.previews)
    return {"schema_version": 1, "bundle": bundle.proof_document(), "bundle_sha256": bundle.proof_sha256,
        "files": sorted(files, key=lambda item: item["path"]), "directories": sorted(bundle.plan.preview_directories)}


def _validate_payload(value, operation_id, plan_hash):
    _check(isinstance(value, dict) and set(value) == {"schema_version", "bundle", "bundle_sha256", "files", "directories"}
        and value["schema_version"] == 1 and isinstance(value["bundle"], dict)
        and value["bundle"].get("operation_id") == str(operation_id) and value["bundle"].get("plan_sha256") == plan_hash
        and _hash(value["bundle_sha256"]) and _digest(value["bundle"]) == value["bundle_sha256"], "PACKAGING_STORE_CORRUPT_STATE")
    files, directories = value["files"], value["directories"]
    _check(isinstance(files, list) and 2 <= len(files) <= 20008 and isinstance(directories, list) and len(directories) <= 20000,
        "PACKAGING_STORE_CORRUPT_STATE")
    seen = set(); names = set(); total = 0
    for item in files:
        _check(isinstance(item, dict) and set(item) == {"path", "size", "sha256"}
            and _relative(item["path"]) and not item["path"].endswith("/")
            and type(item["size"]) is int and 0 <= item["size"] <= 16 * 1024**3 and _hash(item["sha256"]), "PACKAGING_STORE_CORRUPT_STATE")
        path = item["path"]; _check(path.casefold() not in seen, "PACKAGING_STORE_CORRUPT_STATE")
        seen.add(path.casefold()); names.add(path); total += item["size"]
        _check(path == "metadata.json" or ("/" not in path and path.endswith(".zip")) or path.startswith("PREVIEW/"), "PACKAGING_STORE_CORRUPT_STATE")
    _check(total <= 128 * 1024**3 and "metadata.json" in names, "PACKAGING_STORE_CORRUPT_STATE")
    for path in directories:
        _check(isinstance(path, str) and path.endswith("/") and _relative(path[:-1])
            and (path == "PREVIEW/" or path.startswith("PREVIEW/")) and path[:-1].casefold() not in seen,
            "PACKAGING_STORE_CORRUPT_STATE")
        seen.add(path[:-1].casefold()); names.add(path)
    for path in names:
        parts = path.removesuffix("/").split("/"); _check(len(parts) <= 16, "PACKAGING_STORE_CORRUPT_STATE")
        _check(len(parts) == 1 or "/".join(parts[:-1]) + "/" in names, "PACKAGING_STORE_CORRUPT_STATE")
    by_path = {item["path"]: item for item in files}; archives = value["bundle"].get("archives")
    _check(isinstance(archives, (list, tuple)) and 1 <= len(archives) <= 6, "PACKAGING_STORE_CORRUPT_STATE")
    _check(by_path["metadata.json"]["size"] <= 65536 and by_path["metadata.json"]["sha256"] == value["bundle"].get("manifest_sha256"),
        "PACKAGING_STORE_CORRUPT_STATE")
    required = {"metadata.json"}; required_directories = set()
    for archive in archives:
        _check(isinstance(archive, dict) and isinstance(archive.get("filename"), str)
            and archive["filename"].endswith(".zip") and "/" not in archive["filename"]
            and archive["filename"] not in required, "PACKAGING_STORE_CORRUPT_STATE")
        name = archive["filename"]; required.add(name)
        _check(by_path.get(name) == {"path": name, "size": archive.get("size"), "sha256": archive.get("sha256")}, "PACKAGING_STORE_CORRUPT_STATE")
    first = archives[0]; prefix = first["filename"][:-4] + "/"; entries = first.get("entries")
    _check(isinstance(entries, (list, tuple)) and len(entries) <= 20001, "PACKAGING_STORE_CORRUPT_STATE")
    for entry in entries:
        _check(isinstance(entry, dict) and isinstance(entry.get("path"), str), "PACKAGING_STORE_CORRUPT_STATE")
        if entry["path"].startswith(prefix + "PREVIEW/"):
            path = entry["path"][len(prefix):]
            if path.endswith("/"): required_directories.add(path)
            else:
                required.add(path)
                _check(by_path.get(path) == {"path": path, "size": entry.get("size"), "sha256": entry.get("sha256")}, "PACKAGING_STORE_CORRUPT_STATE")
    _check(set(by_path) == required and set(directories) == required_directories, "PACKAGING_STORE_CORRUPT_STATE")
    return total


def _record(value, operation_id, request_hash, plan_hash):
    _check(isinstance(value, dict) and set(value) == {"version", "kind", "operation_id", "request_hash", "plan_hash", "status", "attempt", "attempt_history", "directory_identity", "proof_sha256", "payload"}
        and value["version"] == 1 and value["kind"] == "PBR_PACKAGED_RESULT" and value["operation_id"] == str(operation_id), "PACKAGING_STORE_CORRUPT_STATE")
    _check(value["request_hash"] == request_hash and value["plan_hash"] == plan_hash, "PACKAGING_STORE_REQUEST_CONFLICT")
    _check(value["status"] in {"RESERVED", "BUILDING", "READY"} and type(value["attempt"]) is int and 1 <= value["attempt"] <= 32
        and _hash(value["proof_sha256"]) and _digest(value["payload"]) == value["proof_sha256"], "PACKAGING_STORE_CORRUPT_STATE")
    history = value["attempt_history"]
    _check(isinstance(history, list) and len(history) == value["attempt"] - 1, "PACKAGING_STORE_CORRUPT_STATE")
    for index, item in enumerate(history, 1):
        _check(isinstance(item, dict) and set(item) == {"attempt", "proof_sha256", "outcome"}
            and type(item["attempt"]) is int and item["attempt"] == index and _hash(item["proof_sha256"])
            and item["outcome"] == "INCOMPLETE", "PACKAGING_STORE_CORRUPT_STATE")
    identity = value["directory_identity"]
    _check(identity is None if value["status"] == "RESERVED" else isinstance(identity, list) and len(identity) == 2
        and all(type(number) is int and number >= 0 for number in identity), "PACKAGING_STORE_CORRUPT_STATE")
    _validate_payload(value["payload"], operation_id, plan_hash)
    return value


@contextmanager
def _operation(root, operation_id, *, create):
    _check(secure_filesystem_access_supported(), "PACKAGING_STORE_PLATFORM_UNSUPPORTED")
    _check(isinstance(root, Path) and isinstance(operation_id, UUID) and operation_id.version == 4)
    import fcntl
    with _private_root(root) as parent:
        name = str(operation_id); created = False
        if create:
            try: os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent); created = True
            except FileExistsError: pass
        try: fd = os.open(name, _directory_flags(), dir_fd=parent)
        except FileNotFoundError: raise PackagingStoreError("PACKAGING_STORE_NOT_FOUND") from None
        try:
            before = os.fstat(fd); _check(before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o700, "PACKAGING_STORE_UNSAFE")
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if created: flags |= os.O_CREAT | os.O_EXCL
            try: lock = os.open("operation.lock", flags, 0o600, dir_fd=fd)
            except FileNotFoundError: raise PackagingStoreError("PACKAGING_STORE_UNKNOWN_OPERATION") from None
            try:
                info = os.fstat(lock)
                _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) == 0o600, "PACKAGING_STORE_UNSAFE")
                try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: raise PackagingStoreError("PACKAGING_STORE_BUSY") from None
                yield FileJournal(fd), created
                _check(_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == _identity(before), "PACKAGING_STORE_CHANGED")
                with _private_root(root) as current:
                    _check(_identity(os.fstat(current)) == _identity(os.fstat(parent)), "PACKAGING_STORE_CHANGED")
            finally: os.close(lock)
        finally: os.close(fd)


def _exists(fd, name):
    try: return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError: return None


@contextmanager
def _directory(journal, name, expected):
    fd = os.open(name, _directory_flags(), dir_fd=journal.fd)
    try:
        info = os.fstat(fd)
        _check(_identity(info) == tuple(expected) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "PACKAGING_STORE_CHANGED")
        yield fd
        _check(_identity(os.stat(name, dir_fd=journal.fd, follow_symlinks=False)) == tuple(expected), "PACKAGING_STORE_CHANGED")
    finally: os.close(fd)


def _tree(fd, deadline, prefix="", *, seen=None, depth=0):
    seen = seen if seen is not None else set(); _check(depth <= 16, "PACKAGING_STORE_UNSAFE")
    with os.scandir(fd) as listing:
        for item in listing:
            deadline.check(); _check(len(seen) < 40016, "PACKAGING_STORE_UNSAFE")
            info = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
            path = prefix + item.name; _check(_relative(path), "PACKAGING_STORE_UNSAFE")
            if stat.S_ISDIR(info.st_mode):
                _check(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700 and info.st_dev == os.fstat(fd).st_dev,
                    "PACKAGING_STORE_UNSAFE")
                seen.add(path + "/")
                with _descendant(fd, (item.name,)) as child: _tree(child, deadline, path + "/", seen=seen, depth=depth + 1)
            else:
                _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) in {0o400, 0o600}, "PACKAGING_STORE_UNSAFE")
                seen.add(path)
    return seen


def _verify_file(fd, item, deadline, *, freeze=False):
    before = os.fstat(fd)
    _check(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
        and stat.S_IMODE(before.st_mode) in ({0o400, 0o600} if freeze else {0o400}), "PACKAGING_STORE_UNSAFE")
    if freeze and before.st_size < item["size"]: return False
    _check(before.st_size == item["size"], "PACKAGING_STORE_ARTIFACT_CHANGED")
    os.lseek(fd, 0, os.SEEK_SET); digest = hashlib.sha256(); total = 0
    while True:
        deadline.check(); chunk = os.read(fd, min(1024**2, item["size"] + 1 - total))
        if not chunk: break
        total += len(chunk); _check(total <= item["size"], "PACKAGING_STORE_ARTIFACT_CHANGED"); digest.update(chunk)
    _check(total == item["size"] and digest.hexdigest() == item["sha256"] and _signature(os.fstat(fd)) == _signature(before),
        "PACKAGING_STORE_ARTIFACT_CHANGED")
    if freeze: os.fchmod(fd, 0o400); os.fsync(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    return True


def _verify_directory(fd, payload, deadline, *, incomplete=False):
    actual = _tree(fd, deadline); expected = {item["path"] for item in payload["files"]} | set(payload["directories"])
    _check(actual <= expected, "PACKAGING_STORE_UNEXPECTED_FILE")
    complete = actual == expected
    for item in payload["files"]:
        if item["path"] not in actual: complete = False; continue
        parts = tuple(item["path"].split("/"))
        with _descendant(fd, parts[:-1]) as parent:
            file = os.open(parts[-1], _metadata_flags(), dir_fd=parent)
            try: complete = _verify_file(file, item, deadline, freeze=incomplete) and complete
            finally: os.close(file)
    if not incomplete: _check(complete, "PACKAGING_STORE_ARTIFACT_MISSING")
    return complete


def _sync_directories(fd, directories):
    for path in sorted(directories, key=lambda value: value.count("/"), reverse=True):
        with _descendant(fd, tuple(path.rstrip("/").split("/"))) as child: os.fsync(child)
    os.fsync(fd)


def _finish(journal, record, deadline):
    ready = _exists(journal.fd, "ready"); incoming = _exists(journal.fd, "incoming")
    _check(not (ready and incoming), "PACKAGING_STORE_RECOVERY_REQUIRED")
    if record["status"] == "RESERVED":
        _check(not ready and not incoming, "PACKAGING_STORE_RECOVERY_REQUIRED"); return False
    if ready:
        with _directory(journal, "ready", record["directory_identity"]) as directory:
            _verify_directory(directory, record["payload"], deadline)
        if record["status"] != "READY": record["status"] = "READY"; journal.write(record)
        return True
    _check(record["status"] != "READY", "PACKAGING_STORE_ARTIFACT_MISSING")
    if not incoming: return False
    with _directory(journal, "incoming", record["directory_identity"]) as directory:
        if not _verify_directory(directory, record["payload"], deadline, incomplete=True): return False
        _sync_directories(directory, record["payload"]["directories"])
    rename_noreplace(journal.fd, "incoming", journal.fd, "ready")
    record["status"] = "READY"; journal.write(record)
    return True


@contextmanager
def _source_files(bundle, deadline):
    first = bundle.archives[0]; root = bundle.plan.resolutions[0].archive_root
    with bundle.open_archive(first.filename, max_seconds=min(120, deadline.remaining())) as fd, os.fdopen(os.dup(fd), "rb") as stream, zipfile.ZipFile(stream) as archive:
        @contextmanager
        def open_source(path):
            if path.startswith("PREVIEW/"):
                with archive.open(root + "/" + path) as value: yield value
            else:
                provider = bundle.open_manifest(max_seconds=min(120, deadline.remaining())) if path == "metadata.json" else bundle.open_archive(path, max_seconds=min(120, deadline.remaining()))
                with provider as source, os.fdopen(os.dup(source), "rb") as value: yield value
        yield open_source


def _copy_file(directory, item, source, deadline):
    parts = tuple(item["path"].split("/"))
    with _descendant(directory, parts[:-1]) as parent:
        target = os.open(parts[-1], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            count = 0; digest = hashlib.sha256()
            while True:
                deadline.check(); chunk = source.read(min(1024**2, item["size"] + 1 - count))
                if not chunk: break
                count += len(chunk); _check(count <= item["size"], "PACKAGING_STORE_COPY_MISMATCH"); digest.update(chunk)
                remaining = memoryview(chunk)
                while remaining:
                    deadline.check(); written = os.write(target, remaining)
                    _check(written > 0, "PACKAGING_STORE_WRITE_FAILED"); remaining = remaining[written:]
            _check(count == item["size"] and digest.hexdigest() == item["sha256"], "PACKAGING_STORE_COPY_MISMATCH")
            os.fsync(target); _check(_verify_file(target, item, deadline, freeze=True), "PACKAGING_STORE_COPY_MISMATCH")
        finally: os.close(target)


@dataclass(frozen=True)
class StoredPackages:
    operation_id: UUID
    request_hash: str
    plan_hash: str
    proof_sha256: str
    attempt: int
    attempt_history: tuple[dict, ...]
    payload: dict


def _result(record):
    # Use JSON container types on the initial result as well as journal replays.
    # This also prevents a caller from mutating the live journal document.
    detached = json.loads(json.dumps(record, allow_nan=False))
    return StoredPackages(UUID(record["operation_id"]), record["request_hash"], record["plan_hash"], record["proof_sha256"], record["attempt"],
        tuple(detached["attempt_history"]), detached["payload"])


def retain_packages(bundle: PreparedPackages, *, artifact_root: Path, request_hash: str,
    max_seconds: float = 600, max_bytes: int = 16 * 1024**3) -> StoredPackages:
    """Retain only a complete live bundle; exact READY retries return original bytes."""
    _check(isinstance(bundle, PreparedPackages) and bundle._live[0] and isinstance(artifact_root, Path) and _hash(request_hash)
        and type(max_bytes) is int and 0 < max_bytes <= 128 * 1024**3)
    deadline = _Deadline(max_seconds)
    try:
        destination = artifact_root.resolve(strict=True)
        for source in (bundle._materials_root, bundle._workspace_root):
            path = source.resolve(strict=True)
            _check(not destination.is_relative_to(path) and not path.is_relative_to(destination), "PACKAGING_STORE_ROOT_OVERLAP")
        payload = _payload(bundle); total = _validate_payload(payload, bundle.operation_id, bundle.plan.sha256)
        _check(total <= max_bytes, "PACKAGING_STORE_SIZE_LIMIT")
        _check(len(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) + 8192 <= MAX_STATE_BYTES,
            "PACKAGING_STORE_PROOF_SIZE_LIMIT")
        deadline.check()
        with _operation(artifact_root, bundle.operation_id, create=True) as (journal, created):
            record = journal.read()
            if record is not None:
                _record(record, bundle.operation_id, request_hash, bundle.plan.sha256)
                if _finish(journal, record, deadline): return _result(record)
                _check(record["attempt"] < 32, "PACKAGING_STORE_ATTEMPT_LIMIT")
                if _exists(journal.fd, "incoming"):
                    _remove_owned(journal.fd, "incoming", tuple(record["directory_identity"])); os.fsync(journal.fd)
                attempt = record["attempt"] + 1
                history = [*record["attempt_history"], {"attempt": record["attempt"], "proof_sha256": record["proof_sha256"], "outcome": "INCOMPLETE"}]
            else:
                _check(created, "PACKAGING_STORE_UNKNOWN_OPERATION"); attempt = 1; history = []
            record = {"version": 1, "kind": "PBR_PACKAGED_RESULT", "operation_id": str(bundle.operation_id),
                "request_hash": request_hash, "plan_hash": bundle.plan.sha256, "status": "RESERVED", "attempt": attempt, "attempt_history": history,
                "directory_identity": None, "proof_sha256": _digest(payload), "payload": payload}
            journal.write(record)
            os.mkdir("incoming", 0o700, dir_fd=journal.fd); os.fsync(journal.fd)
            with _descendant(journal.fd, ("incoming",)) as directory:
                record["directory_identity"] = list(_identity(os.fstat(directory))); record["status"] = "BUILDING"; journal.write(record)
                for path in sorted(payload["directories"], key=lambda value: value.count("/")): _mkdir(directory, tuple(path.rstrip("/").split("/")))
                with _source_files(bundle, deadline) as open_source:
                    for item in payload["files"]:
                        with open_source(item["path"]) as source: _copy_file(directory, item, source, deadline)
                _sync_directories(directory, payload["directories"])
            _check(_finish(journal, record, deadline), "PACKAGING_STORE_INCOMPLETE")
            return _result(record)
    except PackagingStoreError: raise
    except (PackagingStageError, JournalError, OSError, ValueError, TypeError, KeyError, RecursionError, EOFError, zlib.error, zipfile.BadZipFile):
        raise PackagingStoreError("PACKAGING_STORE_FAILED") from None


def recover_packages(*, artifact_root: Path, operation_id: UUID, request_hash: str, plan_hash: str,
    max_seconds: float = 600) -> StoredPackages:
    """Reconcile complete retained bytes after restart, without reading NAS inputs."""
    _check(_hash(request_hash) and _hash(plan_hash)); deadline = _Deadline(max_seconds)
    try:
        with _operation(artifact_root, operation_id, create=False) as (journal, _):
            value = journal.read(); _check(value is not None, "PACKAGING_STORE_UNKNOWN_OPERATION")
            record = _record(value, operation_id, request_hash, plan_hash)
            _check(_finish(journal, record, deadline), "PACKAGING_STORE_INCOMPLETE")
            return _result(record)
    except PackagingStoreError: raise
    except (PackagingStageError, JournalError, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise PackagingStoreError("PACKAGING_STORE_FAILED") from None


@contextmanager
def open_retained_file(*, artifact_root: Path, operation_id: UUID, request_hash: str, plan_hash: str,
    expected_proof_sha256: str, path: str, max_seconds: float = 120,
    expected_size: int | None = None, expected_file_sha256: str | None = None):
    """Read one allowlisted retained artifact against an independently held proof."""
    _check(_hash(request_hash) and _hash(plan_hash) and _hash(expected_proof_sha256)); deadline = _Deadline(max_seconds)
    if expected_size is not None or expected_file_sha256 is not None:
        _check(type(expected_size) is int and 0 <= expected_size <= 16 * 1024**3 and _hash(expected_file_sha256))
    try:
        with _operation(artifact_root, operation_id, create=False) as (journal, _):
            value = journal.read(); _check(value is not None, "PACKAGING_STORE_UNKNOWN_OPERATION")
            record = _record(value, operation_id, request_hash, plan_hash)
            _check(record["status"] == "READY" and record["proof_sha256"] == expected_proof_sha256, "PACKAGING_STORE_PROOF_MISMATCH")
            item = next((item for item in record["payload"]["files"] if item["path"] == path), None)
            _check(item is not None, "PACKAGING_STORE_FILE_NOT_FOUND")
            if expected_size is not None:
                _check(item["size"] == expected_size and item["sha256"] == expected_file_sha256, "PACKAGING_STORE_FILE_MISMATCH")
            with _directory(journal, "ready", record["directory_identity"]) as root:
                parts = tuple(path.split("/"))
                with _descendant(root, parts[:-1]) as parent:
                    fd = os.open(parts[-1], _metadata_flags(), dir_fd=parent)
                    try:
                        _verify_file(fd, item, deadline); before = os.fstat(fd)
                        yield fd
                        _check(_signature(os.fstat(fd)) == _signature(before)
                            and _signature(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)) == _signature(before), "PACKAGING_STORE_ARTIFACT_CHANGED")
                    finally: os.close(fd)
    except PackagingStoreError: raise
    except (PackagingStageError, JournalError, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise PackagingStoreError("PACKAGING_STORE_FAILED") from None
