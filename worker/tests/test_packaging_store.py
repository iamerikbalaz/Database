from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from app import packaging_assembly, packaging_store as storage
from app.packaging_assembly import assemble_packages
from app.packaging_convert import EXECUTABLE
from app.packaging_store import PackagingStoreError, open_retained_file, recover_packages, retain_packages
from test_packaging_assembly import make, snapshot, staged

REQUEST = "a" * 64


@pytest.fixture(autouse=True)
def runtime():
    if not Path(EXECUTABLE).is_file():
        if os.environ.get("REQUIRE_PACKAGING_RUNTIME") == "1": pytest.fail("Required packaging runtime is absent")
        pytest.skip("Opt-in Linux ImageMagick Q16-HDRI runtime required")


@pytest.fixture
def ready_bundle(tmp_path):
    source = make(tmp_path, size=(1024, 1024), master="1K"); root = tmp_path / "retained"; root.mkdir(mode=0o700)
    with staged(source) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        yield source, root, bundle


def retain(fixture, **changes):
    return retain_packages(fixture[2], artifact_root=fixture[1], request_hash=changes.pop("request_hash", REQUEST), **changes)


def recover(fixture, **changes):
    arguments = dict(artifact_root=fixture[1], operation_id=fixture[2].operation_id, request_hash=REQUEST, plan_hash=fixture[2].plan.sha256)
    arguments.update(changes)
    return recover_packages(**arguments)


def open_file(fixture, result, path, **changes):
    arguments = dict(artifact_root=fixture[1], operation_id=result.operation_id, request_hash=REQUEST, plan_hash=result.plan_hash,
        expected_proof_sha256=result.proof_sha256, path=path)
    arguments.update(changes)
    return open_retained_file(**arguments)


def test_retained_legacy_layout_proofs_and_exact_retry_survive_assembly_cleanup(tmp_path):
    source = make(tmp_path, size=(1024, 1024), master="1K"); before = snapshot(source[2]); root = tmp_path / "retained"; root.mkdir(mode=0o700)
    with staged(source) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        fixture = source, root, bundle; result = retain(fixture); repeated = retain(fixture)
        assert result == repeated and result.attempt == 1 and result.payload["bundle_sha256"] == bundle.proof_sha256
        assert result.payload["directories"] == ["PREVIEW/", "PREVIEW/empty/"]
        names = {item["path"] for item in result.payload["files"]}
        assert names == {"metadata.json", bundle.archives[0].filename, "PREVIEW/český náhled.png"}
        operation = root / str(bundle.operation_id)
        assert not (operation / "incoming").exists() and (operation / "ready" / "PREVIEW" / "empty").is_dir()
    assert list(source[1].iterdir()) == [] and snapshot(source[2]) == before
    assert recover(fixture) == result
    for item in result.payload["files"]:
        with open_file(fixture, result, item["path"]) as descriptor:
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 65536): digest.update(chunk)
            assert digest.hexdigest() == item["sha256"]
            with pytest.raises(OSError): os.write(descriptor, b"No writes")


def test_ready_retry_returns_original_result_when_new_assembly_has_different_timestamps(tmp_path, monkeypatch):
    source = make(tmp_path, size=(1024, 1024), master="1K"); root = tmp_path / "retained"; root.mkdir(mode=0o700); operation = uuid4()
    with staged(source, operation=operation) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        first = retain((source, root, bundle))
    actual_clock = packaging_assembly.time.time_ns
    monkeypatch.setattr(packaging_assembly.time, "time_ns", lambda: actual_clock() + 10_000_000_000)
    with staged(source, operation=operation) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        assert bundle.proof_sha256 != first.payload["bundle_sha256"]
        assert retain((source, root, bundle)) == first


@pytest.mark.parametrize("target", ["source", "workspace", "parent", "inside-source", "inside-workspace"])
def test_artifact_root_cannot_overlap_sources_or_ephemeral_storage(ready_bundle, target):
    source, _, bundle = ready_bundle
    root = {"source": source[0], "workspace": source[1], "parent": source[0].parent,
        "inside-source": source[0] / "nested", "inside-workspace": source[1] / "nested"}[target]
    if target.startswith("inside-"): root.mkdir(mode=0o700)
    with pytest.raises(PackagingStoreError, match="ROOT_OVERLAP"):
        retain_packages(bundle, artifact_root=root, request_hash=REQUEST)
    assert not (root / str(bundle.operation_id)).exists()


def test_conflicting_request_or_plan_cannot_reuse_an_operation(ready_bundle):
    result = retain(ready_bundle)
    with pytest.raises(PackagingStoreError, match="REQUEST_CONFLICT"): retain(ready_bundle, request_hash="b" * 64)
    with pytest.raises(PackagingStoreError, match="REQUEST_CONFLICT"): recover(ready_bundle, plan_hash="c" * 64)
    assert recover(ready_bundle) == result


def test_unknown_operation_directory_is_preserved_without_creating_lock_or_journal(ready_bundle):
    root = ready_bundle[1] / str(ready_bundle[2].operation_id); root.mkdir(mode=0o700); (root / "keep").write_text("Keep")
    with pytest.raises(PackagingStoreError, match="UNKNOWN_OPERATION"): retain(ready_bundle)
    assert [p.name for p in root.iterdir()] == ["keep"] and (root / "keep").read_text() == "Keep"


class SyntheticCrash(BaseException): pass


@pytest.mark.parametrize("point", ["before-first-copy", "partial-copy", "all-copied", "before-rename", "after-rename", "before-ready-state", "after-ready-state"])
def test_interrupted_retention_recovers_or_safely_rebuilds_known_incomplete_work(ready_bundle, monkeypatch, point):
    original_copy = storage._copy_file; rename = storage.rename_noreplace; write = storage.FileJournal.write; count = 0
    total = len(storage._payload(ready_bundle[2])["files"])
    def copying(directory, item, source, deadline):
        nonlocal count
        count += 1
        if point == "before-first-copy" and count == 1: raise SyntheticCrash()
        if point == "partial-copy" and count == 1:
            parts = tuple(item["path"].split("/"))
            with storage._descendant(directory, parts[:-1]) as parent:
                fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                try: os.write(fd, b"short"); os.fsync(fd)
                finally: os.close(fd)
            raise SyntheticCrash()
        original_copy(directory, item, source, deadline)
        if point == "all-copied" and count == total: raise SyntheticCrash()
    def renaming(*args):
        if point == "before-rename": raise SyntheticCrash()
        rename(*args)
        if point == "after-rename": raise SyntheticCrash()
    def writing(self, value):
        if point == "before-ready-state" and value["status"] == "READY": raise SyntheticCrash()
        write(self, value)
        if point == "after-ready-state" and value["status"] == "READY": raise SyntheticCrash()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_copy_file", copying); patch.setattr(storage, "rename_noreplace", renaming); patch.setattr(storage.FileJournal, "write", writing)
        with pytest.raises(SyntheticCrash): retain(ready_bundle)
    if point in {"before-first-copy", "partial-copy"}:
        with pytest.raises(PackagingStoreError, match="INCOMPLETE"): recover(ready_bundle)
        result = retain(ready_bundle); assert result.attempt == 2
        assert len(result.attempt_history) == 1 and result.attempt_history[0]["attempt"] == 1
        assert result.attempt_history[0]["outcome"] == "INCOMPLETE"
    else:
        result = recover(ready_bundle); assert result.attempt == 1
    assert recover(ready_bundle) == result
    assert not (ready_bundle[1] / str(result.operation_id) / "incoming").exists()


def test_recovery_in_a_new_process_after_real_exit_between_rename_and_ready_write(tmp_path):
    root = tmp_path / "child"; root.mkdir(mode=0o700); operation = uuid4()
    script = '''
import os, sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0, '/app/tests')
from test_packaging_assembly import make, staged
from app.packaging_assembly import assemble_packages
from app import packaging_store as storage
root = Path(sys.argv[1]); source = make(root, size=(1024, 1024), master='1K')
retained = root / 'retained'; retained.mkdir(mode=0o700)
rename = storage.rename_noreplace
def die(*args):
    rename(*args)
    os._exit(75)
storage.rename_noreplace = die
with staged(source, operation=UUID(sys.argv[2])) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone='UTC') as bundle:
    storage.retain_packages(bundle, artifact_root=retained, request_hash='a' * 64)
'''
    result = subprocess.run([sys.executable, "-c", script, str(root), str(operation)], cwd="/app",
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    assert result.returncode == 75
    state = json.loads((root / "retained" / str(operation) / "state.json").read_text())
    assert state["status"] == "BUILDING"
    recovered = recover_packages(artifact_root=root / "retained", operation_id=operation, request_hash=REQUEST, plan_hash=state["plan_hash"])
    assert recovered.proof_sha256 == state["proof_sha256"] and recovered.attempt == 1


@pytest.mark.parametrize("change", ["bytes", "missing", "symlink", "hardlink", "extra", "directory-replaced"])
def test_changed_ready_artifacts_fail_without_rewriting_or_deleting_them(ready_bundle, change, tmp_path):
    result = retain(ready_bundle); directory = ready_bundle[1] / str(result.operation_id) / "ready"; target = directory / "metadata.json"
    outside = tmp_path / "outside"; outside.write_bytes(target.read_bytes()); original = outside.read_bytes()
    if change == "bytes": target.chmod(0o600); target.write_bytes(b"x" * target.stat().st_size); target.chmod(0o400)
    elif change == "missing": target.unlink()
    elif change == "symlink": target.unlink(); target.symlink_to(outside)
    elif change == "hardlink": target.unlink(); os.link(outside, target)
    elif change == "extra": (directory / "unknown").write_text("Keep")
    else: directory.rename(directory.with_name("moved")); directory.mkdir(mode=0o700); (directory / "keep").write_text("Keep")
    with pytest.raises(PackagingStoreError): recover(ready_bundle)
    assert outside.read_bytes() == original
    if change == "extra": assert (directory / "unknown").read_text() == "Keep"
    if change == "directory-replaced": assert (directory / "keep").read_text() == "Keep"


@pytest.mark.parametrize("change", ["payload", "kind", "version", "request", "proof"])
def test_corrupt_or_conflicting_journal_is_never_trusted(ready_bundle, change):
    result = retain(ready_bundle); path = ready_bundle[1] / str(result.operation_id) / "state.json"; value = json.loads(path.read_text())
    if change == "payload": value["payload"]["files"][0]["sha256"] = "0" * 64
    elif change == "kind": value["kind"] = "OTHER_OPERATION"
    elif change == "version": value["version"] = 2
    elif change == "request": value["request_hash"] = "b" * 64
    else: value["proof_sha256"] = "0" * 64
    path.write_text(json.dumps(value)); before = path.read_bytes()
    with pytest.raises(PackagingStoreError): recover(ready_bundle)
    assert path.read_bytes() == before


@pytest.mark.parametrize("path", ["../state.json", "state.json", "/metadata.json", "PREVIEW/../metadata.json", "missing.zip"])
def test_reader_requires_exact_allowlisted_path_and_independent_proof(ready_bundle, path):
    result = retain(ready_bundle)
    with pytest.raises(PackagingStoreError, match="FILE_NOT_FOUND"):
        with open_file(ready_bundle, result, path): pass
    with pytest.raises(PackagingStoreError, match="PROOF_MISMATCH"):
        with open_file(ready_bundle, result, "metadata.json", expected_proof_sha256="0" * 64): pass


def test_operation_lock_rejects_parallel_work_and_is_released(ready_bundle):
    result = retain(ready_bundle)
    with storage._operation(ready_bundle[1], result.operation_id, create=False):
        with pytest.raises(PackagingStoreError, match="BUSY"): retain(ready_bundle)
        with pytest.raises(PackagingStoreError, match="BUSY"): recover(ready_bundle)
    assert recover(ready_bundle) == result


@pytest.mark.parametrize("limits", [{"max_bytes": 1}, {"max_seconds": .000001}])
def test_limits_fail_before_reserving_an_operation_directory(ready_bundle, limits):
    with pytest.raises(PackagingStoreError): retain(ready_bundle, **limits)
    assert list(ready_bundle[1].iterdir()) == []


@pytest.mark.parametrize("kind", ["root-symlink", "root-public", "journal-symlink", "lock-hardlink"])
def test_private_storage_and_journal_links_are_checked_without_touching_outside_data(ready_bundle, tmp_path, kind):
    result = retain(ready_bundle); root = ready_bundle[1]; operation = root / str(result.operation_id)
    outside = tmp_path / "keep-outside"; outside.write_bytes(b"Synthetic unrelated bytes"); outside.chmod(0o600)
    if kind == "root-symlink":
        root.rename(root.with_name("moved-root")); root.symlink_to(root.with_name("moved-root"), target_is_directory=True)
    elif kind == "root-public": root.chmod(0o755)
    elif kind == "journal-symlink":
        (operation / "state.json").unlink(); (operation / "state.json").symlink_to(outside)
    else:
        (operation / "operation.lock").unlink(); os.link(outside, operation / "operation.lock")
    with pytest.raises(PackagingStoreError): recover(ready_bundle)
    assert outside.read_bytes() == b"Synthetic unrelated bytes"


def test_corrupt_physical_write_is_detected_by_rereading_and_is_preserved_for_review(ready_bundle, monkeypatch):
    copy = storage._copy_file; write = os.write
    def corrupt(*args):
        with monkeypatch.context() as patch:
            patch.setattr(os, "write", lambda fd, data: write(fd, b"x" * len(data)))
            copy(*args)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_copy_file", corrupt)
        with pytest.raises(PackagingStoreError, match="ARTIFACT_CHANGED"): retain(ready_bundle)
    directory = ready_bundle[1] / str(ready_bundle[2].operation_id) / "incoming"
    before = snapshot(directory)
    with pytest.raises(PackagingStoreError, match="ARTIFACT_CHANGED"): recover(ready_bundle)
    assert snapshot(directory) == before and not directory.with_name("ready").exists()


@pytest.mark.parametrize("phase,code", [("RESERVED", "UNKNOWN_OPERATION"), ("BUILDING", "RECOVERY_REQUIRED")])
def test_unknown_reservation_gaps_are_not_deleted_or_overwritten(ready_bundle, monkeypatch, phase, code):
    write = storage.FileJournal.write
    def interrupt(self, value):
        if value["status"] == phase: raise SyntheticCrash()
        write(self, value)
    with monkeypatch.context() as patch:
        patch.setattr(storage.FileJournal, "write", interrupt)
        with pytest.raises(SyntheticCrash): retain(ready_bundle)
    operation = ready_bundle[1] / str(ready_bundle[2].operation_id); before = snapshot(operation)
    with pytest.raises(PackagingStoreError, match=code): retain(ready_bundle)
    assert snapshot(operation) == before


@pytest.mark.parametrize("replacement", ["operation", "root"])
def test_replaced_storage_directory_cannot_return_success_or_delete_replacement(ready_bundle, monkeypatch, replacement):
    copy = storage._copy_file; changed = False; root = ready_bundle[1]
    target = root / str(ready_bundle[2].operation_id) if replacement == "operation" else root
    moved = target.with_name("moved-owned-" + replacement)
    def replace_directory(*args):
        nonlocal changed
        copy(*args)
        if not changed:
            changed = True; target.rename(moved); target.mkdir(mode=0o700); (target / "keep").write_text("Keep")
    monkeypatch.setattr(storage, "_copy_file", replace_directory)
    with pytest.raises(PackagingStoreError, match="CHANGED"): retain(ready_bundle)
    assert (target / "keep").read_text() == "Keep" and moved.is_dir()


def test_retained_reader_detects_file_replacement_before_context_finishes(ready_bundle):
    result = retain(ready_bundle); path = ready_bundle[1] / str(result.operation_id) / "ready" / "metadata.json"
    with pytest.raises(PackagingStoreError, match="ARTIFACT_CHANGED"):
        with open_file(ready_bundle, result, "metadata.json") as descriptor:
            assert os.read(descriptor, 1)
            original = path.read_bytes(); path.unlink(); path.write_bytes(original); path.chmod(0o400)
