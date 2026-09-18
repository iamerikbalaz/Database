"""Bounded ImageMagick conversion between already-opened private file descriptors."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from threading import BoundedSemaphore
from uuid import uuid4

from app.image_probe import MAX_PIXELS, MAX_SIDE
from app.inventory import _signature
from app.packaging_plan import MapOperation, _fit
from app.packaging_stage import PackagingStageError, _identity, _private_root, _remove_owned
from app.secure_filesystem import _directory_flags, secure_filesystem_access_supported
from app.technical_validation import probe_image
from app.packaging_lease import inherited_lease_fds

EXECUTABLE = "/usr/bin/magick-im7.q16hdri"
POLICY = Path("/etc/ImageMagick-7/policy.xml")
CONVERSION_SLOT = BoundedSemaphore(1)


class PackagingConversionError(RuntimeError):
    """Fixed failure codes; subprocess output and local paths never escape."""


def _check(condition, code="PACKAGING_CONVERSION_INVALID"):
    if not condition: raise PackagingConversionError(code)


def _environment():
    # No inherited credentials, proxy, preload, Python or ImageMagick settings.
    return {"PATH": os.defpath, "HOME": "/nonexistent", "XDG_CONFIG_HOME": "/nonexistent",
        "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1",
        "MAGICK_CONFIGURE_PATH": "/etc/ImageMagick-7", "MAGICK_TEMPORARY_PATH": ".",
        "MAGICK_THREAD_LIMIT": "2", "OMP_NUM_THREADS": "2"}


@dataclass(frozen=True)
class ConversionProof:
    sha256: str
    size: int
    width: int
    height: int
    bits: int
    format: str
    input_sha256: str
    runtime_version: str
    policy_sha256: str


def verify_runtime():
    """Fail closed unless the opt-in runtime and exact reviewed policy exist."""
    _check(secure_filesystem_access_supported(), "PACKAGING_CONVERSION_PLATFORM_UNSUPPORTED")
    try:
        expected = Path(__file__).with_name("packaging-policy.xml").read_bytes()
        with POLICY.open("rb") as stream: actual = stream.read(32769)
        _check(len(expected) <= 32768 and actual == expected, "PACKAGING_CONVERSION_POLICY_MISMATCH")
        result = subprocess.run([EXECUTABLE, "-version"], cwd="/", env=_environment(),
            pass_fds=inherited_lease_fds(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
        _check(result.returncode == 0 and len(result.stdout) <= 4096, "PACKAGING_CONVERSION_RUNTIME_UNAVAILABLE")
        version = re.search(rb"Version: ImageMagick (7\.[0-9.]+(?:-[0-9]+)?) Q16-HDRI\b", result.stdout)
        _check(version is not None, "PACKAGING_CONVERSION_RUNTIME_UNSUPPORTED")
        return version[1].decode("ascii"), hashlib.sha256(expected).hexdigest()
    except (OSError, subprocess.SubprocessError):
        raise PackagingConversionError("PACKAGING_CONVERSION_RUNTIME_UNAVAILABLE") from None


@contextmanager
def _cache(root):
    try:
        with _private_root(root) as parent:
            name = "convert-" + str(uuid4()); os.mkdir(name, 0o700, dir_fd=parent)
            fd = os.open(name, _directory_flags(), dir_fd=parent); owned = _identity(os.fstat(fd))
            try: yield fd
            finally:
                os.close(fd)
                try: _remove_owned(parent, name, owned)
                except (OSError, PackagingStageError):
                    raise PackagingConversionError("PACKAGING_CONVERSION_CLEANUP_REQUIRED") from None
    except PackagingStageError:
        raise PackagingConversionError("PACKAGING_CONVERSION_PRIVATE_CACHE_REQUIRED") from None


def _run(args, descriptors, seconds):
    process = subprocess.Popen(args, pass_fds=inherited_lease_fds(descriptors), cwd=Path(__file__).resolve().parent.parent,
        env=_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        result = process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
        raise PackagingConversionError("PACKAGING_CONVERSION_TIMEOUT") from None
    except BaseException:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
        raise
    _check(result == 0, "PACKAGING_CONVERSION_FAILED")


def convert_map(source_fd: int, target_fd: int, operation: MapOperation, *, expected_input_sha256: str,
    cache_root: Path, max_seconds: float = 150, max_bytes: int = 2 * 1024**3) -> ConversionProof:
    """Convert one RESIZE operation; verify bytes, dimensions and stored bit depth.

    Caller owns the exclusive empty output and removes it on failure. For lower
    resolutions the input must be the verified effective master, with its digest.
    This function never resolves a user-provided input/output filename.
    """
    if not CONVERSION_SLOT.acquire(blocking=False):
        raise PackagingConversionError("PACKAGING_CONVERSION_BUSY")
    try:
        return _convert_map(source_fd, target_fd, operation, expected_input_sha256=expected_input_sha256,
            cache_root=cache_root, max_seconds=max_seconds, max_bytes=max_bytes)
    finally: CONVERSION_SLOT.release()


def _convert_map(source_fd, target_fd, operation, *, expected_input_sha256, cache_root, max_seconds, max_bytes):
    _check(type(max_seconds) in {int, float} and 0 < max_seconds <= 300
        and type(max_bytes) is int and 0 < max_bytes <= 2 * 1024**3 and isinstance(cache_root, Path))
    _check(isinstance(operation, MapOperation) and operation.action == "RESIZE"
        and isinstance(operation.format, str) and operation.format in {"PNG", "JPEG", "TIFF", "WEBP"}
        and type(operation.bits) is int and operation.bits in {1, 2, 4, 8, 16} and type(operation.input_is_effective_master) is bool
        and type(operation.width) is int and type(operation.height) is int
        and 0 < operation.width <= MAX_SIDE and 0 < operation.height <= MAX_SIDE
        and operation.width * operation.height <= MAX_PIXELS
        and isinstance(expected_input_sha256, str) and re.fullmatch(r"[a-f0-9]{64}", expected_input_sha256) is not None)
    _check(operation.input_is_effective_master or expected_input_sha256 == operation.source_sha256)
    _check(type(source_fd) is int and type(target_fd) is int and source_fd >= 3 and target_fd >= 3 and source_fd != target_fd)
    deadline = time.monotonic() + max_seconds
    def remaining():
        value = deadline - time.monotonic(); _check(value > 0, "PACKAGING_CONVERSION_TIMEOUT"); return value
    try:
        version, policy_hash = verify_runtime()
        import fcntl
        source = os.fstat(source_fd); target = os.fstat(target_fd)
        _check(all(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid() for info in (source, target))
            and stat.S_IMODE(source.st_mode) == 0o400 and stat.S_IMODE(target.st_mode) == 0o600
            and source.st_size > 0 and target.st_size == 0 and _identity(source) != _identity(target)
            and fcntl.fcntl(source_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
            and fcntl.fcntl(target_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR,
            "PACKAGING_CONVERSION_UNSAFE_DESCRIPTOR")
        os.lseek(source_fd, 0, os.SEEK_SET)
        before = probe_image(source_fd, timeout=remaining())
        _check("error" not in before and before["sha256"] == expected_input_sha256
            and before["format"] == operation.format
            and (before["bits"] >= operation.bits if operation.input_is_effective_master else before["bits"] == operation.bits),
            "PACKAGING_CONVERSION_INPUT_MISMATCH")
        side = max(operation.width, operation.height)
        _check(max(before["width"], before["height"]) >= side
            and _fit(before["width"], before["height"], side) == (operation.width, operation.height),
            "PACKAGING_CONVERSION_GEOMETRY_MISMATCH")
        bits = 16 if before["bits"] == 16 else 8
        with _cache(cache_root) as cache_fd:
            os.lseek(source_fd, 0, os.SEEK_SET)
            args = [sys.executable, "-m", "app.packaging_convert_child", str(source_fd), str(target_fd), str(cache_fd),
                str(side), str(bits), str(max(1, math.ceil(min(120, remaining())))), str(max_bytes), operation.format]
            _run(args, (source_fd, target_fd, cache_fd), remaining())
        _check(_signature(os.fstat(source_fd)) == _signature(source), "PACKAGING_CONVERSION_INPUT_CHANGED")
        after = os.fstat(target_fd)
        _check(_identity(after) == _identity(target) and after.st_nlink == 1 and 0 < after.st_size <= max_bytes,
            "PACKAGING_CONVERSION_OUTPUT_INVALID")
        os.fsync(target_fd); os.lseek(target_fd, 0, os.SEEK_SET)
        verified = probe_image(target_fd, timeout=remaining())
        _check("error" not in verified and verified["width"] == operation.width and verified["height"] == operation.height
            and verified["format"] == operation.format and verified["bits"] == bits, "PACKAGING_CONVERSION_OUTPUT_MISMATCH")
        remaining(); os.fchmod(target_fd, 0o400); os.fsync(target_fd); os.lseek(target_fd, 0, os.SEEK_SET)
        return ConversionProof(verified["sha256"], after.st_size, verified["width"], verified["height"], verified["bits"],
            verified["format"], expected_input_sha256, version, policy_hash)
    except PackagingConversionError: raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise PackagingConversionError("PACKAGING_CONVERSION_IO_FAILED") from None
