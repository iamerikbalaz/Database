import json
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.folder_discovery import DISCOVERY_SLOTS, DiscoveryError, DiscoveryLimits, discover_folders, discovery_parts
from app.secure_filesystem import secure_filesystem_access_supported

POSIX = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="secure Linux directory discovery required")


@pytest.mark.parametrize("value", ["/", ".", "..", "../private", "a/../b", "a//b", "a/", "C:/private", "a\\b", "a\x00b", "a\nb", "a\ud800b", "/".join(["x"] * 17), "é" * 128, "a" * 2049])
def test_invalid_paths_fail_before_filesystem(value, monkeypatch, tmp_path):
    monkeypatch.setattr("app.folder_discovery.open_material_directory", lambda *_: pytest.fail("Invalid path reached filesystem"))
    with pytest.raises(ValueError): discovery_parts(value)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/internal/folder-discovery", content=json.dumps({"parent_path": value}), headers={"Content-Type": "application/json"})
        assert response.status_code == 422 and response.json() == {"detail": {"code": "INVALID_FOLDER_PATH"}}


@POSIX
def test_one_level_listing_never_opens_files_or_scans_children(tmp_path, monkeypatch):
    parent = tmp_path / "Library"; parent.mkdir()
    (parent / "SAFE_0001_G03").mkdir(); (parent / "Other").mkdir()
    (parent / "SAFE_0001_G03" / "PRIVATE_CHILD").mkdir()
    source = parent / "private.txt"; source.write_text("PRIVATE_SYNTHETIC_CONTENT")
    original = (source.read_bytes(), source.stat().st_mtime_ns)
    (parent / "linked").symlink_to(parent / "Other", target_is_directory=True)
    (parent / "unsafe:name").mkdir(); os.mkfifo(parent / "pipe")
    actual_open = os.open
    def open_directory(*args, **kwargs):
        assert args[1] & os.O_DIRECTORY
        return actual_open(*args, **kwargs)
    monkeypatch.setattr("app.folder_discovery.os.open", open_directory)
    with TestClient(create_app(tmp_path)) as client:
        result = client.post("/internal/folder-discovery", json={"parent_path": "Library"})
        assert result.status_code == 200
        assert result.json() == {"schema_version": 1, "parent_path": "Library", "omitted_entries": 4,
            "directories": [{"name": "Other", "path": "Library/Other"}, {"name": "SAFE_0001_G03", "path": "Library/SAFE_0001_G03"}]}
        assert "PRIVATE" not in result.text and str(tmp_path) not in result.text
        root = client.post("/internal/folder-discovery", json={"parent_path": ""})
        assert root.json()["directories"] == [{"name": "Library", "path": "Library"}]
    assert original == (source.read_bytes(), source.stat().st_mtime_ns)


@POSIX
@pytest.mark.parametrize("field,value,code", [("max_entries", 1, "DISCOVERY_ENTRY_LIMIT"), ("max_directories", 1, "DISCOVERY_DIRECTORY_LIMIT"), ("max_seconds", -1, "DISCOVERY_TIME_LIMIT")])
def test_limits_refuse_partial_lists(tmp_path, field, value, code):
    (tmp_path / "One").mkdir(); (tmp_path / "Two").mkdir()
    with pytest.raises(DiscoveryError, match=code):
        discover_folders(tmp_path, (), limits=replace(DiscoveryLimits(), **{field: value}))
    assert len(discover_folders(tmp_path, ())["directories"]) == 2  # slot released


@POSIX
def test_symlink_and_ancestor_escape_are_rejected(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "PRIVATE_CHILD").mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with TestClient(create_app(root)) as client:
        for parent in ("link", "link/PRIVATE_CHILD"):
            response = client.post("/internal/folder-discovery", json={"parent_path": parent})
            assert response.status_code == 422 and "PRIVATE_CHILD" not in response.text
        assert client.post("/internal/folder-discovery", json={"parent_path": "missing"}).status_code == 404
    with TestClient(create_app(tmp_path / "missing-root")) as client:
        assert client.post("/internal/folder-discovery", json={"parent_path": ""}).status_code == 503


@POSIX
@pytest.mark.parametrize("race", ["child", "ancestor"])
def test_changed_listing_or_replaced_ancestor_never_returns_obsolete_results(tmp_path, monkeypatch, race):
    parent = tmp_path / "Library"; parent.mkdir(); (parent / "One").mkdir()
    actual_scan = os.scandir; calls = 0
    def scan(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            if race == "child": (parent / "Two").mkdir()
            else:
                parent.rename(tmp_path / "Moved"); parent.mkdir()
        return actual_scan(fd)
    monkeypatch.setattr("app.folder_discovery.os.scandir", scan)
    with pytest.raises(DiscoveryError, match="DISCOVERY_SOURCE_CHANGED"):
        discover_folders(tmp_path, ("Library",))


@POSIX
def test_directory_replaced_by_link_before_open_is_never_followed(tmp_path, monkeypatch):
    (tmp_path / "One").mkdir(); target = tmp_path / "target"; target.mkdir()
    original_open = os.open
    def open_changed(path, flags, **kwargs):
        if path == "One" and "dir_fd" in kwargs:
            (tmp_path / "One").rmdir(); (tmp_path / "One").symlink_to(target, target_is_directory=True)
        return original_open(path, flags, **kwargs)
    monkeypatch.setattr("app.folder_discovery.os.open", open_changed)
    with pytest.raises(DiscoveryError, match="DISCOVERY_READ_FAILED"):
        discover_folders(tmp_path, ())


def test_capacity_and_invalid_body_are_fixed_safe_errors(tmp_path):
    assert DISCOVERY_SLOTS.acquire(blocking=False) and DISCOVERY_SLOTS.acquire(blocking=False)
    try:
        with TestClient(create_app(tmp_path)) as client:
            response = client.post("/internal/folder-discovery", json={"parent_path": ""})
            assert response.status_code == 503 and response.json()["detail"]["code"] == "DISCOVERY_BUSY"
            for payload in ({"parent_path": 1}, {"PRIVATE_SYNTHETIC_KEY": "PRIVATE_SYNTHETIC_VALUE"}):
                response = client.post("/internal/folder-discovery", json=payload)
                assert response.status_code == 422 and "PRIVATE_SYNTHETIC" not in response.text
    finally:
        DISCOVERY_SLOTS.release(); DISCOVERY_SLOTS.release()
