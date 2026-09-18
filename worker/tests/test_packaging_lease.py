"""Real OS locks and an orphaned descendant; no live files, network or database."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from app.packaging_lease import PackagingLeaseError, inherited_lease_fds, open_execution_lease

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux execution leases required")


@contextmanager
def private_directory(path):
    path.mkdir(mode=0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: yield fd
    finally: os.close(fd)


def test_exclusive_nonblocking_lock_lifetime_and_context_reset(tmp_path):
    import fcntl
    with private_directory(tmp_path / "journal") as root:
        assert inherited_lease_fds((42,)) == (42,)
        with open_execution_lease(root, create=True) as lease:
            assert inherited_lease_fds((42,)) == (42, lease.fd)
            assert os.get_inheritable(lease.fd) is False
            other = os.open("execution.lock", os.O_RDWR, dir_fd=root)
            try:
                with pytest.raises(BlockingIOError): fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally: os.close(other)
            with pytest.raises(PackagingLeaseError, match="ALREADY_ACTIVE"):
                with open_execution_lease(root, expected_identity=lease.identity): pass
        assert inherited_lease_fds((42,)) == (42,)
        with open_execution_lease(root, expected_identity=lease.identity): pass


@pytest.mark.parametrize("defect", ["symlink", "hardlink", "public", "nonempty", "readonly", "fifo"])
def test_existing_unsafe_lock_is_refused_without_overwriting(tmp_path, defect):
    with private_directory(tmp_path / "journal") as root:
        target = tmp_path / "protected"; target.write_bytes(b"Untouched")
        lock = tmp_path / "journal" / "execution.lock"
        if defect == "symlink": lock.symlink_to(target)
        elif defect == "hardlink": os.link(target, lock)
        elif defect == "fifo": os.mkfifo(lock, 0o600)
        else:
            lock.write_bytes(b"Existing" if defect == "nonempty" else b"")
            lock.chmod(0o644 if defect == "public" else 0o400 if defect == "readonly" else 0o600)
        with pytest.raises(PackagingLeaseError):
            with open_execution_lease(root, expected_identity=(0, 0, 0)): pass
        assert target.read_bytes() == b"Untouched" and lock.lstat()
        if defect == "nonempty": assert lock.read_bytes() == b"Existing"


def test_unknown_existing_lock_is_not_adopted_and_replacement_is_detected(tmp_path):
    with private_directory(tmp_path / "journal") as root:
        with open_execution_lease(root, create=True) as lease: pass
        with pytest.raises(PackagingLeaseError, match="EXISTS"):
            with open_execution_lease(root, create=True): pass
        lock = tmp_path / "journal" / "execution.lock"
        lock.rename(lock.with_name("preserved.lock"))
        lock.write_bytes(b""); lock.chmod(0o600)
        with pytest.raises(PackagingLeaseError, match="CHANGED"):
            with open_execution_lease(root, expected_identity=lease.identity): pass
        assert lock.with_name("preserved.lock").exists()


def test_replaced_lock_during_lease_fails_even_with_its_original_descriptor(tmp_path):
    with private_directory(tmp_path / "journal") as root:
        with pytest.raises(PackagingLeaseError, match="CHANGED"):
            with open_execution_lease(root, create=True):
                lock = tmp_path / "journal" / "execution.lock"
                lock.rename(lock.with_name("preserved.lock"))
                lock.write_bytes(b""); lock.chmod(0o600)


def test_changed_lock_cannot_be_passed_to_a_descendant(tmp_path):
    with private_directory(tmp_path / "journal") as root:
        with pytest.raises(PackagingLeaseError):
            with open_execution_lease(root, create=True) as lease:
                os.fchmod(lease.fd, 0o644)
                inherited_lease_fds()


def test_failure_closes_parent_descriptor_without_explicitly_unlocking_children(tmp_path):
    with private_directory(tmp_path / "journal") as root:
        with pytest.raises(RuntimeError, match="synthetic"):
            with open_execution_lease(root, create=True) as lease:
                raise RuntimeError("synthetic")
        assert inherited_lease_fds() == ()
        with open_execution_lease(root, expected_identity=lease.identity): pass


def test_private_directory_is_required(tmp_path):
    with private_directory(tmp_path / "journal") as root:
        os.fchmod(root, 0o755)
        with pytest.raises(PackagingLeaseError):
            with open_execution_lease(root, create=True): pass
        assert not (tmp_path / "journal" / "execution.lock").exists()


def test_killed_supervisor_leaves_descendant_lock_until_descendant_exits(tmp_path):
    import fcntl
    root = tmp_path / "journal"; root.mkdir(mode=0o700)
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    control_read, control_write = os.pipe()
    supervisor = r"""
import json, os, subprocess, sys
from app.packaging_lease import open_execution_lease, inherited_lease_fds
root = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
ready, release, control = map(int, sys.argv[2:])
with open_execution_lease(root, create=True) as lease:
    child = subprocess.Popen([sys.executable, "-c",
        "import os,sys;os.write(int(sys.argv[1]),b'R');os.read(int(sys.argv[2]),1)",
        str(ready), str(release)], pass_fds=inherited_lease_fds((ready, release)),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps(list(lease.identity)), flush=True)
    os.read(control, 1)
    child.wait(timeout=10)
os.close(root)
"""
    process = None
    try:
        process = subprocess.Popen([sys.executable, "-c", supervisor, str(root), str(ready_write), str(release_read), str(control_read)],
            pass_fds=(ready_write, release_read, control_read), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[1], env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"})
        os.close(ready_write); ready_write = None
        os.close(release_read); release_read = None
        os.close(control_read); control_read = None
        assert select.select([ready_read], [], [], 5)[0] and os.read(ready_read, 1) == b"R"
        assert select.select([process.stdout], [], [], 5)[0]
        identity = tuple(json.loads(process.stdout.readline(256)))
        process.kill(); process.wait(timeout=5)
        with private_directory(tmp_path / "other") as _:
            # A separately opened descriptor remains blocked after the supervisor
            # is gone. Releasing only its own fd must not have issued LOCK_UN.
            fd = os.open(root / "execution.lock", os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError): fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally: os.close(fd)
        os.close(release_write); release_write = None  # Child sees EOF and exits.
        deadline = time.monotonic() + 5
        while True:
            fd = os.open(root / "execution.lock", os.O_RDWR)
            try:
                try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                else: break
            finally: os.close(fd)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with open_execution_lease(directory, expected_identity=identity): pass
        finally: os.close(directory)
    finally:
        for fd in (ready_read, ready_write, release_read, release_write, control_read, control_write):
            if fd is not None: os.close(fd)
        if process is not None:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)
            process.stdout.close()
