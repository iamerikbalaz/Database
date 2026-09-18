"""Private, short-lived copies of approved inputs. Source files are read-only."""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import time
from uuid import UUID

from app.inventory import InventoryError, InventoryLimits, _safe_name, _signature, inventory_material
from app.packaging_plan import PackagingPlan, build_packaging_plan
from app.secure_filesystem import (MaterialFolderNotFound, MaterialsRootUnavailable, UnsafeMaterialPath,
    SecureFilesystemAccessUnavailable, _directory_flags, _metadata_flags, open_material_directory, secure_filesystem_access_supported)


class PackagingStageError(RuntimeError):
    """Fixed codes only; no filesystem paths or source contents."""


def _identity(info): return (info.st_dev, info.st_ino)


def _check(condition, code="PACKAGING_STAGE_UNSAFE"):
    if not condition: raise PackagingStageError(code)


def _read_digest(fd, size, deadline):
    digest = hashlib.sha256(); count = 0
    while True:
        _check(time.monotonic() < deadline, "PACKAGING_STAGE_TIME_LIMIT")
        chunk = os.read(fd, min(1024 * 1024, size + 1 - count))
        if not chunk: break
        count += len(chunk); _check(count <= size, "PACKAGING_STAGED_INPUT_CHANGED"); digest.update(chunk)
    _check(count == size, "PACKAGING_STAGED_INPUT_CHANGED")
    return digest.hexdigest()


@contextmanager
def _descendant(root_fd, parts):
    with ExitStack() as descriptors:
        current = root_fd
        for part in parts:
            _check(_safe_name(part))
            current = os.open(part, _directory_flags(), dir_fd=current)
            descriptors.callback(os.close, current)
        yield current


@contextmanager
def _private_root(path):
    _check(path.is_absolute() and len(path.parts) > 1)
    with ExitStack() as descriptors:
        current = os.open(path.anchor, _directory_flags()); descriptors.callback(os.close, current)
        for part in path.parts[1:]:
            _check(_safe_name(part))
            current = os.open(part, _directory_flags(), dir_fd=current); descriptors.callback(os.close, current)
        info = os.fstat(current)
        _check(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not stat.S_IMODE(info.st_mode) & 0o077,
            "PACKAGING_STAGE_PRIVATE_ROOT_REQUIRED")
        yield current


def _mkdir(root_fd, parts):
    with ExitStack() as descriptors:
        current = root_fd
        for part in parts:
            _check(_safe_name(part))
            try: os.mkdir(part, 0o700, dir_fd=current)
            except FileExistsError: pass
            current = os.open(part, _directory_flags(), dir_fd=current); descriptors.callback(os.close, current)
            info = os.fstat(current)
            _check(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)


def _copy(source_fd, input_fd, entry, deadline):
    parts = tuple(entry["path"].split("/")); _mkdir(input_fd, parts[:-1])
    with _descendant(source_fd, parts[:-1]) as source_parent, _descendant(input_fd, parts[:-1]) as target_parent:
        source = os.open(parts[-1], _metadata_flags(), dir_fd=source_parent)
        try:
            before = os.fstat(source)
            _check(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == entry["size"], "PACKAGING_SOURCE_CHANGED")
            target = os.open(parts[-1], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=target_parent)
            try:
                digest = hashlib.sha256(); count = 0
                while True:
                    _check(time.monotonic() < deadline, "PACKAGING_STAGE_TIME_LIMIT")
                    chunk = os.read(source, min(1024 * 1024, entry["size"] + 1 - count))
                    if not chunk: break
                    count += len(chunk); _check(count <= entry["size"], "PACKAGING_SOURCE_CHANGED"); digest.update(chunk)
                    remaining = memoryview(chunk)
                    while remaining:
                        _check(time.monotonic() < deadline, "PACKAGING_STAGE_TIME_LIMIT")
                        written = os.write(target, remaining); _check(written > 0, "PACKAGING_STAGE_WRITE_FAILED"); remaining = remaining[written:]
                _check(count == entry["size"] and digest.hexdigest() == entry["sha256"], "PACKAGING_SOURCE_CHANGED")
                _check(_signature(os.fstat(source)) == _signature(before) and _signature(os.stat(parts[-1], dir_fd=source_parent, follow_symlinks=False)) == _signature(before), "PACKAGING_SOURCE_CHANGED")
                os.fsync(target); os.lseek(target, 0, os.SEEK_SET)
                _check(_read_digest(target, entry["size"], deadline) == entry["sha256"], "PACKAGING_STAGE_COPY_MISMATCH")
                os.fchmod(target, 0o400); os.fsync(target)
            finally: os.close(target)
        finally: os.close(source)


def _remove_owned(parent_fd, name, expected, *, depth=0, budget=None):
    """Descriptor-relative cleanup refuses a replaced workspace or mount point."""
    budget = budget if budget is not None else [50000]
    _check(depth <= 20, "PACKAGING_STAGE_CLEANUP_REQUIRED")
    fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    try:
        _check(_identity(os.fstat(fd)) == expected, "PACKAGING_STAGE_CLEANUP_REQUIRED")
        names = []
        with os.scandir(fd) as listing:
            for item in listing:
                _check(len(names) < budget[0], "PACKAGING_STAGE_CLEANUP_REQUIRED")
                names.append(item.name)
        for entry in names:
            budget[0] -= 1; _check(budget[0] >= 0 and _safe_name(entry), "PACKAGING_STAGE_CLEANUP_REQUIRED")
            info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                _check(info.st_dev == expected[0], "PACKAGING_STAGE_CLEANUP_REQUIRED")
                _remove_owned(fd, entry, _identity(info), depth=depth + 1, budget=budget)
            else:
                # unlink does not follow a symlink, including one inserted after
                # a failed converter. It never deletes the referenced target.
                os.unlink(entry, dir_fd=fd)
        _check(_identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) == expected, "PACKAGING_STAGE_CLEANUP_REQUIRED")
        os.rmdir(name, dir_fd=parent_fd)
    finally: os.close(fd)


@dataclass(frozen=True)
class StagedInputs:
    operation_id: UUID
    plan: PackagingPlan
    _fd: int
    _entries: tuple[tuple[str, int, str], ...]
    _live: list[bool]

    @contextmanager
    def open_input(self, path: str):
        """Yield one read-only descriptor; reject arbitrary or replaced inputs."""
        _check(self._live[0], "PACKAGING_STAGE_CLOSED")
        expected = next((entry for entry in self._entries if entry[0] == path), None)
        _check(expected is not None, "PACKAGING_INPUT_NOT_PLANNED")
        parts = tuple(path.split("/"))
        with ExitStack() as descriptors:
            try:
                parent = descriptors.enter_context(_descendant(self._fd, parts[:-1]))
                fd = os.open(parts[-1], _metadata_flags(), dir_fd=parent)
                descriptors.callback(os.close, fd)
                before = os.fstat(fd)
                _check(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o400
                    and before.st_uid == os.geteuid() and before.st_size == expected[1], "PACKAGING_STAGED_INPUT_CHANGED")
                _check(_read_digest(fd, expected[1], time.monotonic() + 120) == expected[2], "PACKAGING_STAGED_INPUT_CHANGED")
                os.lseek(fd, 0, os.SEEK_SET)
            except OSError:
                raise PackagingStageError("PACKAGING_STAGED_INPUT_CHANGED") from None
            yield fd
            try:
                _check(_signature(os.fstat(fd)) == _signature(before), "PACKAGING_STAGED_INPUT_CHANGED")
            except OSError:
                raise PackagingStageError("PACKAGING_STAGED_INPUT_CHANGED") from None


@contextmanager
def stage_packaging_inputs(materials_root: Path, parts: tuple[str, ...], report: dict, *,
    expected_source_revision_hash: str, policy: str, workspace_root: Path, operation_id: UUID,
    max_seconds: float = 120, max_bytes: int = 8 * 1024**3):
    """Freshly verify inputs, yield a private copy, and clean only this workspace.

    No conversion or publication occurs. The caller must hold a durable authorized
    material operation. A killed process needs a later guarded orphan-cleanup flow.
    """
    _check(secure_filesystem_access_supported(), "PACKAGING_STAGE_PLATFORM_UNSUPPORTED")
    _check(isinstance(operation_id, UUID) and isinstance(materials_root, Path) and isinstance(workspace_root, Path)
        and parts and all(isinstance(part, str) and _safe_name(part) for part in parts))
    _check(type(max_seconds) in {int, float} and 0 < max_seconds <= 600 and type(max_bytes) is int and 0 < max_bytes <= 256 * 1024**3)
    plan = build_packaging_plan(report, expected_source_revision_hash=expected_source_revision_hash, policy=policy)
    _check(plan.identity == parts[-1])
    entries = {entry["path"]: entry for entry in report["inventory"]["entries"]}
    required = {item.source for item in plan.resolutions[0].maps} | {item.path for item in plan.previews}
    if plan.production_metadata: required.add(plan.production_metadata.path)
    _check(sum(entries[path]["size"] for path in required) <= max_bytes, "PACKAGING_STAGE_SIZE_LIMIT")
    deadline = time.monotonic() + max_seconds
    name = "packaging-" + str(operation_id)
    try:
        material_path = materials_root.resolve(strict=True); workspace_path = workspace_root.resolve(strict=True)
        _check(not workspace_path.is_relative_to(material_path) and not material_path.is_relative_to(workspace_path), "PACKAGING_STAGE_ROOT_OVERLAP")
        with _private_root(workspace_root) as storage:
            def fresh():
                _check(time.monotonic() < deadline, "PACKAGING_STAGE_TIME_LIMIT")
                current = inventory_material(materials_root, parts, limits=InventoryLimits(max_seconds=deadline - time.monotonic()))
                _check(current["source_revision_hash"] == expected_source_revision_hash, "PACKAGING_SOURCE_CHANGED")
            fresh()
            try: os.mkdir(name, 0o700, dir_fd=storage)
            except FileExistsError: raise PackagingStageError("PACKAGING_STAGE_ALREADY_EXISTS") from None
            workspace = os.open(name, _directory_flags(), dir_fd=storage); owned = _identity(os.fstat(workspace))
            try:
                os.mkdir("inputs", 0o700, dir_fd=workspace)
                with _descendant(workspace, ("inputs",)) as inputs, open_material_directory(materials_root, parts) as source:
                    for directory in plan.preview_directories: _mkdir(inputs, tuple(directory.rstrip("/").split("/")))
                    for path in sorted(required): _copy(source, inputs, entries[path], deadline)
                    fresh()
                    _check(_identity(os.stat(name, dir_fd=storage, follow_symlinks=False)) == owned)
                    live = [True]
                    try: yield StagedInputs(operation_id, plan, inputs, tuple((path, entries[path]["size"], entries[path]["sha256"]) for path in sorted(required)), live)
                    finally: live[0] = False
            finally:
                os.close(workspace)
                try: _remove_owned(storage, name, owned); os.fsync(storage)
                except (OSError, PackagingStageError): raise PackagingStageError("PACKAGING_STAGE_CLEANUP_REQUIRED") from None
    except PackagingStageError: raise
    except (InventoryError, MaterialFolderNotFound, MaterialsRootUnavailable, UnsafeMaterialPath, SecureFilesystemAccessUnavailable):
        raise PackagingStageError("PACKAGING_SOURCE_CHECK_FAILED") from None
    except OSError: raise PackagingStageError("PACKAGING_STAGE_IO_FAILED") from None
