"""Bounded, non-recursive directory discovery under the configured source root."""
import os
import stat
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore

from app.inventory import _safe_name, _signature
from app.secure_filesystem import _directory_flags, open_material_directory

DISCOVERY_SLOTS = BoundedSemaphore(2)


class DiscoveryError(RuntimeError):
    """Fixed diagnostic code; no source names or absolute paths."""


@dataclass(frozen=True)
class DiscoveryLimits:
    max_entries: int = 4096
    max_directories: int = 512
    max_seconds: float = 5


def _safe_discovery_name(value):
    return _safe_name(value) and not any(unicodedata.category(char).startswith("C") for char in value)


def discovery_parts(value: str) -> tuple[str, ...]:
    # Empty is the one explicit spelling of the configured root. All other
    # paths are canonical, portable POSIX relatives, never normalized guesses.
    if not isinstance(value, str) or len(value.encode("utf-8", "surrogatepass")) > 2048:
        raise ValueError("Invalid discovery path")
    parts = tuple(value.split("/")) if value else ()
    if len(parts) > 16 or any(not _safe_discovery_name(part) for part in parts):
        raise ValueError("Invalid discovery path")
    return parts


def discover_folders(root: Path, parts: tuple[str, ...], *, limits: DiscoveryLimits | None = None) -> dict:
    if discovery_parts("/".join(parts)) != parts:
        raise ValueError("Invalid discovery path")
    if not DISCOVERY_SLOTS.acquire(blocking=False):
        raise DiscoveryError("DISCOVERY_BUSY")
    try:
        return _discover(root, parts, limits or DiscoveryLimits())
    finally:
        DISCOVERY_SLOTS.release()


def _discover(root, parts, limits):
    deadline = time.monotonic() + limits.max_seconds
    parent = "/".join(parts)

    def check_time():
        if time.monotonic() > deadline:
            raise DiscoveryError("DISCOVERY_TIME_LIMIT")

    with open_material_directory(root, parts) as directory_fd:
        original = _signature(os.fstat(directory_fd))

        def snapshot():
            entries = {}
            with os.scandir(directory_fd) as listing:
                for item in listing:
                    check_time()
                    if len(entries) >= limits.max_entries:
                        raise DiscoveryError("DISCOVERY_ENTRY_LIMIT")
                    entries[item.name] = _signature(os.stat(item.name, dir_fd=directory_fd, follow_symlinks=False))
            return entries

        try:
            first = snapshot()
            directories = []
            omitted = 0
            for name, signature in sorted(first.items()):
                check_time()
                path = parent + "/" + name if parent else name
                if (not _safe_discovery_name(name) or not stat.S_ISDIR(signature[2]) or signature[0] != original[0]
                        or len(path.encode("utf-8", "surrogatepass")) > 2048 or len(parts) >= 16):
                    omitted += 1
                    continue
                if len(directories) >= limits.max_directories:
                    raise DiscoveryError("DISCOVERY_DIRECTORY_LIMIT")
                # Opening only a directory descriptor proves this is still a
                # real directory. Never read files or recurse into children.
                child_fd = os.open(name, _directory_flags(), dir_fd=directory_fd)
                try:
                    if _signature(os.fstat(child_fd)) != signature:
                        raise DiscoveryError("DISCOVERY_SOURCE_CHANGED")
                finally:
                    os.close(child_fd)
                directories.append({"name": name, "path": path})
            if snapshot() != first or _signature(os.fstat(directory_fd)) != original:
                raise DiscoveryError("DISCOVERY_SOURCE_CHANGED")
            # Reject an obsolete descriptor if an ancestor was moved/replaced.
            with open_material_directory(root, parts) as current_fd:
                if _signature(os.fstat(current_fd)) != original:
                    raise DiscoveryError("DISCOVERY_SOURCE_CHANGED")
            check_time()
        except OSError:
            raise DiscoveryError("DISCOVERY_READ_FAILED") from None
    return {"schema_version": 1, "parent_path": parent, "directories": directories, "omitted_entries": omitted}
