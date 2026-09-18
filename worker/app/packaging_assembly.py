"""Assemble all planned resolutions into verified, short-lived private artifacts."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import stat
import time
from threading import BoundedSemaphore
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.inventory import _signature
from app.packaging_convert import ConversionProof, convert_map
from app.packaging_plan import MapOperation, PackagingPlan, _digest
from app.packaging_stage import StagedInputs, PackagingStageError, _identity, _private_root, _remove_owned
from app.packaging_zip import ArchiveProof, ZipEntry, create_verified_zip
from app.secure_filesystem import _directory_flags, _metadata_flags
from app.technical_validation import probe_image

ASSEMBLY_SLOT = BoundedSemaphore(1)


class PackagingAssemblyError(RuntimeError):
    """Fixed codes without source paths or contents."""


def _check(condition, code="PACKAGING_ASSEMBLY_INVALID"):
    if not condition: raise PackagingAssemblyError(code)


@dataclass(frozen=True)
class MapArtifact:
    operation: MapOperation
    input_sha256: str
    size: int
    sha256: str
    bits: int
    conversion: ConversionProof | None


@dataclass(frozen=True)
class _File:
    name: str
    size: int
    sha256: str
    modified_ns: int


class _Budget:
    def __init__(self, seconds, size): self.deadline = time.monotonic() + seconds; self.left = size
    def seconds(self):
        result = self.deadline - time.monotonic(); _check(result > 0, "PACKAGING_ASSEMBLY_TIME_LIMIT"); return result
    def reserve(self, size):
        self.seconds(); _check(0 <= size <= self.left, "PACKAGING_ASSEMBLY_SIZE_LIMIT"); self.left -= size


def _hash_fd(fd, size, deadline):
    os.lseek(fd, 0, os.SEEK_SET); digest = hashlib.sha256(); count = 0
    while True:
        _check(time.monotonic() < deadline, "PACKAGING_ASSEMBLY_TIME_LIMIT")
        chunk = os.read(fd, min(1024**2, size + 1 - count))
        if not chunk: break
        count += len(chunk); _check(count <= size, "PACKAGING_ARTIFACT_CHANGED"); digest.update(chunk)
    _check(count == size, "PACKAGING_ARTIFACT_CHANGED")
    return digest.hexdigest()


@contextmanager
def _open(root, item, deadline):
    fd = None
    try:
        fd = os.open(item.name, _metadata_flags(), dir_fd=root); before = os.fstat(fd)
        _check(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o400 and before.st_size == item.size
            and before.st_mtime_ns == item.modified_ns, "PACKAGING_ARTIFACT_CHANGED")
        _check(_hash_fd(fd, item.size, deadline) == item.sha256, "PACKAGING_ARTIFACT_CHANGED")
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        if fd is not None: os.close(fd)
        raise PackagingAssemblyError("PACKAGING_ARTIFACT_CHANGED") from None
    except BaseException:
        if fd is not None: os.close(fd)
        raise
    try:
        yield fd
        try:
            _check(_signature(os.fstat(fd)) == _signature(before)
                and _signature(os.stat(item.name, dir_fd=root, follow_symlinks=False)) == _signature(before), "PACKAGING_ARTIFACT_CHANGED")
        except OSError: raise PackagingAssemblyError("PACKAGING_ARTIFACT_CHANGED") from None
    finally: os.close(fd)


def _new(root, name):
    return os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)


def _write(fd, value, budget):
    remaining = memoryview(value)
    while remaining:
        budget.seconds(); written = os.write(fd, remaining)
        _check(written > 0, "PACKAGING_ASSEMBLY_WRITE_FAILED"); remaining = remaining[written:]


def _copy_map(source, target, operation, budget):
    before = os.fstat(source); budget.reserve(before.st_size); digest = hashlib.sha256(); count = 0
    os.lseek(source, 0, os.SEEK_SET)
    while True:
        budget.seconds(); chunk = os.read(source, min(1024**2, before.st_size + 1 - count))
        if not chunk: break
        count += len(chunk); _check(count <= before.st_size, "PACKAGING_ARTIFACT_CHANGED"); digest.update(chunk); _write(target, chunk, budget)
    _check(count == before.st_size and digest.hexdigest() == operation.source_sha256, "PACKAGING_ARTIFACT_CHANGED")
    os.fsync(target); os.lseek(target, 0, os.SEEK_SET)
    actual = probe_image(target, timeout=budget.seconds())
    _check("error" not in actual and actual["sha256"] == operation.source_sha256 and actual["width"] == operation.width
        and actual["height"] == operation.height and actual["format"] == operation.format and actual["bits"] == operation.bits,
        "PACKAGING_COPY_IMAGE_MISMATCH")
    os.fchmod(target, 0o400); os.fsync(target)
    return MapArtifact(operation, operation.source_sha256, count, actual["sha256"], actual["bits"], None)


@dataclass(frozen=True)
class PreparedPackages:
    operation_id: UUID
    plan: PackagingPlan
    storage_timezone: str
    manifest_sha256: str
    maps: tuple[MapArtifact, ...]
    archives: tuple[ArchiveProof, ...]
    _fd: int
    _files: tuple[_File, ...]
    _live: list[bool]
    _workspace_root: Path
    _materials_root: Path

    @property
    def proof_sha256(self):
        return _digest(self.proof_document())

    def proof_document(self):
        return {"operation_id": str(self.operation_id), "plan_sha256": self.plan.sha256,
            "storage_timezone": self.storage_timezone, "manifest_sha256": self.manifest_sha256,
            "maps": [asdict(item) for item in self.maps], "archives": [asdict(item) for item in self.archives]}

    @contextmanager
    def _open_named(self, name, max_seconds=120):
        _check(self._live[0], "PACKAGING_ARTIFACTS_CLOSED")
        _check(type(max_seconds) in {int, float} and 0 < max_seconds <= 120)
        item = next((item for item in self._files if item.name == name), None)
        _check(item is not None, "PACKAGING_ARTIFACT_NOT_FOUND")
        with _open(self._fd, item, time.monotonic() + max_seconds) as fd: yield fd

    def open_archive(self, filename, *, max_seconds=120):
        _check(any(item.filename == filename for item in self.archives), "PACKAGING_ARTIFACT_NOT_FOUND")
        return self._open_named(filename, max_seconds)

    def open_manifest(self, *, max_seconds=120): return self._open_named("metadata.json", max_seconds)


def _assemble(staged, fd, path, storage_timezone, budget):
    plan = staged.plan; maps = []; map_files = {}; effective = {}; artifacts = []; files = []
    os.mkdir("cache", 0o700, dir_fd=fd)
    for resolution in plan.resolutions:
        for operation in resolution.maps:
            name = "map-" + str(len(maps)).zfill(4); target = _new(fd, name)
            try:
                input_ref = effective.get(operation.shortcut) if operation.input_is_effective_master else None
                _check(not operation.input_is_effective_master or input_ref is not None)
                provider = _open(fd, input_ref, budget.deadline) if input_ref else staged.open_input(operation.source, max_seconds=min(120, budget.seconds()))
                with provider as source:
                    if operation.action == "COPY": item = _copy_map(source, target, operation, budget)
                    else:
                        _check(budget.left > 0, "PACKAGING_ASSEMBLY_SIZE_LIMIT")
                        input_hash = input_ref.sha256 if input_ref else operation.source_sha256
                        result = convert_map(source, target, operation, expected_input_sha256=input_hash,
                            cache_root=path / "cache", max_seconds=min(150, budget.seconds()), max_bytes=min(2 * 1024**3, budget.left))
                        budget.reserve(result.size)
                        item = MapArtifact(operation, input_hash, result.size, result.sha256, result.bits, result)
                info = os.fstat(target); ref = _File(name, item.size, item.sha256, info.st_mtime_ns)
                _check(info.st_size == item.size, "PACKAGING_ARTIFACT_CHANGED")
                map_files[operation.destination] = ref; maps.append(item)
                if not operation.input_is_effective_master: effective[operation.shortcut] = ref
            finally: os.close(target)
    manifest = plan.web_manifest(); manifest_hash = hashlib.sha256(manifest).hexdigest(); budget.reserve(len(manifest))
    target = _new(fd, "metadata.json")
    try:
        _write(target, manifest, budget); os.fsync(target); os.fchmod(target, 0o400)
        manifest_file = _File("metadata.json", len(manifest), manifest_hash, os.fstat(target).st_mtime_ns)
    finally: os.close(target)
    files.append(manifest_file)
    for resolution in plan.resolutions:
        entries = []; providers = {}; stamp = time.time_ns()
        for name in resolution.archive_entries:
            if name.endswith("/"): entries.append(ZipEntry(name, 0, None, stamp)); continue
            if name == "metadata.json": item = manifest_file
            elif name in map_files: item = map_files[name]
            else: item = None
            if item is not None:
                providers[name] = ("artifact", item)
                entries.append(ZipEntry(name, item.size, item.sha256, item.modified_ns))
            else:
                source_path = "metadata.txt" if name == resolution.name + "/metadata.txt" else name
                proof = plan.production_metadata if source_path == "metadata.txt" else next((p for p in plan.previews if p.path == source_path), None)
                _check(proof is not None)
                with staged.open_input(source_path, max_seconds=min(120, budget.seconds())) as source: modified = os.fstat(source).st_mtime_ns
                providers[name] = ("input", source_path)
                entries.append(ZipEntry(name, proof.size, proof.sha256, modified))
        @contextmanager
        def open_entry(name):
            _check(name in providers)
            kind, value = providers[name]
            with (_open(fd, value, budget.deadline) if kind == "artifact" else staged.open_input(value, max_seconds=min(120, budget.seconds()))) as source: yield source
        target = _new(fd, resolution.archive_name)
        try:
            _check(budget.left > 0, "PACKAGING_ASSEMBLY_SIZE_LIMIT")
            archive = create_verified_zip(target, root=resolution.archive_root, entries=tuple(entries), open_entry=open_entry,
                policy=plan.policy, storage_timezone=storage_timezone, root_modified_ns=stamp,
                max_seconds=min(600, budget.seconds()), max_bytes=min(8 * 1024**3, budget.left))
            budget.reserve(archive.size); artifacts.append(archive)
            files.append(_File(archive.filename, archive.size, archive.sha256, os.fstat(target).st_mtime_ns))
        finally: os.close(target)
    return manifest_hash, tuple(maps), tuple(artifacts), tuple(files)


@contextmanager
def assemble_packages(staged: StagedInputs, *, workspace_root: Path, storage_timezone: str,
    max_seconds: float = 900, max_bytes: int = 16 * 1024**3):
    """Yield a complete verified bundle, then clean only its owned workspace.

    Input staging must remain live. No durable result is committed: the caller
    still needs authorization/current-approval checks and guarded artifact storage.
    """
    if not ASSEMBLY_SLOT.acquire(blocking=False): raise PackagingAssemblyError("PACKAGING_ASSEMBLY_BUSY")
    try:
        with _assemble_packages(staged, workspace_root=workspace_root, storage_timezone=storage_timezone,
                max_seconds=max_seconds, max_bytes=max_bytes) as result: yield result
    finally: ASSEMBLY_SLOT.release()


@contextmanager
def _assemble_packages(staged, *, workspace_root, storage_timezone, max_seconds, max_bytes):
    _check(isinstance(staged, StagedInputs) and staged._live[0] and isinstance(workspace_root, Path)
        and isinstance(storage_timezone, str) and 0 < len(storage_timezone) <= 128
        and type(max_seconds) in {int, float} and 0 < max_seconds <= 3600
        and type(max_bytes) is int and 0 < max_bytes <= 128 * 1024**3)
    _check(workspace_root == staged._workspace_root, "PACKAGING_ASSEMBLY_ROOT_MISMATCH")
    name = "artifacts-" + str(staged.operation_id); budget = _Budget(max_seconds, max_bytes)
    try:
        ZoneInfo(storage_timezone)
        with _private_root(workspace_root) as root:
            _check(_identity(os.fstat(root)) == _identity(os.fstat(staged._storage_fd)), "PACKAGING_ASSEMBLY_ROOT_MISMATCH")
            try: os.mkdir(name, 0o700, dir_fd=root)
            except FileExistsError: raise PackagingAssemblyError("PACKAGING_ARTIFACTS_ALREADY_EXIST") from None
            fd = os.open(name, _directory_flags(), dir_fd=root); owned = _identity(os.fstat(fd))
            try:
                result = _assemble(staged, fd, workspace_root / name, storage_timezone, budget)
                _check(_identity(os.stat(name, dir_fd=root, follow_symlinks=False)) == owned, "PACKAGING_ARTIFACT_CHANGED")
                os.fsync(fd); live = [True]
                try: yield PreparedPackages(staged.operation_id, staged.plan, storage_timezone, *result[:3], fd, result[3], live, workspace_root, staged._materials_root)
                finally: live[0] = False
            finally:
                os.close(fd)
                try: _remove_owned(root, name, owned); os.fsync(root)
                except (OSError, PackagingStageError): raise PackagingAssemblyError("PACKAGING_ASSEMBLY_CLEANUP_REQUIRED") from None
    except PackagingAssemblyError: raise
    except PackagingStageError:
        raise PackagingAssemblyError("PACKAGING_ASSEMBLY_INPUT_OR_ROOT_UNSAFE") from None
    except (OSError, ValueError, ZoneInfoNotFoundError):
        raise PackagingAssemblyError("PACKAGING_ASSEMBLY_FAILED") from None
