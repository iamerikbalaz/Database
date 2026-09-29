import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from app.local_file_check import LocalMaterialFileCheck, PROFILE
from app.local_filesystem import LocalFilesError
from app.local_materials import LocalMaterialLibrary


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows file-check adapter")


@pytest.fixture
def checker(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    library = LocalMaterialLibrary(root, tmp_path / "journal")
    docker = tmp_path / "docker.exe"
    docker.write_bytes(b"test-only executable seam")
    adapter = LocalMaterialFileCheck(library, tmp_path / "checks", docker_executable=docker,
        docker_context="desktop-linux", image="sha256:" + "a" * 64)
    library.file_checker = adapter
    return library, adapter


def folder(library, name):
    relative = "Brand/BRAND_0001_" + name + "_B01"
    material = library.fs.root / relative
    (material / "1K").mkdir(parents=True)
    source = material / "1K" / ("BRAND_0001_" + name + "_COL_1K.jpg")
    source.write_bytes(b"Read-only source test")
    return relative, source


def completed(adapter, identifier, *, defective=False):
    request = json.loads((adapter.root / identifier / "request/request.json").read_bytes())
    result = {"schema_version": 1, "results": [
        {"id": item["id"], "profile": PROFILE, "complete": True,
         "issues": ["Missing NRM"] if defective else [], "report": "Full check finished"}
        for item in reversed(request["materials"])]}
    (adapter.root / identifier / "output/result.json").write_text(json.dumps(result), encoding="utf-8")


def test_batch_launches_one_container_with_every_source_pinned_and_preserves_sources(checker, monkeypatch):
    library, adapter = checker
    first, one = folder(library, "ONE")
    second, two = folder(library, "TWO")
    calls = []
    original = [one.read_bytes(), two.read_bytes()]

    def run(identifier):
        calls.append(identifier)
        for source in (one, two):
            with pytest.raises(PermissionError):
                source.write_bytes(b"Must not write to pinned source")
        completed(adapter, identifier)

    monkeypatch.setattr(adapter, "_run_container", run)
    results = library.check_many([first, second])
    assert len(calls) == 1 and len(results) == 2
    assert library.file_check_profile == PROFILE
    assert all(item["complete"] and item["issues"] == [] for item in results)
    assert str(one.parent.parent) in results[0]["report"]
    assert str(two.parent.parent) in results[1]["report"]
    assert [one.read_bytes(), two.read_bytes()] == original
    assert sorted(p.name for p in one.parent.iterdir()) == [one.name]


def test_single_check_uses_the_same_adapter(checker, monkeypatch):
    library, adapter = checker
    relative, _ = folder(library, "ONE")
    monkeypatch.setattr(adapter, "_run_container", lambda identifier: completed(adapter, identifier, defective=True))
    assert library.check(relative)["issues"] == ["Missing NRM"]


def test_new_entry_during_check_invalidates_whole_batch(checker, monkeypatch):
    library, adapter = checker
    relative, source = folder(library, "ONE")

    def run(identifier):
        completed(adapter, identifier)
        (source.parent / "added-after-check.jpg").write_bytes(b"Late arrival")

    monkeypatch.setattr(adapter, "_run_container", run)
    with pytest.raises(LocalFilesError, match="LOCAL_SOURCE_CHANGED"):
        adapter.check_many([relative])
    assert adapter.lock.acquire(False)
    adapter.lock.release()


@pytest.mark.parametrize("selection", [[], [""], ["../escape"], ["Brand/ONE", "Brand/one"], ["x"] * 101])
def test_invalid_selections_never_launch_a_container(checker, monkeypatch, selection):
    _, adapter = checker
    calls = []
    monkeypatch.setattr(adapter, "_run_container", lambda identifier: calls.append(identifier))
    with pytest.raises((LocalFilesError, ValueError)):
        adapter.check_many(selection)
    assert not calls


def test_checker_refuses_concurrent_batch(checker, monkeypatch):
    library, adapter = checker
    relative, _ = folder(library, "ONE")
    adapter.lock.acquire()
    try:
        with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_BUSY"):
            adapter.check_many([relative])
    finally:
        adapter.lock.release()


def test_container_arguments_are_fixed_read_only_networkless_and_resource_bounded(checker, monkeypatch):
    library, adapter = checker
    calls = []
    monkeypatch.setattr("app.local_file_check.subprocess.run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0))
    adapter._run_container("a" * 32)
    command, options = calls[0]
    assert command[:4] == [adapter.docker, "--context", "desktop-linux", "run"]
    assert command[-4:] == [adapter.image, "python", "-m", "app.local_file_check"]
    assert "--read-only" in command and command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--memory") + 1] == "6g"
    assert "type=bind,source=" + str(library.fs.root) + ",target=/materials,readonly" in command
    assert "type=volume,target=/staging" in command
    assert "type=bind,source=" + str(adapter.root / "cache-key") + ",target=/cache-key,readonly" in command
    assert "type=bind,source=" + str(adapter.root / "cache") + ",target=/cache" in command
    assert "REAWOTE_CHECK_WORKERS=2" in command
    assert "REAWOTE_CHECK_CACHE_NAMESPACE=" + adapter.image in command
    assert options["timeout"] == 7200 and options["creationflags"] == 0x08000000


def test_timeout_removes_only_own_named_container_and_reports_failure(checker, monkeypatch):
    _, adapter = checker
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("app.local_file_check.subprocess.run", run)
    with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_TIME_LIMIT"):
        adapter._run_container("b" * 32)
    assert calls[1] == [adapter.docker, "--context", "desktop-linux", "rm", "-f", "-v", "reawote-local-check-" + "b" * 32]


def test_failed_container_is_never_read_as_success(checker, monkeypatch):
    _, adapter = checker
    monkeypatch.setattr("app.local_file_check.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_FAILED"):
        adapter._run_container("c" * 32)


@pytest.mark.parametrize("configuration", [
    {"image": "latest"}, {"docker_context": "remote"}, {"docker_executable": "docker.exe"},
])
def test_injection_rejects_mutable_images_remote_contexts_and_path_lookup(checker, tmp_path, configuration):
    library, adapter = checker
    options = {"docker_executable": adapter.docker, "docker_context": adapter.context, "image": adapter.image}
    options.update(configuration)
    with pytest.raises(ValueError):
        LocalMaterialFileCheck(library, tmp_path / "another-checks", **options)


def test_runtime_output_cannot_overlap_library_or_journal(checker):
    library, adapter = checker
    for root in (library.fs.root / "checks", library.journal.root / "checks", library.fs.root.parent):
        with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_ROOT_OVERLAP"):
            LocalMaterialFileCheck(library, root, docker_executable=adapter.docker,
                docker_context=adapter.context, image=adapter.image)


def test_signing_key_is_random_private_persistent_and_not_in_request(checker, monkeypatch):
    library, adapter = checker
    relative, _ = folder(library, "ONE")
    key = (adapter.root / "cache-key/private.key").read_bytes()
    assert len(key) == 32 and len(set(key)) > 8
    second = LocalMaterialFileCheck(library, adapter.root, docker_executable=adapter.docker,
        docker_context=adapter.context, image=adapter.image)
    assert (second.root / "cache-key/private.key").read_bytes() == key
    def run(identifier):
        request = (adapter.root / identifier / "request/request.json").read_bytes()
        assert key not in request and key.hex().encode() not in request
        completed(adapter, identifier)
    monkeypatch.setattr(adapter, "_run_container", run)
    assert adapter.check(relative)["complete"]


def test_invalid_existing_key_cannot_be_used_to_certify_cached_data(checker):
    library, adapter = checker
    (adapter.root / "cache-key/private.key").write_bytes(b"invalid")
    with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_KEY_INVALID"):
        LocalMaterialFileCheck(library, adapter.root, docker_executable=adapter.docker,
            docker_context=adapter.context, image=adapter.image)


def test_progress_is_sanitized_monotonic_deduplicated_and_does_not_change_results(checker, monkeypatch):
    library, adapter = checker
    first, _ = folder(library, "ONE")
    second, _ = folder(library, "TWO")
    received = []
    def run(identifier, *, progress):
        request = json.loads((adapter.root / identifier / "request/request.json").read_bytes())
        ids = [item["id"] for item in request["materials"]]
        output = adapter.root / identifier / "output/progress.json"
        output.write_text('{"private":"C:\\\\secret"}', encoding="utf-8"); progress()
        value = {"schema_version": 2, "total": 2, "completed": 0, "cache_hits": 0, "cache_misses": 1,
            "active": [{"material_id": ids[0], "material_index": 1, "file": "1K/FILE.jpg", "phase": "DECODING"}]}
        output.write_text(json.dumps(value), encoding="utf-8"); progress(); progress()
        value.update(completed=1, active=[], cache_hits=1)
        output.write_text(json.dumps(value), encoding="utf-8"); progress()
        value.update(completed=0)
        output.write_text(json.dumps(value), encoding="utf-8"); progress()
        completed(adapter, identifier)
    monkeypatch.setattr(adapter, "_run_container", run)
    results = adapter.check_many([first, second], progress=received.append)
    assert len(results) == 2 and all(result["complete"] for result in results)
    assert len(received) == 2
    assert received[0]["active"][0]["file"] == "1K/FILE.jpg"
    assert received[1]["completed"] == 1 and received[1]["cache_hits"] == 1


def test_progress_observer_failure_does_not_abort_validated_check(checker, monkeypatch):
    library, adapter = checker
    relative, _ = folder(library, "ONE")
    def run(identifier, *, progress):
        value = {"schema_version": 2, "total": 1, "completed": 1, "active": [], "cache_hits": 0, "cache_misses": 1}
        (adapter.root / identifier / "output/progress.json").write_text(json.dumps(value), encoding="utf-8")
        progress()
        completed(adapter, identifier)
    monkeypatch.setattr(adapter, "_run_container", run)
    def disconnected(value): raise RuntimeError("Observer disconnected")
    assert adapter.check(relative, progress=disconnected)["complete"] is True


def test_progress_container_polling_is_bounded_and_suppresses_process_output(checker, monkeypatch):
    _, adapter = checker
    calls = []; progress = []
    class Process:
        returncode = None
        def wait(self, *, timeout):
            calls.append(timeout)
            self.returncode = 0
            return 0
        def poll(self): return self.returncode
    def popen(command, **kwargs):
        assert kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
        return Process()
    monkeypatch.setattr("app.local_file_check.subprocess.Popen", popen)
    adapter._run_container("d" * 32, progress=lambda: progress.append(True))
    assert calls == [.5] and len(progress) == 2


def test_key_file_acl_is_protected_and_excludes_shared_user_groups(checker):
    _, adapter = checker
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    for path in (adapter.root / "cache-key", adapter.root / "cache-key/private.key"):
        size = wintypes.DWORD()
        advapi.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(size))
        descriptor = ctypes.create_string_buffer(size.value)
        assert advapi.GetFileSecurityW(str(path), 4, descriptor, len(descriptor), ctypes.byref(size))
        sddl = wintypes.LPWSTR()
        assert advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 4, ctypes.byref(sddl), None)
        try:
            value = sddl.value
            assert value.startswith("D:P") and ";;;SY)" in value
            assert value.count("(A;") == 2
            assert not any(";;;" + group + ")" in value for group in ("WD", "BU", "AU"))
        finally:
            kernel.LocalFree(sddl)


def test_progress_deadline_stops_cli_and_only_its_container(checker, monkeypatch):
    _, adapter = checker
    calls = []
    class Process:
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = -15; calls.append("terminate")
        def wait(self, *, timeout): calls.append(timeout); return self.returncode
    clock = iter([0, 7201])
    monkeypatch.setattr("app.local_file_check.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("app.local_file_check.subprocess.Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr("app.local_file_check.subprocess.run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0))
    with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_TIME_LIMIT"):
        adapter._run_container("e" * 32, progress=lambda: None)
    assert calls[:2] == ["terminate", 5]
    assert calls[2] == [adapter.docker, "--context", "desktop-linux", "rm", "-f", "-v", "reawote-local-check-" + "e" * 32]


def test_progress_wait_error_still_cleans_own_container_and_staging_volume(checker, monkeypatch):
    _, adapter = checker
    calls = []
    class Process:
        returncode = None
        def poll(self): return self.returncode
        def wait(self, *, timeout): raise OSError("wait failed")
        def terminate(self): calls.append("terminate"); self.returncode = -15
    monkeypatch.setattr("app.local_file_check.subprocess.Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr("app.local_file_check.subprocess.run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0))
    with pytest.raises(LocalFilesError, match="LOCAL_FILE_CHECK_UNAVAILABLE"):
        adapter._run_container("f" * 32, progress=lambda: None)
    assert calls == ["terminate", [adapter.docker, "--context", "desktop-linux", "rm", "-f", "-v", "reawote-local-check-" + "f" * 32]]
