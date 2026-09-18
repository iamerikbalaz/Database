from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app import packaging_convert as conversion
from app.packaging_lease import PackagingLeaseError, open_execution_lease, inherited_lease_fds
from test_packaging_convert import image_files, run, required_runtime  # actual opt-in runtime fixture


def test_actual_conversion_passes_the_execution_lease_to_all_four_children(tmp_path, monkeypatch):
    directory = tmp_path / "journal"; directory.mkdir(mode=0o700)
    root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    real = subprocess.Popen; calls = []
    try:
        with open_execution_lease(root, create=True) as lease:
            def spawn(*args, **kwargs):
                assert lease.fd in kwargs["pass_fds"]
                calls.append(args)
                return real(*args, **kwargs)
            monkeypatch.setattr(subprocess, "Popen", spawn)
            with image_files(tmp_path) as files: assert run(files).bits == 8
        assert len(calls) == 4  # Runtime, source probe, actual conversion, result probe.
    finally: os.close(root)


def test_actual_imagemagick_exec_retains_lock_while_waiting_for_source_bytes(tmp_path):
    # Exercise the fixed child executable's inheritance directly. The public
    # conversion API still refuses pipes and requires verified regular sources.
    with ExitStack() as cleanup:
        directory = tmp_path / "journal"; directory.mkdir(mode=0o700)
        root = os.open(directory, os.O_RDONLY | os.O_DIRECTORY); cleanup.callback(os.close, root)
        cache = tmp_path / "cache"; cache.mkdir(mode=0o700)
        cache_fd = os.open(cache, os.O_RDONLY | os.O_DIRECTORY); cleanup.callback(os.close, cache_fd)
        source, release = os.pipe(); cleanup.callback(os.close, source)
        target = os.open(tmp_path / "target", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600); cleanup.callback(os.close, target)
        child = None
        try:
            with open_execution_lease(root, create=True) as lease:
                args = [sys.executable, "-m", "app.packaging_convert_child", str(source), str(target), str(cache_fd),
                    "1024", "8", "5", str(1024**2), "PNG"]
                child = subprocess.Popen(args, pass_fds=inherited_lease_fds((source, target, cache_fd)),
                    cwd=Path(__file__).resolve().parents[1], env=conversion._environment(),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 5
                while True:
                    assert child.poll() is None
                    if os.path.realpath("/proc/" + str(child.pid) + "/exe") == os.path.realpath(conversion.EXECUTABLE): break
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                inherited = os.stat("/proc/" + str(child.pid) + "/fd/" + str(lease.fd))
                assert (inherited.st_dev, inherited.st_ino) == lease.identity[:2]
            with pytest.raises(PackagingLeaseError, match="BUSY"):
                with open_execution_lease(root, expected_identity=lease.identity): pass
            os.close(release); release = None  # EOF finishes the invalid image safely.
            assert child.wait(timeout=10) != 0
            with open_execution_lease(root, expected_identity=lease.identity): pass
        finally:
            if release is not None: os.close(release)
            if child is not None:
                if child.poll() is None: child.kill()
                child.wait(timeout=10)
