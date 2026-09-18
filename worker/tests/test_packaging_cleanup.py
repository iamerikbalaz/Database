"""Proven incomplete retention cleanup; complete output and unknown data survive."""
import json
import os
import subprocess
import sys

import pytest

from app import packaging_store as storage
from app.packaging_store import PackagingStoreError, cleanup_incomplete_packages
from test_packaging_assembly import snapshot
from test_packaging_store import REQUEST, SyntheticCrash, ready_bundle, retain, recover, runtime


def cleanup(fixture, **changes):
    root = fixture[1]; info = root.stat()
    arguments = dict(artifact_root=root, operation_id=fixture[2].operation_id, request_hash=REQUEST,
        plan_hash=fixture[2].plan.sha256, expected_root_identity=(info.st_dev, info.st_ino))
    arguments.update(changes)
    return cleanup_incomplete_packages(**arguments)


def incomplete(fixture, monkeypatch, *, short=True):
    copy = storage._copy_file
    def stopped(directory, item, source, deadline):
        if short:
            parts = tuple(item["path"].split("/"))
            with storage._descendant(directory, parts[:-1]) as parent:
                fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                try: os.write(fd, b"short"); os.fsync(fd)
                finally: os.close(fd)
        else: copy(directory, item, source, deadline)
        raise SyntheticCrash()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_copy_file", stopped)
        with pytest.raises(SyntheticCrash): retain(fixture)
    return fixture[1] / str(fixture[2].operation_id)


@pytest.mark.parametrize("short", [True, False])
def test_proven_partial_copy_is_removed_without_losing_proof_or_retry_history(ready_bundle, monkeypatch, short):
    source_before = snapshot(ready_bundle[0][2])
    operation = incomplete(ready_bundle, monkeypatch, short=short)
    journal = (operation / "state.json").read_bytes()
    assert cleanup(ready_bundle) is None and not (operation / "incoming").exists()
    assert (operation / "state.json").read_bytes() == journal and cleanup(ready_bundle) is None
    with pytest.raises(PackagingStoreError, match="INCOMPLETE"): recover(ready_bundle)
    result = retain(ready_bundle)
    assert result.attempt == 2 and result.attempt_history == ({"attempt": 1,
        "proof_sha256": json.loads(journal)["proof_sha256"], "outcome": "INCOMPLETE"},)
    assert recover(ready_bundle) == result and snapshot(ready_bundle[0][2]) == source_before


def test_absent_retention_is_not_reserved_by_cleanup(ready_bundle):
    assert cleanup(ready_bundle) is None and list(ready_bundle[1].iterdir()) == []


def test_complete_incoming_output_recovers_without_deleting_any_delivered_file(ready_bundle, monkeypatch):
    def stopped(*args): raise SyntheticCrash()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "rename_noreplace", stopped)
        with pytest.raises(SyntheticCrash): retain(ready_bundle)
    operation = ready_bundle[1] / str(ready_bundle[2].operation_id)
    before = snapshot(operation / "incoming")
    result = cleanup(ready_bundle)
    assert result is not None and result.attempt == 1
    assert snapshot(operation / "ready") == before and not (operation / "incoming").exists()
    assert cleanup(ready_bundle) == result and recover(ready_bundle) == result


def test_ready_result_and_missing_ready_result_are_never_discarded(ready_bundle):
    result = retain(ready_bundle); before = snapshot(ready_bundle[1])
    assert cleanup(ready_bundle) == result and snapshot(ready_bundle[1]) == before
    ready = ready_bundle[1] / str(result.operation_id) / "ready"
    ready.rename(ready.with_name("incoming"))
    changed = snapshot(ready_bundle[1])
    with pytest.raises(PackagingStoreError, match="ARTIFACT_MISSING"): cleanup(ready_bundle)
    assert snapshot(ready_bundle[1]) == changed


@pytest.mark.parametrize("change", ["extra", "operation-extra", "full-corrupt", "linked-file", "linked-directory", "gap"])
def test_ambiguous_or_corrupt_partial_data_is_preserved(ready_bundle, monkeypatch, change):
    operation = incomplete(ready_bundle, monkeypatch, short=False)
    incoming = operation / "incoming"
    file = next(path for path in incoming.rglob("*") if path.is_file())
    if change == "extra": (incoming / "unknown").write_text("Keep")
    elif change == "operation-extra": (operation / "unknown").write_text("Keep")
    elif change == "full-corrupt":
        file.chmod(0o600); file.write_bytes(b"x" * file.stat().st_size); file.chmod(0o400)
    elif change == "linked-file":
        file.unlink(); file.symlink_to(ready_bundle[0][2] / "SOURCE" / "original.sbs")
    elif change == "linked-directory":
        incoming.rename(operation / "preserved-incoming")
        incoming.symlink_to(ready_bundle[0][2], target_is_directory=True)
    else:
        state = json.loads((operation / "state.json").read_text())
        state["status"] = "RESERVED"; state["directory_identity"] = None
        (operation / "state.json").write_text(json.dumps(state))
    before = snapshot(operation); source = snapshot(ready_bundle[0][2])
    with pytest.raises(PackagingStoreError): cleanup(ready_bundle)
    assert snapshot(operation) == before and snapshot(ready_bundle[0][2]) == source


@pytest.mark.parametrize("binding", ["request_hash", "plan_hash", "expected_root_identity"])
def test_wrong_cleanup_binding_cannot_remove_partial_output(ready_bundle, monkeypatch, binding):
    operation = incomplete(ready_bundle, monkeypatch); before = snapshot(operation)
    value = (0, 0) if binding == "expected_root_identity" else "b" * 64
    with pytest.raises(PackagingStoreError, match="CONFLICT|CHANGED"): cleanup(ready_bundle, **{binding: value})
    assert snapshot(operation) == before


def test_busy_retention_operation_refuses_cleanup(ready_bundle, monkeypatch):
    operation = incomplete(ready_bundle, monkeypatch); before = snapshot(operation)
    with storage._operation(ready_bundle[1], ready_bundle[2].operation_id, create=False):
        with pytest.raises(PackagingStoreError, match="BUSY"): cleanup(ready_bundle)
    assert snapshot(operation) == before


@pytest.mark.parametrize("replacement", ["root", "operation", "lock"])
def test_location_or_lock_replacement_before_removal_preserves_both_trees(ready_bundle, monkeypatch, replacement):
    operation = incomplete(ready_bundle, monkeypatch)
    original = storage._remove_owned; changed = []
    def replaced(*args, **kwargs):
        target = ready_bundle[1] if replacement == "root" else operation if replacement == "operation" else operation / "operation.lock"
        preserved = target.with_name("preserved-owned-" + target.name); target.rename(preserved)
        if replacement == "lock": target.write_bytes(b""); target.chmod(0o600)
        else: target.mkdir(mode=0o700); (target / "keep").write_text("Keep")
        changed.append((target, preserved))
        return original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_remove_owned", replaced)
        with pytest.raises(PackagingStoreError, match="CHANGED"): cleanup(ready_bundle)
    target, preserved = changed[0]
    assert preserved.exists() and target.exists()
    if replacement != "lock": assert (target / "keep").read_text() == "Keep"
    old_operation = preserved / str(ready_bundle[2].operation_id) if replacement == "root" else preserved if replacement == "operation" else operation
    assert (old_operation / "incoming").is_dir()


def test_expired_verification_budget_preserves_unproven_partial_copy(ready_bundle, monkeypatch):
    operation = incomplete(ready_bundle, monkeypatch); before = snapshot(operation)
    def expired(self): raise PackagingStoreError("PACKAGING_STORE_TIME_LIMIT")
    with monkeypatch.context() as patch:
        patch.setattr(storage._Deadline, "check", expired)
        with pytest.raises(PackagingStoreError, match="TIME_LIMIT"): cleanup(ready_bundle)
    assert snapshot(operation) == before


def test_real_process_death_during_partial_cleanup_recovers_and_preserves_attempt_evidence(ready_bundle, monkeypatch, tmp_path):
    operation = incomplete(ready_bundle, monkeypatch); journal = (operation / "state.json").read_bytes()
    root = ready_bundle[1]; info = root.stat()
    config = tmp_path / "cleanup-input.json"
    config.write_text(json.dumps({"root": str(root), "id": str(ready_bundle[2].operation_id),
        "plan": ready_bundle[2].plan.sha256, "identity": [info.st_dev, info.st_ino]})); config.chmod(0o600)
    code = """
import json, os, sys
from pathlib import Path
from uuid import UUID
from app import packaging_store as storage
value=json.loads(Path(sys.argv[1]).read_text()); unlink=os.unlink
def stopped(*args, **kwargs):
    unlink(*args, **kwargs)
    os._exit(76)
storage.os.unlink=stopped
storage.cleanup_incomplete_packages(artifact_root=Path(value['root']), operation_id=UUID(value['id']),
    request_hash='a'*64, plan_hash=value['plan'], expected_root_identity=tuple(value['identity']))
raise SystemExit(2)
"""
    process = subprocess.run([sys.executable, "-c", code, str(config)], cwd="/app",
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    assert process.returncode == 76 and (operation / "incoming").exists()
    assert cleanup(ready_bundle) is None and not (operation / "incoming").exists()
    assert (operation / "state.json").read_bytes() == journal
    assert retain(ready_bundle).attempt == 2
