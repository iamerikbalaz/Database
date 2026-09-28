"""Fixed, private desktop report location, independent of material source data."""
from datetime import datetime, timezone
import ctypes
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from app.local_filesystem import FileInfo, LocalFilesystem, LocalFilesError


def _validated_report_target(local, final):
    """Allow only the fixed report namespace, including Windows MSIX redirection."""
    prefix = "\\\\?\\"
    if not final.startswith(prefix):
        raise LocalFilesError("LOCAL_REPORTS_UNSAFE")
    target = Path(final[len(prefix):])
    if target == local / "REAWOTE":
        return target
    try:
        parts = target.relative_to(local).parts
    except ValueError:
        raise LocalFilesError("LOCAL_REPORTS_UNSAFE") from None
    # Packaged desktop children can inherit file virtualization without having
    # their own package identity. KnownFolder APIs still return the logical path.
    if (len(parts) != 5 or parts[0].casefold() != "packages"
            or tuple(part.casefold() for part in parts[2:]) != ("localcache", "local", "reawote")):
        raise LocalFilesError("LOCAL_REPORTS_UNSAFE")
    return target


def _default_report_root():
    local = os.environ.get("LOCALAPPDATA")
    if not local or not Path(local).is_absolute():
        raise LocalFilesError("LOCAL_REPORTS_UNAVAILABLE")
    local = Path(local)
    fs = LocalFilesystem(local)
    # Only this fixed output directory may resolve an OS virtualization target.
    # Source directories retain LocalFilesystem's exact-path checks unchanged.
    with fs.directory(""):
        logical = local / "REAWOTE"
        logical.mkdir(exist_ok=True)
        handle = fs.kernel.CreateFileW(str(logical), 0x81, 3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise LocalFilesError("LOCAL_REPORTS_UNAVAILABLE")
        try:
            info = FileInfo()
            if not fs.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise LocalFilesError("LOCAL_REPORTS_UNAVAILABLE")
            if not info.attrs & 0x10 or info.attrs & 0x400:
                raise LocalFilesError("LOCAL_REPORTS_UNSAFE")
            final = ctypes.create_unicode_buffer(32768)
            length = fs.kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
            if length < 1 or length >= len(final):
                raise LocalFilesError("LOCAL_REPORTS_UNSAFE")
            target = _validated_report_target(local, final.value)
            # Check every physical ancestor without following junctions, while
            # the original directory handle prevents substitution of the target.
            LocalFilesystem(target)
            return target / "Reports" / "Checks"
        finally:
            fs.kernel.CloseHandle(handle)


class LocalCheckReports:
    def __init__(self, root=None):
        if root is None:
            root = _default_report_root()
        self.root = Path(os.path.abspath(root))
        if self.root == Path(self.root.anchor):
            raise LocalFilesError("LOCAL_REPORTS_UNSAFE")

    def _filesystem(self):
        # Each existing ancestor is opened without following reparse points.
        # Keep the verified parent held while creating each missing child.
        pending = []; current = self.root
        while not current.exists():
            pending.append(current); current = current.parent
        fs = LocalFilesystem(current)
        for child in reversed(pending):
            with fs.directory(""):
                child.mkdir(exist_ok=True)
                fs = LocalFilesystem(child)
        return fs

    def save(self, report, *, open_report=True):
        if not isinstance(report, str) or len(report.encode("utf-8")) > 16 * 1024 * 1024:
            raise LocalFilesError("LOCAL_REPORT_TOO_LARGE")
        fs = self._filesystem()
        name = "automatic-file-check-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex + ".txt"
        with fs.directory("") as directory:
            path = directory / name
            with open(path, "xb") as output:
                output.write(report.encode("utf-8-sig")); output.flush(); os.fsync(output.fileno())
            opened = False
            if open_report:
                try:
                    with fs.opened(path):
                        subprocess.Popen([str(Path(os.environ["WINDIR"]) / "System32" / "notepad.exe"), str(path)], close_fds=True)
                    opened = True
                except (OSError, KeyError, LocalFilesError):
                    pass
            return {"report_path": str(path), "report_opened": opened}
