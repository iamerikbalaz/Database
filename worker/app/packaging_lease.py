"""A Linux execution lock retained by conversion descendants after parent death."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
import stat

_ACTIVE = ContextVar("packaging_execution_lease", default=None)


class PackagingLeaseError(RuntimeError):
    """Fixed codes only; no filesystem paths or process diagnostics."""


def _check(condition, code="PACKAGING_LEASE_UNSAFE"):
    if not condition: raise PackagingLeaseError(code)


def _file(fd):
    import fcntl
    value = os.fstat(fd)
    _check(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and value.st_uid == os.geteuid()
        and stat.S_IMODE(value.st_mode) == 0o600 and value.st_size == 0
        and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR)
    return value.st_dev, value.st_ino, value.st_ctime_ns


@dataclass(frozen=True)
class ExecutionLease:
    fd: int
    identity: tuple[int, int, int]


@contextmanager
def open_execution_lease(directory_fd: int, *, create: bool = False, expected_identity: tuple[int, int, int] | None = None):
    """Caller owns the private journal directory; this function never removes it.

    Closing this context closes only our descriptor. Never issue LOCK_UN: children
    inherit the same open file description and must keep its lock until they exit.
    """
    _check(os.name == "posix", "PACKAGING_LEASE_PLATFORM_UNSUPPORTED")
    _check(type(directory_fd) is int and directory_fd >= 3 and type(create) is bool)
    _check(expected_identity is None if create else isinstance(expected_identity, tuple) and len(expected_identity) == 3
        and all(type(value) is int and value >= 0 for value in expected_identity))
    _check(_ACTIVE.get() is None, "PACKAGING_LEASE_ALREADY_ACTIVE")
    import fcntl
    fd = None; token = None
    try:
        parent = os.fstat(directory_fd)
        _check(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        if create: flags |= os.O_CREAT | os.O_EXCL
        try: fd = os.open("execution.lock", flags, 0o600, dir_fd=directory_fd)
        except FileExistsError: raise PackagingLeaseError("PACKAGING_LEASE_EXISTS") from None
        identity = _file(fd); _check(identity[0] == parent.st_dev)
        if not create: _check(identity == expected_identity, "PACKAGING_LEASE_CHANGED")
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise PackagingLeaseError("PACKAGING_LEASE_BUSY") from None
        os.fsync(fd); os.fsync(directory_fd)
        lease = ExecutionLease(fd, identity)
        token = _ACTIVE.set(lease)
        yield lease
        _check(_file(fd) == identity, "PACKAGING_LEASE_CHANGED")
        named = os.stat("execution.lock", dir_fd=directory_fd, follow_symlinks=False)
        _check((named.st_dev, named.st_ino, named.st_ctime_ns) == identity, "PACKAGING_LEASE_CHANGED")
    except PackagingLeaseError: raise
    except (OSError, ValueError): raise PackagingLeaseError("PACKAGING_LEASE_FAILED") from None
    finally:
        if token is not None: _ACTIVE.reset(token)
        if fd is not None: os.close(fd)


def inherited_lease_fds(descriptors=()):
    """Explicit pass_fds only; never add secrets or executable arguments."""
    lease = _ACTIVE.get()
    if lease is None: return descriptors
    try:
        _check(_file(lease.fd) == lease.identity, "PACKAGING_LEASE_CHANGED")
        _check(lease.fd not in descriptors)
        return (*descriptors, lease.fd)
    except PackagingLeaseError: raise
    except OSError: raise PackagingLeaseError("PACKAGING_LEASE_FAILED") from None
