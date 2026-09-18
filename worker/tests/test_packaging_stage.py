import hashlib
import os
import stat
from uuid import uuid4

import pytest

from app import packaging_stage
from app.inventory import inventory_material
from app.packaging_stage import PackagingStageError, stage_packaging_inputs
from app.secure_filesystem import secure_filesystem_access_supported
from test_packaging_plan import report

pytestmark = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="Linux private staging descriptors required")


@pytest.fixture
def source(tmp_path):
    materials = tmp_path / "materials"; materials.mkdir()
    workspace = tmp_path / "workspace"; workspace.mkdir(mode=0o700)
    value = report(); identity = value["inventory"]["folder_name"]; folder = materials / identity; folder.mkdir()
    # Stage tests use synthetic bytes; image decoding is a separate contract.
    for entry in value["inventory"]["entries"]:
        path = folder / entry["path"]
        if entry["kind"] == "directory": path.mkdir(parents=True, exist_ok=True)
        else: path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(("Synthetic " + entry["path"]).encode())
    (folder / "SOURCE").mkdir(); (folder / "SOURCE" / "original.sbs").write_bytes(b"Unpackaged original source")
    value["inventory"] = inventory_material(materials, (identity,))
    by_path = {entry["path"]: entry for entry in value["inventory"]["entries"]}
    for image in value["images"]: image["sha256"] = by_path[image["path"]]["sha256"]
    return materials, workspace, folder, value


def stage(source, **changes):
    materials, workspace, folder, value = source
    return stage_packaging_inputs(materials, (folder.name,), value, expected_source_revision_hash=value["inventory"]["source_revision_hash"],
        policy=value["inventory"]["policy"], workspace_root=workspace, operation_id=changes.pop("operation_id", uuid4()), **changes)


def snapshot(folder):
    return {str(path.relative_to(folder)): (path.stat().st_mtime_ns, path.stat().st_mode, path.read_bytes() if path.is_file() else None) for path in folder.rglob("*")}


def test_actual_private_copies_are_verified_read_only_and_removed_without_source_changes(source):
    _, workspace, folder, value = source; before = snapshot(folder); operation = uuid4()
    with stage(source, operation_id=operation) as staged:
        assert len(list(workspace.iterdir())) == 1 and staged.plan.source_revision_hash == value["inventory"]["source_revision_hash"]
        assert not (workspace / ("packaging-" + str(operation)) / "inputs" / "SOURCE").exists()
        for entry in value["inventory"]["entries"]:
            if entry["kind"] != "file" or entry["path"].startswith("SOURCE/"): continue
            with staged.open_input(entry["path"]) as fd:
                assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o400
                assert hashlib.sha256(os.read(fd, 65536)).hexdigest() == entry["sha256"]
                with pytest.raises(OSError): os.write(fd, b"forbidden")
    assert list(workspace.iterdir()) == [] and snapshot(folder) == before
    with pytest.raises(PackagingStageError, match="STAGE_CLOSED"):
        with staged.open_input("metadata.txt"): pass


@pytest.mark.parametrize("path", ["../metadata.txt", "/metadata.txt", "SOURCE/original.sbs", "not-planned"])
def test_consumer_cannot_open_unplanned_inputs(source, path):
    with stage(source) as staged:
        with pytest.raises(PackagingStageError, match="INPUT_NOT_PLANNED"):
            with staged.open_input(path): pass


def test_consumer_error_cleans_only_its_own_workspace(source):
    _, workspace, folder, _ = source; outside = workspace / "unrelated"; outside.mkdir(); marker = outside / "keep"; marker.write_text("Keep")
    before = snapshot(folder)
    with pytest.raises(RuntimeError, match="Synthetic consumer failure"):
        with stage(source): raise RuntimeError("Synthetic consumer failure")
    assert marker.read_text() == "Keep" and list(workspace.iterdir()) == [outside] and snapshot(folder) == before


@pytest.mark.parametrize("change", ["map", "metadata", "source", "add"])
def test_changed_source_is_rejected_before_staging(source, change):
    _, workspace, folder, value = source
    target = folder / (value["images"][0]["path"] if change == "map" else "metadata.txt" if change == "metadata" else "SOURCE/original.sbs" if change == "source" else "new.txt")
    target.write_bytes(b"Changed synthetic source")
    with pytest.raises(PackagingStageError, match="SOURCE_CHANGED"):
        with stage(source): pytest.fail("Stale input accepted")
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "fifo"])
def test_unsafe_source_entries_never_reach_the_workspace(source, unsafe):
    _, workspace, folder, _ = source; target = folder / "metadata.txt"; original = target.read_bytes(); target.unlink()
    outside = workspace.parent / "outside.txt"; outside.write_bytes(original)
    if unsafe == "symlink": target.symlink_to(outside)
    elif unsafe == "hardlink": os.link(outside, target)
    else: os.mkfifo(target)
    with pytest.raises(PackagingStageError):
        with stage(source): pytest.fail("Unsafe source accepted")
    assert outside.read_bytes() == original and list(workspace.iterdir()) == []


def test_no_reuse_or_deletion_of_an_existing_workspace(source):
    operation = uuid4(); _, workspace, _, _ = source; existing = workspace / ("packaging-" + str(operation)); existing.mkdir(); (existing / "keep").write_text("Keep")
    with pytest.raises(PackagingStageError, match="ALREADY_EXISTS"):
        with stage(source, operation_id=operation): pass
    assert (existing / "keep").read_text() == "Keep"


@pytest.mark.parametrize("defect", ["public", "link", "inside-source", "parent-of-source"])
def test_workspace_root_must_be_private_disjoint_and_unlinked(source, defect):
    materials, workspace, folder, value = source
    if defect == "public": workspace.chmod(0o755)
    elif defect == "link": link = workspace.parent / "link"; link.symlink_to(workspace, target_is_directory=True); workspace = link
    elif defect == "inside-source": workspace = folder / "staging"; workspace.mkdir(mode=0o700)
    else: workspace = materials.parent
    with pytest.raises(PackagingStageError):
        with stage((materials, workspace, folder, value)): pass
    assert (folder / "metadata.txt").is_file()


def test_copy_failure_removes_partial_outputs_without_source_changes(source, monkeypatch):
    _, workspace, folder, _ = source; before = snapshot(folder)
    def fail(*args): raise OSError("Synthetic private write failure")
    monkeypatch.setattr(os, "write", fail)
    with pytest.raises(PackagingStageError, match="IO_FAILED"):
        with stage(source): pass
    assert list(workspace.iterdir()) == [] and snapshot(folder) == before


def test_copy_is_reread_and_corruption_cannot_be_returned(source, monkeypatch):
    write = os.write
    def corrupt(fd, value): return write(fd, b"x" * len(value))
    monkeypatch.setattr(os, "write", corrupt)
    with pytest.raises(PackagingStageError, match="COPY_MISMATCH"):
        with stage(source): pytest.fail("Corrupt copy accepted")
    assert list(source[1].iterdir()) == []


def test_change_to_uncopied_original_during_copy_invalidates_the_whole_stage(source, monkeypatch):
    copy = packaging_stage._copy; changed = False
    def racing(*args):
        nonlocal changed
        copy(*args)
        if not changed: changed = True; (source[2] / "SOURCE" / "original.sbs").write_bytes(b"Changed while copying")
    monkeypatch.setattr(packaging_stage, "_copy", racing)
    with pytest.raises(PackagingStageError, match="SOURCE_CHANGED"):
        with stage(source): pass
    assert list(source[1].iterdir()) == []


@pytest.mark.parametrize("change", ["rewrite", "replace-with-link", "ancestor-rename"])
def test_source_replacement_during_an_open_copy_cannot_yield_success(source, monkeypatch, change):
    copy = packaging_stage._copy; read = os.read; copying = False; changed = False
    target = source[2] / source[3]["images"][0]["path"]; original = target.read_bytes(); inode = target.stat().st_ino
    outside = source[1].parent / "outside-copy.txt"; outside.write_bytes(original)
    def racing_read(fd, count):
        nonlocal changed
        value = read(fd, count)
        if copying and not changed and os.fstat(fd).st_ino == inode and value:
            changed = True
            if change == "rewrite": target.write_bytes(b"x" * len(original))
            elif change == "replace-with-link": target.unlink(); target.symlink_to(outside)
            else: source[2].rename(source[2].with_name("renamed-owned-source"))
        return value
    def copying_now(*args):
        nonlocal copying
        copying = True
        try: return copy(*args)
        finally: copying = False
    monkeypatch.setattr(packaging_stage, "_copy", copying_now); monkeypatch.setattr(os, "read", racing_read)
    with pytest.raises(PackagingStageError):
        with stage(source): pytest.fail("Raced input accepted")
    assert changed and outside.read_bytes() == original and list(source[1].iterdir()) == []


def test_staged_bytes_are_rechecked_before_consumption(source):
    operation = uuid4(); workspace = source[1] / ("packaging-" + str(operation))
    with stage(source, operation_id=operation) as staged:
        target = workspace / "inputs" / "metadata.txt"; original = target.read_bytes(); target.chmod(0o600); target.write_bytes(b"x" * len(original)); target.chmod(0o400)
        with pytest.raises(PackagingStageError, match="STAGED_INPUT_CHANGED"):
            with staged.open_input("metadata.txt"): pass
    assert list(source[1].iterdir()) == []


@pytest.mark.parametrize("replacement", ["missing", "symlink"])
def test_input_open_errors_have_fixed_codes_even_when_caught_by_consumer(source, replacement):
    operation = uuid4(); workspace = source[1] / ("packaging-" + str(operation))
    with stage(source, operation_id=operation) as staged:
        target = workspace / "inputs" / "metadata.txt"; target.unlink()
        if replacement == "symlink": target.symlink_to(source[2] / "metadata.txt")
        with pytest.raises(PackagingStageError) as failure:
            with staged.open_input("metadata.txt"): pass
        assert str(failure.value) == "PACKAGING_STAGED_INPUT_CHANGED"
    assert list(source[1].iterdir()) == []


def test_cleanup_refuses_replaced_workspace_and_preserves_the_replacement(source):
    operation = uuid4(); workspace = source[1] / ("packaging-" + str(operation)); moved = source[1] / "moved-owned-stage"
    with pytest.raises(PackagingStageError, match="CLEANUP_REQUIRED"):
        with stage(source, operation_id=operation):
            workspace.rename(moved); workspace.mkdir(); (workspace / "keep").write_text("Keep")
    assert (workspace / "keep").read_text() == "Keep" and (moved / "inputs" / "metadata.txt").is_file()


def test_cleanup_unlinks_inserted_symlink_without_following_it(source):
    operation = uuid4(); outside = source[1].parent / "keep-outside"; outside.mkdir(); marker = outside / "keep"; marker.write_text("Keep")
    with stage(source, operation_id=operation):
        (source[1] / ("packaging-" + str(operation)) / "link").symlink_to(outside, target_is_directory=True)
    assert marker.read_text() == "Keep" and list(source[1].iterdir()) == []


@pytest.mark.parametrize("limits", [{"max_bytes": 1}, {"max_seconds": 0.000001}])
def test_stage_has_explicit_byte_and_time_budgets(source, limits):
    with pytest.raises(PackagingStageError):
        with stage(source, **limits): pass
    assert list(source[1].iterdir()) == []
