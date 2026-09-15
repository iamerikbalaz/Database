import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.inventory import InventoryError, InventoryLimits, inventory_material
from app.secure_filesystem import secure_filesystem_access_supported


POSIX = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="secure POSIX descriptors required")


def material(root):
    path = root / "SAFE_0001_G03"
    (path / "4K").mkdir(parents=True)
    (path / "4K" / "SAFE_0001_G03_COL_4K.png").write_bytes(b"synthetic map bytes")
    (path / "metadata.txt").write_bytes(b"synthetic private source")
    return path


@POSIX
def test_inventory_hashes_contents_deterministically_without_exposing_or_changing_them(tmp_path):
    path = material(tmp_path)
    before = {str(p): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None) for p in path.rglob("*")}
    first = inventory_material(tmp_path, (path.name,))
    assert first == inventory_material(tmp_path, (path.name,))
    canonical = {key: first[key] for key in ("schema_version", "folder_name", "master_resolution", "policy", "entries")}
    assert first["source_revision_hash"] == hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    assert first["master_resolution"] == "4K"
    assert first["total_bytes"] == len(b"synthetic map bytes") + len(b"synthetic private source")
    assert "synthetic private" not in json.dumps(first)
    assert str(tmp_path) not in json.dumps(first)
    assert before == {str(p): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None) for p in path.rglob("*")}


@POSIX
@pytest.mark.parametrize("change", ["master", "metadata", "preview", "source", "add", "remove", "empty-directory", "rename"])
def test_every_source_change_invalidates_revision(tmp_path, change):
    path = material(tmp_path)
    original = inventory_material(tmp_path, (path.name,))["source_revision_hash"]
    if change == "master": (path / "4K" / "SAFE_0001_G03_COL_4K.png").write_bytes(b"changed map")
    elif change == "metadata": (path / "metadata.txt").write_bytes(b"changed metadata")
    elif change in {"preview", "source"}:
        (path / change.upper()).mkdir(); (path / change.upper() / "file.txt").write_bytes(b"auxiliary")
    elif change == "add": (path / "extra.txt").write_bytes(b"extra")
    elif change == "remove": (path / "metadata.txt").unlink()
    elif change == "empty-directory": (path / "new").mkdir()
    else: (path / "metadata.txt").rename(path / "renamed.txt")
    assert inventory_material(tmp_path, (path.name,))["source_revision_hash"] != original


@POSIX
@pytest.mark.parametrize("kind", ["file-link", "directory-link", "fifo", "hardlink", "control-name", "backslash-name"])
def test_unsafe_entries_return_no_partial_inventory(tmp_path, kind):
    path = material(tmp_path)
    outside = tmp_path / "outside"; outside.mkdir()
    secret = outside / "secret"; secret.write_bytes(b"not accessible")
    if kind == "file-link": (path / "unsafe").symlink_to(secret)
    elif kind == "directory-link": (path / "unsafe").symlink_to(outside, target_is_directory=True)
    elif kind == "fifo": os.mkfifo(path / "unsafe")
    elif kind == "hardlink": os.link(secret, path / "unsafe")
    elif kind == "control-name": (path / "bad\nname").write_bytes(b"bad")
    else: (path / "bad\\name").write_bytes(b"bad")
    response = TestClient(create_app(tmp_path)).post("/internal/material-inventory", json={"folder_path": path.name})
    assert response.status_code == 422
    assert set(response.json()) == {"detail"}
    assert "not accessible" not in response.text and str(tmp_path) not in response.text


@POSIX
@pytest.mark.parametrize("limit, value, code", [
    ("max_entries", 1, "ENTRY"), ("max_depth", 0, "DEPTH"),
    ("max_file_bytes", 1, "FILE"), ("max_total_bytes", 1, "TOTAL"),
    ("max_seconds", -1, "TIME"),
])
def test_bounded_scanning_rejects_limit_without_partial_hash(tmp_path, limit, value, code):
    path = material(tmp_path)
    with pytest.raises(InventoryError, match=f"INVENTORY_{code}_LIMIT"):
        inventory_material(tmp_path, (path.name,), limits=replace(InventoryLimits(), **{limit: value}))


@POSIX
@pytest.mark.parametrize("change", ["rewrite", "grow", "replace", "remove", "directory-rename"])
def test_change_during_read_is_detected(tmp_path, monkeypatch, change):
    path = material(tmp_path); target = path / "4K" / "SAFE_0001_G03_COL_4K.png"
    read = os.read; changed = False
    def race(fd, count):
        nonlocal changed
        chunk = read(fd, count)
        if not changed:
            changed = True
            if change == "rewrite": target.write_bytes(b"changed map content")
            elif change == "grow":
                with target.open("ab") as stream: stream.write(b"growth")
            elif change == "replace": target.unlink(); target.write_bytes(b"replacement")
            elif change == "remove": target.unlink()
            else: (path / "4K").rename(path / "8K")
        return chunk
    monkeypatch.setattr(os, "read", race)
    with pytest.raises(InventoryError, match="INVENTORY_(SOURCE_CHANGED|READ_FAILED)"):
        inventory_material(tmp_path, (path.name,))


@pytest.mark.parametrize("body", [{"folder_path": "../outside"}, {"folder_path": "/absolute"}, {"folder_path": "C:\\outside"}, {"folder_path": "x", "extra": "private"}, {"folder_path": None}])
def test_inventory_rejects_unsafe_request_before_filesystem(tmp_path, body, monkeypatch):
    monkeypatch.setattr("app.api.inventory_material", lambda *_: pytest.fail("must not inspect"))
    response = TestClient(create_app(tmp_path)).post("/internal/material-inventory", json=body)
    assert response.status_code == 422
    assert set(response.json()) == {"detail"}


@POSIX
def test_inventory_http_contract_and_unavailable_sources(tmp_path):
    path = material(tmp_path)
    client = TestClient(create_app(tmp_path))
    response = client.post("/internal/material-inventory", json={"folder_path": path.name})
    assert response.status_code == 200
    assert len(response.json()["source_revision_hash"]) == 64
    assert client.post("/internal/material-inventory", json={"folder_path": "absent"}).status_code == 404
    assert TestClient(create_app(None)).post("/internal/material-inventory", json={"folder_path": "any"}).status_code == 503


@POSIX
def test_zip_policy_boundary_changes_revision_without_changing_file_contents(tmp_path):
    path = material(tmp_path)
    old = datetime(2026, 3, 3, tzinfo=timezone.utc).timestamp()
    new = datetime(2026, 3, 5, tzinfo=timezone.utc).timestamp()
    os.utime(path / "4K", (old, old))
    first = inventory_material(tmp_path, (path.name,))
    os.utime(path / "4K", (new, new))
    second = inventory_material(tmp_path, (path.name,))
    assert first["entries"] == second["entries"]
    assert first["policy"] != second["policy"]
    assert first["source_revision_hash"] != second["source_revision_hash"]


@POSIX
def test_renamed_material_is_rejected_even_when_open_descriptor_remains_readable(tmp_path, monkeypatch):
    path = material(tmp_path)
    read = os.read; changed = False
    def race(fd, count):
        nonlocal changed
        chunk = read(fd, count)
        if not changed:
            changed = True
            path.rename(tmp_path / "moved")
            material(tmp_path)
        return chunk
    monkeypatch.setattr(os, "read", race)
    with pytest.raises(InventoryError, match="INVENTORY_SOURCE_CHANGED"):
        inventory_material(tmp_path, (path.name,))
