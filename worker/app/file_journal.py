"""Linux-only durable state for one explicitly authorized filesystem operation."""
from contextlib import contextmanager
import ctypes
import errno
import json
import os
from pathlib import Path
import stat
from uuid import UUID

from app.secure_filesystem import _directory_flags, _metadata_flags, open_material_directory
from app.inventory import _safe_name

MAX_STATE_BYTES = 32 * 1024 * 1024


class JournalError(RuntimeError):
    """Fixed public code; never include OS paths or stored document contents."""


def rename_noreplace(source_fd: int, source: str, target_fd: int, target: str):
    """Atomic Linux rename that cannot replace an existing destination."""
    if not _safe_name(source) or not _safe_name(target): raise JournalError("JOURNAL_NAME_INVALID")
    if os.name != "posix": raise JournalError("JOURNAL_PLATFORM_UNSUPPORTED")
    try:
        function = ctypes.CDLL(None, use_errno=True).renameat2
        function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        function.restype = ctypes.c_int
        if function(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1) != 0:
            code = ctypes.get_errno()
            if code == errno.EEXIST: raise JournalError("JOURNAL_TARGET_EXISTS")
            if code in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}: raise JournalError("JOURNAL_PLATFORM_UNSUPPORTED")
            raise JournalError("JOURNAL_RENAME_FAILED")
        os.fsync(source_fd)
        if target_fd != source_fd: os.fsync(target_fd)
    except (AttributeError, OSError):
        raise JournalError("JOURNAL_RENAME_FAILED") from None


def _safe_private_file(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise JournalError("JOURNAL_UNSAFE_STORAGE")


def _read(fd, limit):
    chunks = []; size = 0
    while True:
        chunk = os.read(fd, min(65536, limit + 1 - size))
        if not chunk: break
        size += len(chunk)
        if size > limit: raise JournalError("JOURNAL_SIZE_LIMIT")
        chunks.append(chunk)
    return b"".join(chunks)


class FileJournal:
    def __init__(self, fd): self.fd = fd

    def backup_metadata(self, raw: bytes):
        if len(raw) > 4 * 1024 * 1024: raise JournalError("JOURNAL_SIZE_LIMIT")
        try:
            fd = os.open("metadata.original", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
            try:
                remaining = memoryview(raw)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0: raise JournalError("JOURNAL_WRITE_FAILED")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally: os.close(fd)
            os.fsync(self.fd)
        except OSError: raise JournalError("JOURNAL_BACKUP_FAILED") from None

    def original_metadata(self):
        try:
            fd = os.open("metadata.original", _metadata_flags(), dir_fd=self.fd)
            try:
                _safe_private_file(fd)
                return _read(fd, 4 * 1024 * 1024)
            finally: os.close(fd)
        except OSError: raise JournalError("JOURNAL_BACKUP_FAILED") from None

    def read(self):
        try:
            fd = os.open("state.json", _metadata_flags(), dir_fd=self.fd)
        except FileNotFoundError: return None
        except OSError: raise JournalError("JOURNAL_UNSAFE_STORAGE") from None
        try:
            _safe_private_file(fd)
            value = json.loads(_read(fd, MAX_STATE_BYTES))
            if not isinstance(value, dict): raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError, RecursionError, OSError):
            raise JournalError("JOURNAL_INVALID_STATE") from None
        finally: os.close(fd)

    def write(self, value):
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > MAX_STATE_BYTES: raise JournalError("JOURNAL_SIZE_LIMIT")
        try:
            # A prior interrupted write may leave this private temporary file.
            # Never truncate before verifying regular-file, ownership and links.
            fd = os.open("state.pending", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=self.fd)
            try:
                _safe_private_file(fd); os.ftruncate(fd, 0)
                remaining = memoryview(raw)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0: raise JournalError("JOURNAL_WRITE_FAILED")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally: os.close(fd)
            try:
                existing = os.open("state.json", _metadata_flags(), dir_fd=self.fd)
            except FileNotFoundError: existing = None
            if existing is not None:
                try: _safe_private_file(existing)
                finally: os.close(existing)
            os.replace("state.pending", "state.json", src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        except OSError: raise JournalError("JOURNAL_WRITE_FAILED") from None


@contextmanager
def open_journal(root: Path, operation_id: str):
    """Serialize one operation, with a private no-follow journal directory."""
    try:
        parsed = UUID(operation_id)
        if str(parsed) != operation_id or parsed.version != 4: raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise JournalError("JOURNAL_ID_INVALID") from None
    if os.name != "posix": raise JournalError("JOURNAL_PLATFORM_UNSUPPORTED")
    import fcntl
    with open_material_directory(root, ()) as root_fd:
        info = os.fstat(root_fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise JournalError("JOURNAL_UNSAFE_STORAGE")
        try:
            try:
                os.mkdir(operation_id, mode=0o700, dir_fd=root_fd); os.fsync(root_fd)
            except FileExistsError: pass
            fd = os.open(operation_id, _directory_flags(), dir_fd=root_fd)
            try:
                info = os.fstat(fd)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise JournalError("JOURNAL_UNSAFE_STORAGE")
                lock_fd = os.open("operation.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=fd)
                try:
                    _safe_private_file(lock_fd)
                    try: fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError: raise JournalError("JOURNAL_BUSY") from None
                    yield FileJournal(fd)
                finally: os.close(lock_fd)
            finally: os.close(fd)
        except OSError: raise JournalError("JOURNAL_UNSAFE_STORAGE") from None
