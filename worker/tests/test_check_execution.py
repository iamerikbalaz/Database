import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from app import check_execution as execution
from app.check_execution import EvidenceCache, ExecutionOptions, MaterialExecution, canonical
from app.inventory import InventoryError
from app.local_file_check import FileCheckRunError, run
from app.material_file_check import check_material_files
from test_material_file_check import make_valid, add_map, digest_tree

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux descriptor safety and isolated image decoders")
NAMESPACE = "sha256:" + "1" * 64


def options(tmp_path):
    staging = tmp_path / "stage"; staging.mkdir(mode=0o700)
    cache = tmp_path / "cache"; cache.mkdir(mode=0o700)
    key = tmp_path / "key"; key.write_bytes(b"k" * 32)
    return ExecutionOptions(staging, cache, key, NAMESPACE)


def check(root, folder, config, callback=None):
    session = MaterialExecution(config, callback)
    result = check_material_files(root, (folder.name,), execution=session)
    return result, session


@pytest.mark.parametrize("defect", [None, "wrong-ID", "legacy-COL", "missing-metadata", "broken-preview", "missing-ROUGH"])
def test_snapshot_results_exactly_match_original_check_and_cleanup(tmp_path, defect):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    if defect == "wrong-ID": add_map(folder, "ID", fmt="TIFF", mode="L")
    if defect == "legacy-COL":
        next((folder / "1K").glob("*_COL_1K.jpg")).unlink()
        add_map(folder, "COL", fmt="TIFF")
    if defect == "missing-metadata": (folder / "metadata.json").unlink()
    if defect == "broken-preview": (folder / "PREVIEW/SPHERE_1.png").write_bytes(b"broken")
    if defect == "missing-ROUGH": next((folder / "1K").glob("*_ROUGH_1K.jpg")).unlink()
    (folder / "SOURCE").mkdir(); (folder / "SOURCE/payload.bin").write_bytes(b"not checked")
    before = digest_tree(folder)
    expected = check_material_files(root, (folder.name,))
    actual, session = check(root, folder, config)
    assert actual == expected and session.misses > 0
    assert digest_tree(folder) == before
    assert list(config.staging_root.iterdir()) == []


def test_original_files_are_read_exactly_twice_snapshot_copy_and_final_hash(tmp_path, monkeypatch):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    original = os.read; reads = {}
    def tracked(fd, count):
        value = original(fd, count)
        path = os.readlink(f"/proc/self/fd/{fd}")
        if path.startswith(str(root) + "/") and value:
            reads[path] = reads.get(path, 0) + len(value)
        return value
    monkeypatch.setattr(os, "read", tracked)
    result, _ = check(root, folder, config)
    assert result["issues"] == []
    expected = {str(path): 2 * path.stat().st_size for path in folder.rglob("*") if path.is_file()}
    assert reads == expected


def test_warm_cache_avoids_decoding_but_keeps_identical_result(tmp_path, monkeypatch):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    first, _ = check(root, folder, config)
    def unexpected(*_args, **_kwargs): pytest.fail("Unchanged authenticated image must reuse complete evidence")
    monkeypatch.setattr(execution, "probe_image", unexpected)
    second, session = check(root, folder, config)
    assert second == first and session.hits == 4 and session.misses == 0


def test_changed_content_with_preserved_size_and_mtime_cannot_reuse_cache(tmp_path):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    check(root, folder, config)
    path = folder / "PREVIEW/SPHERE_1.png"; before = path.stat()
    path.write_bytes(b"X" * before.st_size)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    result, session = check(root, folder, config)
    assert session.misses == 1 and any("IMAGE_UNREADABLE" in issue for issue in result["issues"])


@pytest.mark.parametrize("changed", ["metadata.json", "PREVIEW/SPHERE_1.png", "1K/new.jpg"])
def test_final_original_verification_remains_mandatory_even_on_cache_hit(tmp_path, changed):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    check(root, folder, config); modified = False
    def mutate(event):
        nonlocal modified
        if event["phase"] == "CACHED" and not modified:
            modified = True; (folder / changed).write_bytes(b"changed after snapshot")
    with pytest.raises(InventoryError, match="SOURCE_CHANGED"):
        check(root, folder, config, mutate)
    assert list(config.staging_root.iterdir()) == []


def test_snapshot_budget_is_bounded_and_partial_snapshot_is_removed(tmp_path, monkeypatch):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    monkeypatch.setattr(execution, "STAGING_BYTES", 100)
    with pytest.raises(InventoryError, match="INVENTORY_(FILE|TOTAL)_LIMIT"):
        check(root, folder, config)
    assert list(config.staging_root.iterdir()) == []


def test_snapshot_retains_source_link_defenses(tmp_path):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    os.symlink(folder / "metadata.json", folder / "PREVIEW/linked.png")
    with pytest.raises(InventoryError, match="UNSAFE_ENTRY"):
        check(root, folder, config)
    assert list(config.staging_root.iterdir()) == []


def evidence():
    return {"width": 1024, "height": 512, "bits": 8, "format": "JPEG", "mode": "RGB", "sha256": "a" * 64}


@pytest.mark.parametrize("change", ["signature", "namespace", "version", "sha", "extra", "bool-width", "oversize", "truncated", "symlink", "hardlink"])
def test_invalid_cache_evidence_is_a_miss(tmp_path, change):
    config = options(tmp_path); cache = EvidenceCache(config.cache_root, config.key_path, config.namespace)
    value = evidence(); digest = value["sha256"]; cache.put(digest, value)
    path = config.cache_root / cache.bucket(digest) / cache.name(digest)
    record = json.loads(path.read_bytes())
    if change == "signature": record["signature"] = "0" * 64
    elif change == "namespace": record["namespace"] = "sha256:" + "2" * 64
    elif change == "version": record["version"] = "other"
    elif change == "sha": record["sha256"] = "b" * 64
    elif change == "extra": record["evidence"]["approved"] = True
    elif change == "bool-width": record["evidence"]["width"] = True
    if change in {"extra", "bool-width"}:
        record.pop("signature")
        record["signature"] = hmac.new(b"k" * 32, canonical(record), hashlib.sha256).hexdigest()
    path.write_bytes(canonical(record))
    if change == "oversize": path.write_bytes(b"x" * 5000)
    elif change == "truncated": path.write_bytes(b'{"schema_version":')
    elif change == "symlink":
        other = tmp_path / "record"; path.rename(other); os.symlink(other, path)
    elif change == "hardlink": os.link(path, tmp_path / "record")
    assert cache.get(digest) is None


def test_corrupt_cache_falls_back_to_actual_decode(tmp_path):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    expected, _ = check(root, folder, config)
    for path in config.cache_root.glob("*/*.json"): path.write_bytes(b"corrupt")
    actual, session = check(root, folder, config)
    # COL and NRM have identical fixture bytes; the repaired first record can
    # already serve the second image inside this same material.
    assert actual == expected and session.misses >= 3


def test_unsafe_key_disables_cache_instead_of_trusting_unsigned_facts(tmp_path):
    config = options(tmp_path); real = tmp_path / "real-key"
    config.key_path.rename(real); os.symlink(real, config.key_path)
    cache = EvidenceCache(config.cache_root, config.key_path, config.namespace)
    cache.put(evidence()["sha256"], evidence())
    assert cache.key is None and cache.get(evidence()["sha256"]) is None


def test_namespace_change_is_a_cache_miss(tmp_path):
    config = options(tmp_path); value = evidence()
    cache = EvidenceCache(config.cache_root, config.key_path, config.namespace); cache.put(value["sha256"], value)
    changed = EvidenceCache(config.cache_root, config.key_path, "sha256:" + "3" * 64)
    assert changed.get(value["sha256"]) is None


def test_cache_bucket_saturation_does_not_grow_or_block_checking(tmp_path, monkeypatch):
    config = options(tmp_path); value = evidence()
    cache = EvidenceCache(config.cache_root, config.key_path, config.namespace)
    bucket = config.cache_root / cache.bucket(value["sha256"]); bucket.mkdir()
    monkeypatch.setattr(execution, "CACHE_BUCKET_RECORDS", 2)
    for name in ("a.json", "b.json"): (bucket / name).write_bytes(b"invalid evidence")
    before = {path.name for path in bucket.iterdir()}
    cache.put(value["sha256"], value)
    assert cache.get(value["sha256"]) is None
    assert {path.name for path in bucket.iterdir()} == before | {".lock"}


def test_cache_write_is_atomic_and_cleans_temporary_on_failure(tmp_path, monkeypatch):
    config = options(tmp_path); value = evidence()
    cache = EvidenceCache(config.cache_root, config.key_path, config.namespace)
    def failed(*_args, **_kwargs): raise OSError("disk failure")
    monkeypatch.setattr(os, "replace", failed)
    cache.put(value["sha256"], value)
    assert cache.get(value["sha256"]) is None
    assert not list(config.cache_root.glob("*/.write-*"))


def test_cache_bucket_symlink_is_not_followed(tmp_path):
    config = options(tmp_path); value = evidence()
    cache = EvidenceCache(config.cache_root, config.key_path, config.namespace)
    other = tmp_path / "outside"; other.mkdir()
    os.symlink(other, config.cache_root / cache.bucket(value["sha256"]))
    cache.put(value["sha256"], value)
    assert cache.get(value["sha256"]) is None and list(other.iterdir()) == []


def test_busy_cache_lock_skips_store_without_waiting(tmp_path):
    import fcntl
    config = options(tmp_path); value = evidence()
    cache = EvidenceCache(config.cache_root, config.key_path, config.namespace)
    bucket = config.cache_root / cache.bucket(value["sha256"]); bucket.mkdir()
    with (bucket / ".lock").open("wb") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cache.put(value["sha256"], value)
    assert cache.get(value["sha256"]) is None


def test_parallel_processes_keep_order_progress_bounds_and_identical_findings(tmp_path):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    second = root / "ROUBAL_0002_OTHER_B01"; shutil.copytree(folder, second)
    # This second material intentionally has old map names; issues must match.
    expected = [check_material_files(root, (path.name,)) for path in (folder, second)]
    identifiers = [str(uuid4()), str(uuid4())]; progress = []
    request = {"schema_version": 1, "materials": [{"id": identifier, "folder_path": path.name}
        for identifier, path in zip(identifiers, (folder, second))]}
    result = run(root, request, execution_options=config, workers=2, progress=progress.append)
    assert [item["id"] for item in result["results"]] == identifiers
    assert [item["issues"] for item in result["results"]] == [item["issues"] for item in expected]
    assert any(len(item["active"]) == 2 for item in progress)
    assert all(item["schema_version"] == 2 and len(item["active"]) <= 2 for item in progress)
    assert progress[-1]["completed"] == 2 and progress[-1]["active"] == []
    assert list(config.staging_root.iterdir()) == []


def test_parallel_failure_never_returns_partial_results_and_cleans_both_snapshots(tmp_path):
    root, folder = make_valid(tmp_path); config = options(tmp_path)
    identifiers = [str(uuid4()), str(uuid4())]
    request = {"schema_version": 1, "materials": [
        {"id": identifiers[0], "folder_path": folder.name}, {"id": identifiers[1], "folder_path": "MISSING"}]}
    with pytest.raises(FileCheckRunError) as raised:
        run(root, request, execution_options=config, workers=2)
    assert raised.value.code == "MATERIAL_FOLDER_NOT_FOUND" and raised.value.material_id == identifiers[1]
    assert list(config.staging_root.iterdir()) == []


def test_progress_coalesces_file_events_but_always_publishes_final_counts(tmp_path, monkeypatch):
    from app import local_file_check as command
    root, folder = make_valid(tmp_path); config = options(tmp_path); progress = []
    # Spawned material processes have their own real clocks; the coordinator's
    # constant clock simulates all intermediate events in one throttle window.
    monkeypatch.setattr(command.time, "monotonic", lambda: 100.0)
    request = {"schema_version": 1, "materials": [{"id": str(uuid4()), "folder_path": folder.name}]}
    result = run(root, request, execution_options=config, workers=1, progress=progress.append)
    assert result["results"][0]["issues"] == []
    assert len(progress) == 2 and progress[0]["completed"] == 0 and progress[-1]["completed"] == 1
    assert progress[-1]["cache_hits"] + progress[-1]["cache_misses"] == 4
