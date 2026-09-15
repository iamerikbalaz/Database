"""Bounded read-only inventory of one anchored material tree.

Content hashes identify a completed scan, not a filesystem snapshot or lease.
Consumers must rescan before approval and before copying publication inputs.
"""
import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from app.preflight import configured_policy_boundary, select_zip_policy
from app.secure_filesystem import (
    _directory_flags, _metadata_flags, _resolution_info, open_material_directory,
)


class InventoryError(RuntimeError):
    """Safe fixed code; no source contents or absolute paths."""


@dataclass(frozen=True)
class InventoryLimits:
    max_entries: int = 20_000
    max_depth: int = 16
    max_file_bytes: int = 64 * 1024**3
    max_total_bytes: int = 256 * 1024**3
    max_seconds: float = 120


def _signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _safe_name(name: str) -> bool:
    return (bool(name) and name not in {".", ".."} and len(name.encode("utf-8", "surrogatepass")) <= 255
            and not any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
                        or char in "/\\:" for char in name))


def inventory_material(root: Path, parts: tuple[str, ...], *,
                       limits: InventoryLimits | None = None) -> dict:
    limits = limits or InventoryLimits()
    deadline = time.monotonic() + limits.max_seconds
    entries: list[dict] = []
    signatures: dict[str, tuple] = {}
    total = 0

    def check_time():
        if time.monotonic() > deadline:
            raise InventoryError("INVENTORY_TIME_LIMIT")

    with open_material_directory(root, parts) as material_fd:
        root_signature = _signature(os.fstat(material_fd))
        device = root_signature[0]

        def walk(directory_fd: int, prefix: str, depth: int, *, verify: bool):
            nonlocal total
            check_time()
            if depth > limits.max_depth:
                raise InventoryError("INVENTORY_DEPTH_LIMIT")
            directory_before = _signature(os.fstat(directory_fd))
            names = []
            with os.scandir(directory_fd) as listing:
                for item in listing:
                    check_time()
                    if len(names) >= limits.max_entries:
                        raise InventoryError("INVENTORY_ENTRY_LIMIT")
                    names.append(item.name)
            names.sort()
            for name in names:
                check_time()
                if not _safe_name(name):
                    raise InventoryError("INVENTORY_UNSAFE_NAME")
                relative = prefix + name
                if len(relative.encode("utf-8")) > 2048:
                    raise InventoryError("INVENTORY_PATH_LIMIT")
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                is_directory = stat.S_ISDIR(before.st_mode)
                if (before.st_dev != device or not (is_directory or stat.S_ISREG(before.st_mode))
                        or (not is_directory and before.st_nlink != 1)):
                    raise InventoryError("INVENTORY_UNSAFE_ENTRY")
                signature = _signature(before)
                if verify:
                    if signatures.get(relative) != signature:
                        raise InventoryError("INVENTORY_SOURCE_CHANGED")
                else:
                    if len(entries) >= limits.max_entries:
                        raise InventoryError("INVENTORY_ENTRY_LIMIT")
                    signatures[relative] = signature
                fd = os.open(name, _directory_flags() if is_directory else _metadata_flags(), dir_fd=directory_fd)
                try:
                    if _signature(os.fstat(fd)) != signature:
                        raise InventoryError("INVENTORY_SOURCE_CHANGED")
                    if is_directory:
                        if not verify:
                            entries.append({"path": relative, "kind": "directory", "size": 0, "sha256": None})
                        walk(fd, relative + "/", depth + 1, verify=verify)
                    elif not verify:
                        if before.st_size > limits.max_file_bytes:
                            raise InventoryError("INVENTORY_FILE_LIMIT")
                        if total + before.st_size > limits.max_total_bytes:
                            raise InventoryError("INVENTORY_TOTAL_LIMIT")
                        digest = hashlib.sha256()
                        count = 0
                        while True:
                            check_time()
                            chunk = os.read(fd, 1024 * 1024)
                            if not chunk:
                                break
                            count += len(chunk)
                            if count > before.st_size:
                                raise InventoryError("INVENTORY_SOURCE_CHANGED")
                            digest.update(chunk)
                        if count != before.st_size:
                            raise InventoryError("INVENTORY_SOURCE_CHANGED")
                        total += count
                        entries.append({"path": relative, "kind": "file", "size": count, "sha256": digest.hexdigest()})
                    if _signature(os.fstat(fd)) != signature:
                        raise InventoryError("INVENTORY_SOURCE_CHANGED")
                finally:
                    os.close(fd)
                if _signature(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)) != signature:
                    raise InventoryError("INVENTORY_SOURCE_CHANGED")
            if _signature(os.fstat(directory_fd)) != directory_before:
                raise InventoryError("INVENTORY_SOURCE_CHANGED")

        try:
            walk(material_fd, "", 0, verify=False)
            master, modified, modified_ns, _, errors = _resolution_info(material_fd)
            if any(item.code != "NO_RESOLUTION" for item in errors):
                raise InventoryError("INVENTORY_UNSAFE_ENTRY")
            policy = select_zip_policy(modified_ns, boundary=configured_policy_boundary()) if modified_ns is not None else None
            walk(material_fd, "", 0, verify=True)
            if _signature(os.fstat(material_fd)) != root_signature:
                raise InventoryError("INVENTORY_SOURCE_CHANGED")
            # A rename of any requested ancestor must not produce an approvable
            # inventory for an obsolete descriptor. Reopen without following links.
            with open_material_directory(root, parts) as current_fd:
                if _signature(os.fstat(current_fd)) != root_signature:
                    raise InventoryError("INVENTORY_SOURCE_CHANGED")
        except OSError:
            raise InventoryError("INVENTORY_READ_FAILED") from None

    entries.sort(key=lambda item: item["path"])
    revision = {"schema_version": 1, "folder_name": parts[-1], "master_resolution": master,
                "policy": policy, "entries": entries}
    digest = hashlib.sha256(json.dumps(revision, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    return {**revision, "source_revision_hash": digest, "total_bytes": total,
            "master_last_modified_at": modified}
