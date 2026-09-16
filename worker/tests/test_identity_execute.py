import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.file_journal import JournalError, open_journal
from app.identity_execute import execute_identity_change
from app.identity_plan import plan_identity_change
from app.inventory import inventory_material
from app.secure_filesystem import secure_filesystem_access_supported
from test_identity_plan import OLD, NEW, TARGET, metadata, source

POSIX = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="Linux journaled identity execution")


@pytest.fixture
def operation(tmp_path):
    root = tmp_path / "materials"
    material = source(root)
    (material / "metadata.txt").write_text(json.dumps(metadata()), encoding="utf-8")
    nested = material / "SOURCE" / (OLD + "_sources")
    nested.mkdir(); (nested / (OLD + ".sbsar")).write_bytes(b"synthetic nested source")
    os.utime(material / "4K", ns=(1772409600000000000, 1772409600000000000))
    journals = tmp_path / "journals"; journals.mkdir(mode=0o700)
    plan = plan_identity_change(root, ("old-brand", OLD), TARGET)
    key = str(uuid4())
    return root, journals, material, plan, key


def run(case, enabled=True):
    root, journals, _, plan, key = case
    return execute_identity_change(root, journals, key, ("old-brand", OLD), TARGET, plan["plan_hash"], enabled=enabled)


def contents(root: Path):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


@POSIX
def test_confirmed_operation_preserves_uuid_independent_files_and_replays_once(operation):
    root, journals, original, plan, key = operation
    map_inode = (original / "4K" / (OLD + "_COL_4K.png")).stat().st_ino
    master_mtime = (original / "4K").stat().st_mtime_ns
    result = run(operation)
    assert result["status"] == "COMPLETED" and result["failure_code"] is None
    target = root / TARGET.path
    assert not original.exists() and target.is_dir()
    assert (target / "4K" / (NEW + "_COL_4K.png")).stat().st_ino == map_inode
    assert (target / "4K").stat().st_mtime_ns == master_mtime
    assert (target / "SOURCE" / (NEW + "_sources") / (NEW + ".sbsar")).read_bytes() == b"synthetic nested source"
    assert json.loads((target / "metadata.txt").read_text())["FOLDER"] == NEW
    assert inventory_material(root, TARGET.parts())["source_revision_hash"] == result["target_revision_hash"]
    after = contents(target)
    assert run(operation) == result and contents(target) == after
    with open_journal(journals, key) as journal:
        state = journal.read()
        assert state["status"] == "COMPLETED" and state["plan_hash"] == plan["plan_hash"]
    assert list(root.rglob(".reawote-*")) == []


@POSIX
@pytest.mark.parametrize("fault_step", range(11))
def test_every_injected_step_failure_rolls_back_names_metadata_and_timestamps(operation, monkeypatch, fault_step):
    root, _, original, plan, _ = operation
    before = contents(original)
    reached = []
    def fail(index):
        reached.append(index)
        if index == fault_step: raise OSError("SYNTHETIC_PRIVATE_IO_FAILURE")
    monkeypatch.setattr("app.identity_execute._after_step", fail)
    result = run(operation)
    assert fault_step in reached
    assert result["status"] == "ROLLED_BACK", result
    assert contents(original) == before
    assert inventory_material(root, ("old-brand", OLD))["source_revision_hash"] == plan["source_revision_hash"]
    assert not (root / TARGET.path).exists() and list(root.rglob(".reawote-*")) == []
    assert "PRIVATE" not in json.dumps(result)
    assert run(operation) == result


@POSIX
@pytest.mark.parametrize("fault_step", [0, 1, 5, 9, 10])
def test_process_interruption_recovers_from_persisted_journal(operation, monkeypatch, fault_step):
    root, _, original, _, _ = operation
    before = contents(original)
    def crash(index):
        if index == fault_step: raise SystemExit("Simulated worker process exit")
    monkeypatch.setattr("app.identity_execute._after_step", crash)
    with pytest.raises(SystemExit): run(operation)
    result = run(operation)
    assert result["status"] == "ROLLED_BACK"
    assert contents(original) == before and not (root / TARGET.path).exists()


@POSIX
def test_changed_source_or_reused_key_cannot_execute_a_different_plan(operation):
    _, _, original, _, _ = operation
    (original / "added.txt").write_bytes(b"new content")
    result = run(operation)
    assert result["status"] == "REJECTED" and result["source_revision_hash"] is None
    assert run(operation) == result
    assert original.exists()


@POSIX
def test_completed_operation_key_cannot_be_reused_for_another_request(operation):
    root, journals, _, plan, key = operation
    assert run(operation)["status"] == "COMPLETED"
    with pytest.raises(JournalError, match="JOURNAL_REQUEST_CONFLICT"):
        execute_identity_change(root, journals, key, ("old-brand", OLD), TARGET, "a" * 64, enabled=True)


@POSIX
def test_interrupted_private_preparation_does_not_claim_source_restoration(operation, monkeypatch):
    root, _, original, _, _ = operation
    before = contents(original)
    def crash(*args): raise SystemExit("Interrupted before any source action")
    monkeypatch.setattr("app.file_journal.FileJournal.backup_metadata", crash)
    with pytest.raises(SystemExit): run(operation)
    assert contents(original) == before
    (original / "external.txt").write_bytes(b"A later independent change")
    result = run(operation)
    assert result["status"] == "REJECTED" and result["source_revision_hash"] is None
    assert result["failure_code"] == "IDENTITY_PREPARATION_INTERRUPTED"
    assert (original / "external.txt").read_bytes() == b"A later independent change"
    assert not (root / TARGET.path).exists()
    assert run(operation) == result


@POSIX
def test_new_source_path_collision_during_rollback_requires_recovery_and_preserves_both(operation, monkeypatch):
    root, _, original, _, _ = operation
    def collision(index):
        if index == 0:
            original.mkdir(); (original / "do-not-overwrite.txt").write_bytes(b"external collision")
            raise OSError()
    monkeypatch.setattr("app.identity_execute._after_step", collision)
    result = run(operation)
    assert result["status"] == "RECOVERY_REQUIRED"
    assert (original / "do-not-overwrite.txt").read_bytes() == b"external collision"
    assert any(path.is_dir() for path in (root / "old-brand").glob(".reawote-identity-*"))


@POSIX
def test_partial_metadata_write_is_removed_only_by_its_recorded_inode(operation, monkeypatch):
    _, _, original, _, key = operation
    before = contents(original); write = os.write; partial = False; failed = False
    def interrupted(fd, data):
        nonlocal partial, failed
        if os.readlink(f"/proc/self/fd/{fd}").endswith(".reawote-metadata-" + key) and not failed:
            if not partial:
                partial = True
                return write(fd, data[:5])
            failed = True
            raise OSError("Simulated partial metadata write")
        return write(fd, data)
    monkeypatch.setattr(os, "write", interrupted)
    assert run(operation)["status"] == "ROLLED_BACK" and failed
    assert contents(original) == before


@POSIX
def test_concurrent_metadata_edit_is_preserved_and_requires_recovery(operation, monkeypatch):
    root, journals, _, _, key = operation
    fsync = os.fsync; changed = False
    def edit(fd):
        nonlocal changed
        name = os.readlink(f"/proc/self/fd/{fd}")
        if name.endswith(".reawote-metadata-" + key) and not changed:
            changed = True
            Path(name).with_name("metadata.txt").write_bytes(b"external metadata edit")
        return fsync(fd)
    monkeypatch.setattr(os, "fsync", edit)
    result = run(operation)
    assert result["status"] == "RECOVERY_REQUIRED" and changed
    staged = root / "old-brand" / (".reawote-identity-" + key)
    assert (staged / "metadata.txt").read_bytes() == b"external metadata edit"
    assert json.loads((journals / key / "metadata.original").read_text())["FOLDER"] == OLD


@POSIX
def test_replay_cannot_claim_an_unrelated_replacement_directory(operation):
    root, _, _, _, _ = operation
    assert run(operation)["status"] == "COMPLETED"
    target = root / TARGET.path
    target.rename(target.with_name("moved-after-completion")); target.mkdir()
    (target / "unrelated.txt").write_bytes(b"preserve")
    with pytest.raises(JournalError, match="IDENTITY_ENTRY_CHANGED"): run(operation)
    assert (target / "unrelated.txt").read_bytes() == b"preserve"


def test_source_mutations_require_explicit_opt_in_before_any_io(tmp_path):
    with pytest.raises(JournalError, match="SOURCE_MUTATIONS_DISABLED"):
        execute_identity_change(tmp_path / "missing", tmp_path / "journals", str(uuid4()), ("old-brand", OLD), TARGET, "a" * 64)
    assert not (tmp_path / "journals").exists()


def test_journal_storage_must_be_separate_from_source(tmp_path):
    with pytest.raises(JournalError, match="JOURNAL_ROOT_OVERLAPS_SOURCE"):
        execute_identity_change(tmp_path, tmp_path / "journals", str(uuid4()), ("old-brand", OLD), TARGET, "a" * 64, enabled=True)
    assert not (tmp_path / "journals").exists()
