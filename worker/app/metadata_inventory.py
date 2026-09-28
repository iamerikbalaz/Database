"""Bounded filename-only source inventory for production metadata generation."""
import os
import stat
import time

from app.file_journal import JournalError
from app.inventory import _safe_name, _signature
from app.secure_filesystem import _directory_flags, open_material_directory


def metadata_inventory(root, parts, material_fd):
    """No image reads or hashes; verify descriptors and a second stat pass.

    The caller holds the material operation lock and freezes the derived JSON
    in its existing durable journal. This is evidence of observed names only.
    """
    deadline = time.monotonic() + 10
    original = _signature(os.fstat(material_fd))
    entries = []; signatures = {}

    def walk(fd, prefix, depth, *, verify):
        if depth > 16 or time.monotonic() > deadline:
            raise JournalError("SOURCE_METADATA_UNREADABLE")
        before = _signature(os.fstat(fd))
        names = []
        with os.scandir(fd) as iterator:
            for item in iterator:
                if len(names) >= 20_000 or time.monotonic() > deadline:
                    raise JournalError("SOURCE_METADATA_UNREADABLE")
                names.append(item.name)
        for name in sorted(names):
            relative = prefix + name
            if (not _safe_name(name) or len(relative.encode("utf-8")) > 2048
                    or len(relative.split("/")) > 16 or time.monotonic() > deadline):
                raise JournalError("SOURCE_METADATA_UNREADABLE")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            signature = _signature(info); directory = stat.S_ISDIR(info.st_mode)
            if (info.st_dev != original[0] or not (directory or stat.S_ISREG(info.st_mode))
                    or not directory and info.st_nlink != 1):
                raise JournalError("SOURCE_METADATA_UNSAFE_FILE")
            if verify:
                if signatures.get(relative) != signature: raise JournalError("METADATA_SOURCE_CHANGED")
            else:
                if len(entries) >= 20_000: raise JournalError("SOURCE_METADATA_UNREADABLE")
                signatures[relative] = signature
                entries.append({"path": relative, "kind": "directory" if directory else "file"})
            if directory:
                child = os.open(name, _directory_flags(), dir_fd=fd)
                try:
                    if _signature(os.fstat(child)) != signature: raise JournalError("METADATA_SOURCE_CHANGED")
                    walk(child, relative + "/", depth + 1, verify=verify)
                finally: os.close(child)
            if _signature(os.stat(name, dir_fd=fd, follow_symlinks=False)) != signature:
                raise JournalError("METADATA_SOURCE_CHANGED")
        if _signature(os.fstat(fd)) != before: raise JournalError("METADATA_SOURCE_CHANGED")

    try:
        walk(material_fd, "", 0, verify=False)
        walk(material_fd, "", 0, verify=True)
        if _signature(os.fstat(material_fd)) != original: raise JournalError("METADATA_SOURCE_CHANGED")
        with open_material_directory(root, parts) as latest:
            if _signature(os.fstat(latest)) != original: raise JournalError("METADATA_SOURCE_CHANGED")
    except OSError: raise JournalError("SOURCE_METADATA_UNREADABLE") from None
    return entries
