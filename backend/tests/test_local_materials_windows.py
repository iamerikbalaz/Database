"""Exercise real NTFS handles and recovery; no network or user library access."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.identity_client import IdentityClientError
from app.local_filesystem import LocalFilesError
from app.local_materials import LocalMaterialLibrary
from app.metadata_client import MetadataClientError
from app.preview_client import PreviewClientError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows filesystem adapter")
FOLDER = "Brand/BRAND_0001_OLD_K03"
IDENTITY = {"FOLDER": "BRAND_0001_OLD_K03", "MANUFACTURER": "Brand", "PRODUCT_NUMBER": "0001",
            "PRODUCT_NAME": "Old", "CATEGORY": "K03", "BASE_NAME": "BRAND_0001_OLD"}


@pytest.fixture
def library(tmp_path):
    (tmp_path / "library" / FOLDER / "4K").mkdir(parents=True)
    (tmp_path / "library" / FOLDER / "4K" / "BRAND_0001_OLD_COL_4K.png").write_bytes(b"unaltered map content")
    (tmp_path / "library" / "Destination").mkdir()
    return LocalMaterialLibrary(tmp_path / "library", tmp_path / "journal")


def request(**overrides):
    return {"operation_id": str(uuid4()), "source_filename": "metadata.json", "folder_path": FOLDER,
            "expected_sha256": None, "identity": IDENTITY,
            "values": {"hex_color": "#999999", "width_cm": "12.5", "height_cm": "20.1"}, **overrides}


def test_create_edit_and_exact_replay(library):
    first = request()
    assert library.metadata.execute(first).status == "COMPLETED"
    previous = library.metadata.inspect(FOLDER)
    edit = request(expected_sha256=previous.sha256, values={"hex_color": "#FF822D", "width_cm": "19.2", "height_cm": "20.1"})
    result = library.metadata.execute(edit)
    assert result.status == "COMPLETED"
    assert library.metadata.execute(edit) == result
    assert library.metadata.inspect(FOLDER).width_cm == "19.2"
    assert (library.fs.path(FOLDER) / "4K/BRAND_0001_OLD_COL_4K.png").read_bytes() == b"unaltered map content"
    assert {p.name for p in library.fs.path(FOLDER).iterdir()} == {"4K", "metadata.json"}
    listing = library.listing(FOLDER)
    assert listing.omitted_entries == 0
    assert {item.name for item in listing.entries} == {"4K", "metadata.json"}
    document = json.loads(library.fs.read(FOLDER + "/metadata.json"))
    assert document["RESOLUTIONS"]["4K"] == {"MAPS_SHORTCUTS": ["COL"], "FILE_COUNT": 1, "UNRECOGNIZED": []}
    assert document["SOURCE"] == {"SBS": None, "CROPS": [], "REFERENCES": False, "ARCHIVE": False}
    assert document["COLOR"]["runner_up"] == {"hex": None, "delta_e": None}


@pytest.mark.parametrize("length", [1, 2, 3, 7, 16, 31, 63, 99, 140])
def test_rename_handle_exact_utf16_termination(library, length):
    # Missing UTF-16 NUL used to append garbage for some buffer allocations.
    old = library.fs.root / ("a" * length)
    new = library.fs.root / ("b" * length + ".json")
    old.write_bytes(b"content")
    with library.fs.directory(""), library.fs.opened(old, rename=True) as handle:
        library.fs.rename_handle(handle, new)
    assert new.read_bytes() == b"content"
    assert not old.exists()
    assert [p.name for p in library.fs.root.iterdir() if p.is_file()] == [new.name]


def test_metadata_source_changed_rejects_without_overwrite(library):
    library.metadata.execute(request())
    before = library.fs.read(FOLDER + "/metadata.json")
    result = library.metadata.execute(request())
    assert result.status == "REJECTED" and result.failure_code == "METADATA_SOURCE_CHANGED"
    assert library.fs.read(FOLDER + "/metadata.json") == before


@pytest.mark.parametrize("point", ["temporary", "backup", "published"])
def test_metadata_recovers_after_each_durable_boundary(library, monkeypatch, point):
    library.metadata.execute(request())
    current = library.metadata.inspect(FOLDER)
    edit = request(expected_sha256=current.sha256, values={"hex_color": "#FFFFFF", "width_cm": "3.1", "height_cm": "4.2"})
    original_state = library.write_state; original_rename = library.fs.rename_handle
    failed = False
    def save(key, state):
        nonlocal failed
        if point == "temporary" and "after" in state and "result" not in state and not failed:
            failed = True; raise OSError("simulated interruption before state write")
        return original_state(key, state)
    def rename(handle, target):
        nonlocal failed
        result = original_rename(handle, target)
        if not failed and ((point == "backup" and target.name == "before.json") or (point == "published" and target.name == "metadata.json")):
            failed = True; raise OSError("simulated interruption after rename")
        return result
    monkeypatch.setattr(library, "write_state", save)
    monkeypatch.setattr(library.fs, "rename_handle", rename)
    with pytest.raises(MetadataClientError): library.metadata.execute(edit)
    assert library.metadata.execute(edit).status == "COMPLETED"
    assert library.metadata.inspect(FOLDER).width_cm == "3.1"


def target(folder="Destination/BRAND_0001_NEW_K03", name="New"):
    return {"folder_path": FOLDER, "target_path": folder, "brand_name": "Brand", "material_name": name}


def test_identity_renames_nested_files_and_metadata_and_replays(library):
    library.metadata.execute(request())
    proposal = target(); plan = library.identity.plan(proposal)
    command = {**proposal, "operation_id": str(uuid4()), "expected_plan_hash": plan.plan_hash}
    result = library.identity.execute(command)
    assert result.status == "COMPLETED" and library.identity.execute(command) == result
    assert not library.fs.path(FOLDER).exists()
    destination = library.fs.path(proposal["target_path"])
    assert (destination / "4K/BRAND_0001_NEW_COL_4K.png").read_bytes() == b"unaltered map content"
    document = json.loads((destination / "metadata.json").read_text())
    assert document["FOLDER"] == destination.name and document["PRODUCT_NAME"] == "New"
    assert document["TEXTURE_SIZE"]["cm"]["width"] == 12.5


def test_folder_move_keeps_original_json_bytes(library):
    library.metadata.execute(request())
    before = library.fs.read(FOLDER + "/metadata.json")
    proposal = target("Destination/BRAND_0001_OLD_K03", "Old")
    plan = library.identity.plan(proposal)
    result = library.identity.execute({**proposal, "operation_id": str(uuid4()), "expected_plan_hash": plan.plan_hash})
    assert result.status == "COMPLETED"
    assert library.fs.read(proposal["target_path"] + "/metadata.json") == before


@pytest.mark.parametrize("interrupted_step", [1, 2, 3, 4])
def test_identity_recovers_after_rename_before_step_receipt(library, monkeypatch, interrupted_step):
    library.metadata.execute(request())
    proposal = target(); plan = library.identity.plan(proposal)
    command = {**proposal, "operation_id": str(uuid4()), "expected_plan_hash": plan.plan_hash}
    original = library.write_state; failed = False
    def save(key, state):
        nonlocal failed
        if state.get("completed_steps") == interrupted_step and not failed:
            failed = True; raise OSError("simulated crash before receipt")
        return original(key, state)
    monkeypatch.setattr(library, "write_state", save)
    with pytest.raises(IdentityClientError): library.identity.execute(command)
    assert library.identity.execute(command).status == "COMPLETED"
    assert (library.fs.path(proposal["target_path"]) / "4K/BRAND_0001_NEW_COL_4K.png").read_bytes() == b"unaltered map content"


def test_identity_target_collision_does_not_overwrite(library):
    library.fs.path("Destination/BRAND_0001_NEW_K03").mkdir()
    plan = library.identity.plan(target())
    assert not plan.ready
    assert library.fs.path(FOLDER).exists()


def test_hardlink_is_rejected(library, tmp_path):
    outside = tmp_path / "external.json"; outside.write_text("{}")
    os.link(outside, library.fs.path(FOLDER) / "metadata.json")
    with pytest.raises(MetadataClientError): library.metadata.inspect(FOLDER)
    assert outside.read_text() == "{}"


def test_paths_and_journal_cannot_escape_root(library):
    for path in ("../other", "C:/other", "/other", "Brand/../other"):
        with pytest.raises((ValueError, LocalFilesError)): library.fs.path(path)
    with pytest.raises(LocalFilesError): LocalMaterialLibrary(library.fs.root, library.fs.root / "journal")


def test_preview_original_size_and_reload_after_external_edit(library):
    from PIL import Image
    preview = library.fs.path(FOLDER) / "PREVIEW"; preview.mkdir()
    Image.new("RGB", (1200, 1200), "red").save(preview / "SPHERE_1.png")
    first = library.previews.listing(FOLDER).items[0]
    image = library.previews.image(FOLDER, first.name, first.sha256, 256)
    assert (image.width, image.original_width, image.original_height) == (256, 1200, 1200)
    Image.new("RGB", (1200, 1200), "blue").save(preview / "SPHERE_1.png")
    second = library.previews.listing(FOLDER).items[0]
    assert second.sha256 != first.sha256
    with pytest.raises(PreviewClientError): library.previews.image(FOLDER, first.name, first.sha256)
    assert library.previews.image(FOLDER, second.name, second.sha256).sha256 != image.sha256
