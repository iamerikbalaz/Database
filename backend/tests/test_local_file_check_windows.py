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
    assert command[command.index("--memory") + 1] == "4g"
    assert "type=bind,source=" + str(library.fs.root) + ",target=/materials,readonly" in command
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
    assert calls[1] == [adapter.docker, "--context", "desktop-linux", "rm", "-f", "reawote-local-check-" + "b" * 32]


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
