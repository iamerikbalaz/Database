"""Stream planned entries into a bounded ZIP, then verify every archived byte."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
import re
import stat
import time
import zipfile
import zlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.inventory import _safe_name, _signature
from app.packaging_plan import _relative
from app.preflight import ZipPolicy
from app.secure_filesystem import secure_filesystem_access_supported


class PackagingZipError(RuntimeError):
    """Fixed codes only; never paths or file contents."""


def _check(condition, code="PACKAGING_ZIP_INVALID"):
    if not condition: raise PackagingZipError(code)


@dataclass(frozen=True)
class ZipEntry:
    path: str  # Relative to the single archive root; directories end in '/'.
    size: int
    sha256: str | None
    modified_ns: int


@dataclass(frozen=True)
class ZipEntryProof:
    path: str  # Full name inside ZIP, including its root.
    size: int
    sha256: str | None
    crc32: int
    date_time: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class ArchiveProof:
    filename: str
    size: int
    sha256: str
    policy: str
    storage_timezone: str
    entries: tuple[ZipEntryProof, ...]


def _stamp(modified_ns, policy, zone):
    _check(type(modified_ns) is int and modified_ns >= 0)
    if policy == ZipPolicy.LEGACY_BEFORE_2026_03_04.value: return (2026, 1, 1, 0, 0, 0)
    value = datetime.fromtimestamp(modified_ns // 1_000_000_000, timezone.utc).astimezone(zone)
    _check(1980 <= value.year <= 2107, "PACKAGING_ZIP_TIMESTAMP_RANGE")
    # DOS timestamps have two-second precision and no embedded timezone.
    return value.year, value.month, value.day, value.hour, value.minute, value.second // 2 * 2


def _entries(root, entries, policy, storage_timezone, root_modified_ns, max_expanded_bytes):
    _check(isinstance(root, str) and _safe_name(root) and _safe_name(root + ".zip")
        and isinstance(entries, tuple) and 0 < len(entries) <= 20000
        and isinstance(policy, str) and policy in {item.value for item in ZipPolicy}
        and isinstance(storage_timezone, str) and 0 < len(storage_timezone) <= 128)
    zone = ZoneInfo(storage_timezone)
    names = {}; folded = set(); total = 0
    for entry in entries:
        _check(isinstance(entry, ZipEntry) and isinstance(entry.path, str) and _relative(entry.path.removesuffix("/")))
        _check(entry.path.rstrip("/").casefold() not in folded, "PACKAGING_ZIP_PATH_COLLISION")
        folded.add(entry.path.rstrip("/").casefold()); names[entry.path] = entry
        _check(type(entry.size) is int and 0 <= entry.size <= max_expanded_bytes)
        _check(entry.size == 0 and entry.sha256 is None if entry.path.endswith("/") else
            isinstance(entry.sha256, str) and re.fullmatch(r"[a-f0-9]{64}", entry.sha256) is not None)
        _stamp(entry.modified_ns, policy, zone); total += entry.size
    _check(total <= max_expanded_bytes, "PACKAGING_ZIP_EXPANDED_SIZE_LIMIT")
    for path in names:
        parent = path.removesuffix("/").rpartition("/")[0]
        _check(not parent or parent + "/" in names, "PACKAGING_ZIP_PARENT_MISSING")
    return tuple(sorted(entries, key=lambda entry: entry.path)), zone, _stamp(root_modified_ns, policy, zone)


class _BoundedFile:
    """Seekable ZIP IO with pre-write bounds and limited central-directory reads."""
    def __init__(self, stream, maximum, deadline):
        self.stream = stream; self.maximum = maximum; self.deadline = deadline

    def check(self):
        _check(time.monotonic() < self.deadline, "PACKAGING_ZIP_TIME_LIMIT")

    def tell(self): self.check(); return self.stream.tell()
    def seek(self, offset, whence=0): self.check(); return self.stream.seek(offset, whence)
    def seekable(self): return True
    def flush(self): self.check(); return self.stream.flush()

    def write(self, value):
        self.check()
        _check(0 <= self.stream.tell() <= self.maximum - len(value), "PACKAGING_ZIP_SIZE_LIMIT")
        pending = memoryview(value)
        while pending:
            self.check(); count = self.stream.write(pending)
            _check(count is not None and count > 0, "PACKAGING_ZIP_WRITE_FAILED"); pending = pending[count:]
        return len(value)

    def read(self, size=-1):
        self.check()
        if size == -1: size = max(0, os.fstat(self.stream.fileno()).st_size - self.stream.tell())
        _check(type(size) is int and 0 <= size <= 64 * 1024**2, "PACKAGING_ZIP_READ_LIMIT")
        return self.stream.read(size)


def _info(path, date_time, size):
    info = zipfile.ZipInfo(path, date_time); info.create_system = 3; info.file_size = size
    directory = path.endswith("/")
    info.compress_type = zipfile.ZIP_STORED if directory else zipfile.ZIP_DEFLATED
    info.compress_level = None if directory else 6
    info.external_attr = ((stat.S_IFDIR | 0o755) << 16 | 0x10) if directory else (stat.S_IFREG | 0o644) << 16
    return info


def _write_entry(archive, info, entry, open_entry, deadline):
    import fcntl
    with open_entry(entry.path) as source:
        before = os.fstat(source)
        _check(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o400 and before.st_size == entry.size
            and before.st_mtime_ns == entry.modified_ns
            and fcntl.fcntl(source, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
            "PACKAGING_ZIP_SOURCE_MISMATCH")
        os.lseek(source, 0, os.SEEK_SET); digest = hashlib.sha256(); total = 0; crc = 0
        with archive.open(info, "w") as target:
            while True:
                _check(time.monotonic() < deadline, "PACKAGING_ZIP_TIME_LIMIT")
                chunk = os.read(source, min(1024**2, entry.size + 1 - total))
                if not chunk: break
                total += len(chunk); _check(total <= entry.size, "PACKAGING_ZIP_SOURCE_MISMATCH")
                digest.update(chunk); crc = zlib.crc32(chunk, crc); target.write(chunk)
        _check(total == entry.size and digest.hexdigest() == entry.sha256 and _signature(os.fstat(source)) == _signature(before),
            "PACKAGING_ZIP_SOURCE_MISMATCH")
    return crc


def _verify(stream, expected, size):
    stream.seek(-22, os.SEEK_END); end = stream.read(22)
    _check(end[:4] == b"PK\x05\x06" and end[-2:] == b"\x00\x00", "PACKAGING_ZIP_LAYOUT_MISMATCH")
    stream.seek(0)
    with zipfile.ZipFile(stream, "r") as archive:
        actual = archive.infolist()
        _check(archive.comment == b"" and [item.filename for item in actual] == [item.path for item in expected],
            "PACKAGING_ZIP_LAYOUT_MISMATCH")
        for info, entry in zip(actual, expected, strict=True):
            directory = entry.path.endswith("/"); normal = _info(entry.path, entry.date_time, entry.size)
            _check(info.file_size == entry.size and info.CRC == entry.crc32 and info.date_time == entry.date_time
                and info.create_system == 3 and info.external_attr == normal.external_attr
                and info.compress_type == normal.compress_type and info.flag_bits in {0, 0x800}
                and info.comment == b"" and info.compress_size <= size,
                "PACKAGING_ZIP_ENTRY_MISMATCH")
            # Only the standard ZIP64 size/offset extra field can be produced.
            _check(not info.extra or (info.extra[:2] == b"\x01\x00" and len(info.extra) <= 32
                and int.from_bytes(info.extra[2:4], "little") == len(info.extra) - 4), "PACKAGING_ZIP_ENTRY_MISMATCH")
            digest = hashlib.sha256(); total = 0
            with archive.open(info, "r") as source:
                while True:
                    stream.check(); chunk = source.read(min(1024**2, entry.size + 1 - total))
                    if not chunk: break
                    total += len(chunk); _check(total <= entry.size, "PACKAGING_ZIP_ENTRY_MISMATCH"); digest.update(chunk)
            _check(total == entry.size and (directory or digest.hexdigest() == entry.sha256), "PACKAGING_ZIP_ENTRY_MISMATCH")
    stream.seek(0); digest = hashlib.sha256(); total = 0
    while True:
        chunk = stream.read(min(1024**2, size + 1 - total))
        if not chunk: break
        total += len(chunk); _check(total <= size, "PACKAGING_ZIP_CHANGED"); digest.update(chunk)
    _check(total == size, "PACKAGING_ZIP_CHANGED")
    return digest.hexdigest()


def create_verified_zip(target_fd: int, *, root: str, entries: tuple[ZipEntry, ...], open_entry,
    policy: str, storage_timezone: str, root_modified_ns: int, max_seconds: float = 600,
    max_bytes: int = 8 * 1024**3, max_expanded_bytes: int = 16 * 1024**3) -> ArchiveProof:
    """The caller reserves the private empty target and removes it on any failure.

    The source provider only opens allowlisted frozen copies. No extraction or
    source timestamp writes occur. An archive proof is process-local, not a job.
    """
    _check(secure_filesystem_access_supported(), "PACKAGING_ZIP_PLATFORM_UNSUPPORTED")
    _check(type(target_fd) is int and target_fd >= 3 and callable(open_entry)
        and type(max_seconds) in {int, float} and 0 < max_seconds <= 3600
        and type(max_bytes) is int and 0 < max_bytes <= 64 * 1024**3
        and type(max_expanded_bytes) is int and 0 < max_expanded_bytes <= 256 * 1024**3)
    deadline = time.monotonic() + max_seconds
    try:
        import fcntl
        ordered, zone, root_stamp = _entries(root, entries, policy, storage_timezone, root_modified_ns, max_expanded_bytes)
        before = os.fstat(target_fd)
        _check(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size == 0
            and fcntl.fcntl(target_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR, "PACKAGING_ZIP_UNSAFE_TARGET")
        os.lseek(target_fd, 0, os.SEEK_SET)
        expected = [ZipEntryProof(root + "/", 0, None, 0, root_stamp)]
        with os.fdopen(os.dup(target_fd), "r+b", buffering=0) as file:
            stream = _BoundedFile(file, max_bytes, deadline)
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr(_info(root + "/", root_stamp, 0), b"")
                for entry in ordered:
                    stamp = _stamp(entry.modified_ns, policy, zone); path = root + "/" + entry.path; info = _info(path, stamp, entry.size)
                    if entry.path.endswith("/"): archive.writestr(info, b""); crc = 0
                    else: crc = _write_entry(archive, info, entry, open_entry, deadline)
                    expected.append(ZipEntryProof(path, entry.size, entry.sha256, crc, stamp))
            os.fsync(target_fd); after = os.fstat(target_fd)
            _check(after.st_dev == before.st_dev and after.st_ino == before.st_ino and after.st_nlink == 1
                and 22 <= after.st_size <= max_bytes, "PACKAGING_ZIP_CHANGED")
            digest = _verify(stream, tuple(expected), after.st_size)
            _check(_signature(os.fstat(target_fd)) == _signature(after), "PACKAGING_ZIP_CHANGED")
        os.fchmod(target_fd, 0o400); os.fsync(target_fd); os.lseek(target_fd, 0, os.SEEK_SET)
        return ArchiveProof(root + ".zip", after.st_size, digest, policy, storage_timezone, tuple(expected))
    except PackagingZipError: raise
    except (OSError, ValueError, TypeError, OverflowError, KeyError, EOFError, zlib.error, ZoneInfoNotFoundError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise PackagingZipError("PACKAGING_ZIP_FAILED") from None
