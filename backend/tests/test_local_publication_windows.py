"""Native destination capabilities and atomic copying; all sources are owned fixtures."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest

from app.local_filesystem import LocalFilesError
from app.local_materials import LocalMaterialLibrary
from app.local_publication import LocalPublicationExport

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows desktop capability")
FOLDER = "Brand/BRAND_0001_TEST_K03"


@pytest.fixture
def exporter(tmp_path):
    source = tmp_path / "library" / FOLDER
    source.mkdir(parents=True)
    (source / "metadata.json").write_text('{"COLOR":{"hex":"#FFFFFF"}}')
    (source / "map.png").write_bytes(b"unaltered source")
    library = LocalMaterialLibrary(tmp_path / "library", tmp_path / "journal")
    destination = tmp_path / "destination"; destination.mkdir()
    service = LocalPublicationExport(library, tmp_path / "jobs", docker_executable=sys.executable,
        docker_context="desktop-linux", image="sha256:" + "a" * 64)
    yield service, destination
    service.executor.shutdown(wait=True)


def request():
    return {"schema_version": 1, "preview_hash": "a" * 64,
        "material_versions": [{"id": "00000000-0000-0000-0000-000000000001", "updated_at": "2026-01-01T00:00:00Z"}],
        "materials": [{"material_id": "00000000-0000-0000-0000-000000000001", "folder_path": FOLDER,
            "identity_name": FOLDER.split("/")[-1], "metadata_sha256": "a" * 64}],
        "settings": {"cutoff_date": "2026-03-04", "storage_timezone": "Europe/Prague"}, "csv_hex": b"exact;csv\r\n".hex()}


def test_original_master_observations_are_timezone_aware_normalized_and_immutable(exporter, tmp_path):
    original, _ = exporter
    identifier = str(uuid4())
    values = {identifier: "2024-04-22T21:51:34+02:00"}
    service = LocalPublicationExport(original.library, tmp_path / "provenance-jobs", docker_executable=sys.executable,
        docker_context="desktop-linux", image="sha256:" + "a" * 64, original_master_observations=values)
    try:
        values[identifier] = "2026-01-01T00:00:00Z"
        assert service.original_master_observations[identifier] == "2024-04-22T19:51:34+00:00"
        with pytest.raises(TypeError): service.original_master_observations[identifier] = values[identifier]
    finally: service.executor.shutdown(wait=True)
    with pytest.raises(ValueError, match="timezone"):
        LocalPublicationExport(original.library, tmp_path / "bad-provenance", docker_executable=sys.executable,
            docker_context="desktop-linux", image="sha256:" + "a" * 64,
            original_master_observations={identifier: "2024-04-22T19:51:34"})


def finish(service, identifier, actor):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = service.status(identifier, actor)
        if value["status"] != "RUNNING": return value
        time.sleep(.02)
    pytest.fail("Export did not finish")


def synthetic_container(service, identifier, *, bad_hash=False, invalid=False):
    directory = service.root / str(identifier) / "files"
    data = b"synthetic verified archive"
    filename = "BRAND_0001_TEST_K03_1K.zip"
    (directory / filename).write_bytes(data)
    with service.library.fs.tree(FOLDER, for_rename=False) as (_, entries, _): pass
    result = {"schema_version": 1, "status": "FAILED" if invalid else "COMPLETED", "items": [{
        "material_id": "00000000-0000-0000-0000-000000000001", "status": "FAILED" if invalid else "COMPLETED",
        "source_entries": entries,
        "issues": [{"code": "COLOR_MAP_MISSING"}] if invalid else [], "archives": [{"name": filename,
            "size": len(data), "sha256": "0" * 64 if bad_hash else hashlib.sha256(data).hexdigest()}]}]}
    (directory / "result.json").write_text(json.dumps(result))


def test_unique_complete_export_preserves_sources_and_exact_replay_does_not_copy_again(exporter, monkeypatch):
    service, destination = exporter; actor = uuid4(); identifier = uuid4(); seen = []
    before = {path: path.read_bytes() for path in service.library.fs.path(FOLDER).iterdir()}
    monkeypatch.setattr(service, "_run_container", lambda key: synthetic_container(service, key))
    capability = service.grant_destination(destination, actor)
    started = service.start(actor, identifier, capability["destination_token"], request(), lambda: seen.append("verify"))
    assert started["status"] == "RUNNING"
    result = finish(service, identifier, actor)
    assert result["status"] == "COMPLETED", result
    folder = Path(result["output_path"])
    assert folder.parent == destination and folder.name.startswith("REAWOTE-publication-")
    assert (folder / "materials.csv").read_bytes() == b"exact;csv\r\n"
    receipt = json.loads((folder / "export-receipt.json").read_text())
    assert receipt["upload_performed"] is False and len(receipt["archives"]) == 1
    assert service.start(actor, identifier, capability["destination_token"], request(), lambda: pytest.fail("Replay called work")) == result
    assert len(list(destination.iterdir())) == 1 and seen == ["verify", "verify"]
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize("which", ["library", "journal", "root"])
def test_destination_overlap_rejected(exporter, which):
    service, destination = exporter
    path = {"library": service.library.fs.root, "journal": service.library.journal.root,
        "root": service.root, "ancestor": destination.parent}[which]
    with pytest.raises(LocalFilesError, match="OVERLAP"): service.grant_destination(path, uuid4())


def test_destination_may_be_a_parent_with_unique_output_outside_library(exporter):
    service, destination = exporter
    assert service.grant_destination(destination.parent, uuid4())["destination_path"] == str(destination.parent)


def test_destination_token_is_actor_bound_expiring_and_one_use(exporter, monkeypatch):
    service, destination = exporter; actor = uuid4()
    capability = service.grant_destination(destination, actor)
    with pytest.raises(LocalFilesError, match="EXPIRED"):
        service.start(uuid4(), uuid4(), capability["destination_token"], request(), lambda: None)
    service.tokens[capability["destination_token"]]["expires"] = 0
    with pytest.raises(LocalFilesError, match="EXPIRED"):
        service.start(actor, uuid4(), capability["destination_token"], request(), lambda: None)
    capability = service.grant_destination(destination, actor)
    monkeypatch.setattr(service, "_run_container", lambda key: synthetic_container(service, key))
    identifier = uuid4(); service.start(actor, identifier, capability["destination_token"], request(), lambda: None)
    assert finish(service, identifier, actor)["status"] == "COMPLETED"
    with pytest.raises(LocalFilesError, match="EXPIRED"):
        service.start(actor, uuid4(), capability["destination_token"], request(), lambda: None)


@pytest.mark.parametrize("failure", ["archive_hash", "source_invalid", "database_changed", "destination_replaced"])
def test_failures_never_expose_complete_publication_folder(exporter, monkeypatch, failure):
    service, destination = exporter; actor = uuid4(); identifier = uuid4(); calls = 0
    capability = service.grant_destination(destination, actor)
    def container(key):
        synthetic_container(service, key, bad_hash=failure == "archive_hash", invalid=failure == "source_invalid")
        if failure == "destination_replaced":
            destination.rename(destination.with_name("old-destination")); destination.mkdir()
    def verify():
        nonlocal calls
        calls += 1
        if failure == "database_changed" and calls == 2: raise LocalFilesError("LOCAL_EXPORT_MATERIAL_CHANGED")
    monkeypatch.setattr(service, "_run_container", container)
    service.start(actor, identifier, capability["destination_token"], request(), verify)
    result = finish(service, identifier, actor)
    assert result["status"] == "FAILED", result
    assert not any(item.name.startswith("REAWOTE-publication-") for item in destination.iterdir())


def test_container_cannot_write_or_rename_held_sources(exporter, monkeypatch):
    service, destination = exporter; actor = uuid4(); identifier = uuid4()
    def container(key):
        path = service.library.fs.path(FOLDER) / "map.png"
        with pytest.raises(PermissionError): path.write_bytes(b"forbidden")
        with pytest.raises(PermissionError): path.rename(path.with_name("renamed.png"))
        synthetic_container(service, key)
    monkeypatch.setattr(service, "_run_container", container)
    capability = service.grant_destination(destination, actor)
    service.start(actor, identifier, capability["destination_token"], request(), lambda: None)
    assert finish(service, identifier, actor)["status"] == "COMPLETED"


def test_restart_exposes_interrupted_receipt_without_repeating_output(exporter):
    service, destination = exporter; actor = uuid4(); identifier = str(uuid4())
    (service.root / identifier).mkdir()
    service._write(identifier, {"id": identifier, "actor_id": str(actor), "status": "RUNNING"})
    result = service.status(identifier, actor)
    assert result["status"] == "FAILED" and result["error_code"] == "LOCAL_EXPORT_INTERRUPTED"
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("change", ["replace", "add", "remove"])
def test_publication_confirmation_rejects_source_changes_after_export(exporter, monkeypatch, change):
    service, destination = exporter; actor = uuid4(); identifier = uuid4()
    monkeypatch.setattr(service, "_run_container", lambda key: synthetic_container(service, key))
    capability = service.grant_destination(destination, actor)
    service.start(actor, identifier, capability["destination_token"], request(), lambda: None)
    assert finish(service, identifier, actor)["status"] == "COMPLETED"
    with service.unchanged_sources(identifier, actor): pass
    folder = service.library.fs.path(FOLDER)
    if change == "replace": (folder / "map.png").write_bytes(b"a different map")
    elif change == "add": (folder / "new.png").write_bytes(b"added map")
    else: (folder / "map.png").unlink()
    with pytest.raises(LocalFilesError, match="SOURCE_CHANGED"):
        with service.unchanged_sources(identifier, actor): pytest.fail("Changed source accepted")
