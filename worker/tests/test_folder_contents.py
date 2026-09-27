import os
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.folder_discovery import DiscoveryError, DiscoveryLimits, discover_folders
from app.secure_filesystem import secure_filesystem_access_supported

pytestmark = pytest.mark.skipif(not secure_filesystem_access_supported(), reason="secure Linux source listing required")


def test_contents_are_stat_only_nonrecursive_and_omit_unsafe_entries(tmp_path, monkeypatch):
    parent = tmp_path / "TEST_0001_G01"; parent.mkdir()
    (parent / "PREVIEW").mkdir(); (parent / "PREVIEW" / "PRIVATE_CHILD").write_text("PRIVATE_BYTES")
    source = parent / "metadata.txt"; source.write_bytes(b"texture size: 12x34 cm")
    (parent / "linked").symlink_to(source); (parent / "unsafe:name").write_text("PRIVATE_BYTES"); os.mkfifo(parent / "pipe")
    before = source.stat(); original_open = os.open
    def only_directories(path, flags, *args, **kwargs):
        assert flags & os.O_DIRECTORY
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr("app.folder_discovery.os.open", only_directories)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/internal/folder-contents", json={"parent_path": parent.name})
    assert response.status_code == 200
    assert response.json() == {"schema_version": 1, "parent_path": parent.name, "omitted_entries": 3, "entries": [
        {"name": "PREVIEW", "path": parent.name + "/PREVIEW", "kind": "directory", "size": 0},
        {"name": "metadata.txt", "path": parent.name + "/metadata.txt", "kind": "file", "size": before.st_size}]}
    assert "PRIVATE" not in response.text and str(tmp_path) not in response.text
    assert source.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("field,value,code", [("max_entries", 1, "DISCOVERY_ENTRY_LIMIT"), ("max_seconds", -1, "DISCOVERY_TIME_LIMIT")])
def test_file_contents_listing_is_bounded_without_partial_results(tmp_path, field, value, code):
    (tmp_path / "one.txt").write_text("one"); (tmp_path / "two.txt").write_text("two")
    with pytest.raises(DiscoveryError, match=code):
        discover_folders(tmp_path, (), include_files=True, limits=replace(DiscoveryLimits(), **{field: value}))
    assert len(discover_folders(tmp_path, (), include_files=True)["entries"]) == 2


def test_source_file_changed_during_listing_is_not_returned(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"; source.write_text("before")
    scan = os.scandir; calls = 0
    def changed(fd):
        nonlocal calls
        calls += 1
        if calls == 2: source.write_text("after with different size")
        return scan(fd)
    monkeypatch.setattr("app.folder_discovery.os.scandir", changed)
    with pytest.raises(DiscoveryError, match="DISCOVERY_SOURCE_CHANGED"):
        discover_folders(tmp_path, (), include_files=True)
