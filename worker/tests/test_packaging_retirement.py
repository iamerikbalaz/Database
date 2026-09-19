"""Synthetic retirement, immutable proof, exact replay and real crash recovery."""
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from app import packaging_retirement as retirement, packaging_store as storage
from app.packaging_store import PackagingStoreError
from test_packaging_assembly import snapshot
from test_packaging_cleanup import cleanup, incomplete
from test_packaging_store import REQUEST, SyntheticCrash, open_file, ready_bundle, retain, recover, runtime


@pytest.fixture
def accepted(ready_bundle):
    result = retain(ready_bundle)
    root = ready_bundle[1]; info = root.stat()
    arguments = dict(artifact_root=root, operation_id=result.operation_id, request_hash_value=REQUEST,
        plan_hash=result.plan_hash, expected_proof_sha256=result.proof_sha256, retirement_id=uuid4(),
        expected_root_identity=(info.st_dev, info.st_ino))
    return ready_bundle, result, arguments


def record(accepted):
    return json.loads((accepted[0][1] / str(accepted[1].operation_id) / "state.json").read_text())


def stopped_after_intent(accepted, monkeypatch):
    write = retirement.storage.FileJournal.write
    def stopped(self, value):
        write(self, value)
        if value.get("kind") == "PBR_PACKAGED_RETIREMENT" and value["status"] == "REMOVING": raise SyntheticCrash()
    with monkeypatch.context() as patch:
        patch.setattr(retirement.storage.FileJournal, "write", stopped)
        with pytest.raises(SyntheticCrash): retirement.retire_packages(**accepted[2])


def test_retirement_removes_only_retained_copies_and_preserves_exact_receipt_and_original_proof(accepted):
    fixture, result, arguments = accepted
    source = snapshot(fixture[0][2]); original = record(accepted)
    receipt = retirement.retire_packages(**arguments)
    assert receipt == {"schema_version": 1, "status": "REMOVED", "operation_id": str(result.operation_id),
        "request_hash": REQUEST, "plan_hash": result.plan_hash, "proof_sha256": result.proof_sha256,
        "retirement_id": str(arguments["retirement_id"]),
        "retirement_request_hash": retirement.request_hash(result.operation_id, REQUEST, result.plan_hash,
            result.proof_sha256, arguments["retirement_id"]),
        "file_count": len(result.payload["files"]), "byte_count": sum(item["size"] for item in result.payload["files"])}
    operation = fixture[1] / str(result.operation_id)
    assert {item.name for item in operation.iterdir()} == {"operation.lock", "state.json"}
    assert record(accepted)["retained"] == original and record(accepted)["receipt"] == receipt
    assert snapshot(fixture[0][2]) == source
    journal = (operation / "state.json").read_bytes()
    assert retirement.retire_packages(**arguments) == receipt and (operation / "state.json").read_bytes() == journal
    receipt["file_count"] = -1
    assert retirement.retire_packages(**arguments)["file_count"] == len(result.payload["files"])


@pytest.mark.parametrize("phase", ["REMOVING", "REMOVED"])
def test_retirement_intent_fences_old_reads_recovery_retention_and_incomplete_cleanup(accepted, monkeypatch, phase):
    fixture, result, arguments = accepted
    if phase == "REMOVING": stopped_after_intent(accepted, monkeypatch)
    else: retirement.retire_packages(**arguments)
    before = snapshot(fixture[1])
    for operation in (lambda: recover(fixture), lambda: retain(fixture), lambda: cleanup(fixture)):
        with pytest.raises(PackagingStoreError, match="^PACKAGING_STORE_RETIRED$"): operation()
    with pytest.raises(PackagingStoreError, match="^PACKAGING_STORE_RETIRED$"):
        with open_file(fixture, result, "metadata.json"): pytest.fail("Retired bytes must not be served")
    assert snapshot(fixture[1]) == before


@pytest.mark.parametrize("field", ["retirement_id", "request_hash_value", "plan_hash", "expected_proof_sha256", "expected_root_identity"])
def test_changed_retirement_binding_preserves_existing_intent_and_files(accepted, monkeypatch, field):
    stopped_after_intent(accepted, monkeypatch); before = snapshot(accepted[0][1])
    value = uuid4() if field == "retirement_id" else (0, 0) if field == "expected_root_identity" else "b" * 64
    with pytest.raises(PackagingStoreError): retirement.retire_packages(**{**accepted[2], field: value})
    assert snapshot(accepted[0][1]) == before


@pytest.mark.parametrize("change", ["missing", "extra", "bytes", "file-replaced", "directory-replaced", "symlink", "hardlink"])
def test_unproven_ready_output_cannot_create_retirement_intent(accepted, change):
    fixture, result, arguments = accepted
    directory = fixture[1] / str(result.operation_id) / "ready"; target = directory / "metadata.json"
    if change == "missing": target.unlink()
    elif change == "extra": target = directory / "unknown"; target.write_bytes(b"Keep"); target.chmod(0o400)
    elif change == "bytes": target.chmod(0o600); target.write_bytes(b"x" * target.stat().st_size); target.chmod(0o400)
    elif change == "file-replaced":
        target.unlink(); target.write_bytes(b"different"); target.chmod(0o400)
    elif change == "directory-replaced":
        directory.rename(directory.with_name("preserved-ready")); directory.mkdir(mode=0o700)
    elif change == "symlink":
        target.unlink(); target.symlink_to(fixture[0][2] / "SOURCE" / "original.sbs")
    else:
        target.unlink(); os.link(fixture[0][2] / "SOURCE" / "original.sbs", target)
    before = snapshot(fixture[1]); source = snapshot(fixture[0][2])
    with pytest.raises(PackagingStoreError): retirement.retire_packages(**arguments)
    assert record(accepted)["version"] == 1 and snapshot(fixture[1]) == before and snapshot(fixture[0][2]) == source


@pytest.mark.parametrize("change", ["extra", "bytes", "same-bytes-new-inode", "directory-replaced", "operation-replaced", "root-replaced"])
def test_changed_remaining_tree_or_location_refuses_resumed_deletion(accepted, monkeypatch, change):
    stopped_after_intent(accepted, monkeypatch)
    fixture, result, arguments = accepted
    root = fixture[1]; operation = root / str(result.operation_id); directory = operation / "ready"
    target = directory / "metadata.json"
    if change == "extra": (directory / "unknown").write_bytes(b"Keep"); (directory / "unknown").chmod(0o400)
    elif change == "bytes": target.chmod(0o600); target.write_bytes(b"x" * target.stat().st_size); target.chmod(0o400)
    elif change == "same-bytes-new-inode":
        data = target.read_bytes(); target.rename(root / "preserved-metadata")
        target.write_bytes(data); target.chmod(0o400)
    else:
        target = directory / "PREVIEW" / "empty" if change == "directory-replaced" else operation if change == "operation-replaced" else root
        preserved = root / "preserved-empty" if change == "directory-replaced" else target.with_name("preserved-" + target.name)
        target.rename(preserved)
        if change in {"operation-replaced", "root-replaced"}:
            # A byte-identical journal cannot adopt a different physical root.
            import shutil
            shutil.copytree(preserved, target)
        else: target.mkdir(mode=0o700)
    before = snapshot(root); source = snapshot(fixture[0][2])
    with pytest.raises(PackagingStoreError): retirement.retire_packages(**arguments)
    assert snapshot(root) == before and snapshot(fixture[0][2]) == source


def test_busy_reader_prevents_retirement_and_intent_creation(accepted):
    fixture, result, arguments = accepted; original = record(accepted)
    with open_file(fixture, result, "metadata.json"):
        with pytest.raises(PackagingStoreError, match="BUSY"): retirement.retire_packages(**arguments)
    assert record(accepted) == original


def test_retirement_refuses_incomplete_output_and_unknown_operation(ready_bundle, monkeypatch):
    operation = incomplete(ready_bundle, monkeypatch)
    original = json.loads((operation / "state.json").read_text()); root = ready_bundle[1]; info = root.stat()
    arguments = dict(artifact_root=root, operation_id=ready_bundle[2].operation_id, request_hash_value=REQUEST,
        plan_hash=ready_bundle[2].plan.sha256, expected_proof_sha256=original["proof_sha256"], retirement_id=uuid4(),
        expected_root_identity=(info.st_dev, info.st_ino))
    before = snapshot(root)
    with pytest.raises(PackagingStoreError, match="NOT_READY"): retirement.retire_packages(**arguments)
    with pytest.raises(PackagingStoreError, match="NOT_FOUND"): retirement.retire_packages(**{**arguments, "operation_id": uuid4()})
    assert snapshot(root) == before


@pytest.mark.parametrize("phase", ["intent", "file", "directory", "ready-removed", "before-final", "after-final"])
def test_actual_process_death_at_each_removal_boundary_recovers_the_same_receipt(accepted, tmp_path, phase):
    fixture, result, arguments = accepted; original = record(accepted); source = snapshot(fixture[0][2])
    value = {**arguments, "artifact_root": str(arguments["artifact_root"]),
        "operation_id": str(arguments["operation_id"]), "retirement_id": str(arguments["retirement_id"])}
    config = tmp_path / "retirement-input.json"; config.write_text(json.dumps(value)); config.chmod(0o600)
    code = """
import json, os, sys
from pathlib import Path
from uuid import UUID
from app import packaging_retirement as retirement
value=json.loads(Path(sys.argv[1]).read_text()); phase=sys.argv[2]
value['artifact_root']=Path(value['artifact_root'])
value['operation_id']=UUID(value['operation_id']); value['retirement_id']=UUID(value['retirement_id'])
value['expected_root_identity']=tuple(value['expected_root_identity'])
write=retirement.storage.FileJournal.write; unlink=os.unlink; rmdir=os.rmdir
def writing(self,record):
    if record.get('kind')=='PBR_PACKAGED_RETIREMENT' and phase=='before-final' and record['status']=='REMOVED': os._exit(77)
    write(self,record)
    if record.get('kind')=='PBR_PACKAGED_RETIREMENT':
        if phase=='intent' and record['status']=='REMOVING' or phase=='after-final' and record['status']=='REMOVED': os._exit(77)
def removing_file(*args,**kwargs):
    unlink(*args,**kwargs)
    if phase=='file': os._exit(77)
def removing_directory(name,*args,**kwargs):
    rmdir(name,*args,**kwargs)
    if phase=='directory' or phase=='ready-removed' and name=='ready': os._exit(77)
retirement.storage.FileJournal.write=writing; retirement.os.unlink=removing_file; retirement.os.rmdir=removing_directory
retirement.retire_packages(**value)
raise SystemExit(2)
"""
    process = subprocess.run([sys.executable, "-c", code, str(config), phase], cwd="/app",
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    assert process.returncode == 77 and record(accepted)["retained"] == original
    receipt = retirement.retire_packages(**arguments)
    assert receipt["status"] == "REMOVED" and record(accepted)["receipt"] == receipt
    assert retirement.retire_packages(**arguments) == receipt and snapshot(fixture[0][2]) == source


def test_reappearance_after_completed_retirement_is_preserved_and_refused(accepted):
    retirement.retire_packages(**accepted[2])
    operation = accepted[0][1] / str(accepted[1].operation_id)
    ready = operation / "ready"; ready.mkdir(mode=0o700); (ready / "keep").write_text("Keep")
    before = (operation / "state.json").read_bytes()
    with pytest.raises(PackagingStoreError, match="TREE_CHANGED"): retirement.retire_packages(**accepted[2])
    assert (ready / "keep").read_text() == "Keep" and (operation / "state.json").read_bytes() == before


@pytest.mark.parametrize("change", ["version", "nested-version", "key", "file-signature", "files", "directory", "status", "receipt"])
def test_corrupt_retirement_record_never_authorizes_more_deletion(accepted, monkeypatch, change):
    stopped_after_intent(accepted, monkeypatch); value = record(accepted)
    if change == "version": value["version"] = True
    elif change == "nested-version": value["retained"]["version"] = 2
    elif change == "key": value["retirement"]["id"] = str(uuid4())
    elif change == "file-signature": value["retirement"]["files"][0]["signature"][3] = True
    elif change == "files": value["retirement"]["files"].pop()
    elif change == "directory": value["retirement"]["directories"][0]["identity"] = [True, 0]
    elif change == "status": value["status"] = "READY"
    else: value["receipt"] = {}
    operation = accepted[0][1] / str(accepted[1].operation_id)
    (operation / "state.json").write_text(json.dumps(value)); before = snapshot(operation)
    with pytest.raises(PackagingStoreError): retirement.retire_packages(**accepted[2])
    assert snapshot(operation) == before


def test_verification_timeout_leaves_ready_output_and_original_journal_untouched(accepted, monkeypatch):
    before = snapshot(accepted[0][1])
    def expired(self): raise PackagingStoreError("PACKAGING_STORE_TIME_LIMIT")
    with monkeypatch.context() as patch:
        patch.setattr(storage._Deadline, "check", expired)
        with pytest.raises(PackagingStoreError, match="TIME_LIMIT"): retirement.retire_packages(**accepted[2])
    assert snapshot(accepted[0][1]) == before
