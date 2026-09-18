from array import array
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import stat
from uuid import uuid4
import zipfile

from PIL import Image
import pytest

from app import packaging_assembly as assembly
from app.packaging_assembly import PackagingAssemblyError, assemble_packages
from app.packaging_convert import EXECUTABLE, PackagingConversionError
from app.packaging_stage import stage_packaging_inputs
from app.packaging_zip import PackagingZipError
from app.preflight import ZipPolicy
from app.technical_validation import validate_material

IDENTITY = "SYNTHETIC_LONG_PREFIX_0001_G03"
METADATA = b'\xef\xbb\xbf{\r\n "WEB_APP_PART": {}, "DESKTOP_APP_PART": {}\r\n}\r\n'
CURRENT = ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04.value
LEGACY = ZipPolicy.LEGACY_BEFORE_2026_03_04.value


@pytest.fixture(autouse=True)
def runtime():
    if not Path(EXECUTABLE).is_file():
        if os.environ.get("REQUIRE_PACKAGING_RUNTIME") == "1": pytest.fail("Required packaging runtime is absent")
        pytest.skip("Opt-in Linux ImageMagick Q16-HDRI runtime required")


def make(tmp_path, *, size=(2048, 736), master="2K", optional=True):
    materials = tmp_path / "materials"; materials.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"; workspace.mkdir(mode=0o700)
    folder = materials / IDENTITY; (folder / master).mkdir(parents=True)
    color = Image.linear_gradient("L").resize(size).convert("RGB")
    color.save(folder / master / f"{IDENTITY}_COL_{master}.png")
    pixels = array("H", (index % 65536 for index in range(size[0] * size[1])))
    Image.frombytes("I;16", size, pixels.tobytes()).save(folder / master / f"{IDENTITY}_NRM16_{master}.png")
    if optional:
        (folder / "metadata.txt").write_bytes(METADATA)
        (folder / "PREVIEW" / "empty").mkdir(parents=True)
        (folder / "PREVIEW" / "český náhled.png").write_bytes(b"Synthetic preview bytes preserved without decoding")
    (folder / "SOURCE").mkdir(); (folder / "SOURCE" / "original.sbs").write_bytes(b"Synthetic unpackaged source")
    report = validate_material(materials, (IDENTITY,))
    assert report["can_approve"] is True, [finding["code"] for finding in report["errors"]]
    return materials, workspace, folder, report


@contextmanager
def staged(source, policy=CURRENT, operation=None):
    with stage_packaging_inputs(source[0], (IDENTITY,), source[3], expected_source_revision_hash=source[3]["inventory"]["source_revision_hash"],
            policy=policy, workspace_root=source[1], operation_id=operation or uuid4()) as result: yield result


def snapshot(folder):
    return {str(path.relative_to(folder)): (path.stat().st_mtime_ns, path.stat().st_mode, path.read_bytes() if path.is_file() else None) for path in folder.rglob("*")}


@pytest.mark.parametrize("policy", [CURRENT, LEGACY])
def test_actual_complete_bundle_preserves_sources_metadata_previews_and_resolution_provenance(tmp_path, policy):
    source = make(tmp_path); before = snapshot(source[2])
    with staged(source, policy) as inputs:
        with assemble_packages(inputs, workspace_root=source[1], storage_timezone="Europe/Prague") as bundle:
            assert bundle.operation_id == inputs.operation_id and bundle.plan == inputs.plan and len(bundle.proof_sha256) == 64
            assert [item.filename for item in bundle.archives] == [f"{IDENTITY}_2K.zip", f"{IDENTITY}_1K.zip"]
            with bundle.open_manifest() as descriptor:
                manifest = os.read(descriptor, 65536); assert manifest == inputs.plan.web_manifest()
                assert hashlib.sha256(manifest).hexdigest() == bundle.manifest_sha256
            parsed = json.loads(manifest)
            assert parsed["WEB_APP_PART"]["TEXTURE_RESOLUTIONS"] == {"2K": "2048x736", "1K": "1024x368"}
            master = {item.operation.shortcut: item.sha256 for item in bundle.maps if not item.operation.input_is_effective_master}
            for item in bundle.maps:
                assert item.operation.action == "RESIZE" and item.conversion is not None
                assert item.input_sha256 == (master[item.operation.shortcut] if item.operation.input_is_effective_master else item.operation.source_sha256)
            for proof, resolution in zip(bundle.archives, bundle.plan.resolutions, strict=True):
                with bundle.open_archive(proof.filename) as descriptor, os.fdopen(os.dup(descriptor), "rb") as stream, zipfile.ZipFile(stream) as archive:
                    assert archive.namelist() == [resolution.archive_root + "/", *(resolution.archive_root + "/" + name for name in resolution.archive_entries)]
                    assert archive.read(resolution.archive_root + "/metadata.json") == manifest
                    assert archive.read(resolution.archive_root + "/" + resolution.name + "/metadata.txt") == METADATA
                    assert archive.read(resolution.archive_root + "/PREVIEW/český náhled.png") == b"Synthetic preview bytes preserved without decoding"
                    assert archive.testzip() is None and not any("SOURCE" in name for name in archive.namelist())
                    for operation in resolution.maps:
                        value = archive.read(resolution.archive_root + "/" + operation.destination)
                        with Image.open(io.BytesIO(value)) as image:
                            image.load(); assert image.size == (resolution.width, resolution.height)
                        if operation.shortcut == "NRM16": assert value[24] == 16
                    if policy == LEGACY: assert all(info.date_time == (2026, 1, 1, 0, 0, 0) for info in archive.infolist())
                    else: assert all(info.date_time[0] >= 2026 and info.date_time != (2026, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert len(list(source[1].iterdir())) == 1  # Only still-live staged inputs.
        with pytest.raises(PackagingAssemblyError, match="CLOSED"):
            with bundle.open_archive(bundle.archives[0].filename): pass
    assert list(source[1].iterdir()) == [] and snapshot(source[2]) == before


def test_exact_square_master_is_copied_byte_for_byte_without_converter(tmp_path, monkeypatch):
    source = make(tmp_path, size=(1024, 1024), master="1K")
    def forbidden(*args, **kwargs): pytest.fail("Square master must not be reencoded")
    monkeypatch.setattr(assembly, "convert_map", forbidden)
    with staged(source) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        assert all(item.conversion is None and item.sha256 == item.operation.source_sha256 for item in bundle.maps)
        with bundle.open_archive(bundle.archives[0].filename) as fd, os.fdopen(os.dup(fd), "rb") as stream, zipfile.ZipFile(stream) as archive:
            for item in bundle.maps:
                assert archive.read(bundle.plan.resolutions[0].archive_root + "/" + item.operation.destination) == (source[2] / item.operation.source).read_bytes()


@pytest.mark.parametrize("size,master,expected", [((3072, 1105), "4K", ["3K", "2K", "1K"]), ((4096, 1473), "2K", ["2K", "1K"])])
def test_nonstandard_and_capped_masters_generate_only_planned_resolutions(tmp_path, size, master, expected):
    source = make(tmp_path, size=size, master=master)
    with staged(source) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        assert [item.name for item in bundle.plan.resolutions] == expected
        assert len(bundle.archives) == len(expected)
        assert all(item.operation.width == item.conversion.width and item.operation.height == item.conversion.height for item in bundle.maps)


def test_missing_optional_inputs_remain_warnings_and_are_not_invented(tmp_path):
    source = make(tmp_path, size=(1024, 1024), master="1K", optional=False)
    with staged(source) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        assert bundle.plan.warnings == ("PRODUCTION_METADATA_MISSING", "PREVIEW_MISSING")
        assert not any("metadata.txt" in entry.path or "PREVIEW" in entry.path for archive in bundle.archives for entry in archive.entries)


@pytest.mark.parametrize("phase", ["conversion", "archive", "consumer"])
def test_partial_work_and_consumer_failure_clean_only_owned_artifacts(tmp_path, monkeypatch, phase):
    source = make(tmp_path); before = snapshot(source[2]); outside = source[1] / "keep"; outside.mkdir(); (outside / "marker").write_text("Keep")
    original = assembly.convert_map if phase == "conversion" else assembly.create_verified_zip; calls = 0
    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2: raise (PackagingConversionError if phase == "conversion" else PackagingZipError)("SYNTHETIC_FAILURE")
        return original(*args, **kwargs)
    if phase != "consumer": monkeypatch.setattr(assembly, "convert_map" if phase == "conversion" else "create_verified_zip", fail)
    expected = RuntimeError if phase == "consumer" else PackagingConversionError if phase == "conversion" else PackagingZipError
    with pytest.raises(expected, match="SYNTHETIC_FAILURE"):
        with staged(source) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC"):
            if phase == "consumer": raise RuntimeError("SYNTHETIC_FAILURE")
            pytest.fail("Partial bundle returned")
    assert list(source[1].iterdir()) == [outside] and (outside / "marker").read_text() == "Keep" and snapshot(source[2]) == before


def test_refuses_an_existing_artifact_workspace_and_closed_inputs(tmp_path):
    source = make(tmp_path); operation = uuid4(); existing = source[1] / ("artifacts-" + str(operation)); existing.mkdir(); (existing / "keep").write_text("Keep")
    with staged(source, operation=operation) as inputs:
        with pytest.raises(PackagingAssemblyError, match="ALREADY_EXIST"):
            with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC"): pass
    assert (existing / "keep").read_text() == "Keep"
    with pytest.raises(PackagingAssemblyError):
        with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC"): pass


def test_rejects_any_output_root_except_the_original_private_staging_root(tmp_path):
    source = make(tmp_path); before = snapshot(source[2])
    with staged(source) as inputs:
        with pytest.raises(PackagingAssemblyError, match="ROOT_MISMATCH"):
            with assemble_packages(inputs, workspace_root=source[0], storage_timezone="UTC"): pass
    assert snapshot(source[2]) == before


@pytest.mark.parametrize("name", ["../metadata.json", "map-0000", "metadata.json", "/unplanned.zip"])
def test_consumer_cannot_open_unplanned_files_as_archives(tmp_path, name):
    source = make(tmp_path, size=(1024, 1024), master="1K")
    with staged(source) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        with pytest.raises(PackagingAssemblyError, match="NOT_FOUND"):
            with bundle.open_archive(name): pass


@pytest.mark.parametrize("change", ["bytes", "symlink"])
def test_artifact_bytes_and_paths_are_rechecked_on_open(tmp_path, change):
    source = make(tmp_path, size=(1024, 1024), master="1K")
    with staged(source) as inputs, assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
        path = source[1] / ("artifacts-" + str(inputs.operation_id)) / bundle.archives[0].filename
        if change == "bytes": path.chmod(0o600); path.write_bytes(b"x" * path.stat().st_size); path.chmod(0o400)
        else: path.unlink(); path.symlink_to(source[2] / "metadata.txt")
        with pytest.raises(PackagingAssemblyError, match="ARTIFACT_CHANGED"):
            with bundle.open_archive(bundle.archives[0].filename): pass
    assert (source[2] / "metadata.txt").read_bytes() == METADATA and list(source[1].iterdir()) == []


def test_replaced_workspace_is_preserved_and_cleanup_requires_review(tmp_path):
    source = make(tmp_path, size=(1024, 1024), master="1K")
    with staged(source) as inputs:
        path = source[1] / ("artifacts-" + str(inputs.operation_id)); moved = source[1] / "moved-owned-artifacts"
        with pytest.raises(PackagingAssemblyError, match="CLEANUP_REQUIRED"):
            with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC"):
                path.rename(moved); path.mkdir(); (path / "keep").write_text("Keep")
        assert (path / "keep").read_text() == "Keep" and (moved / "metadata.json").is_file()


@pytest.mark.parametrize("limits", [{"max_bytes": 1}, {"max_seconds": .000001}])
def test_whole_operation_has_byte_and_time_limits_and_cleans_partial_work(tmp_path, limits):
    source = make(tmp_path, size=(1024, 1024), master="1K")
    with staged(source) as inputs:
        with pytest.raises(PackagingAssemblyError):
            with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC", **limits): pass
        assert len(list(source[1].iterdir())) == 1
    assert list(source[1].iterdir()) == []


def test_second_bundle_is_rejected_without_queue_and_slot_releases_after_failure(tmp_path):
    source = make(tmp_path, size=(1024, 1024), master="1K")
    with staged(source) as inputs:
        assert assembly.ASSEMBLY_SLOT.acquire(blocking=False)
        try:
            with pytest.raises(PackagingAssemblyError, match="BUSY"):
                with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC"): pass
        finally: assembly.ASSEMBLY_SLOT.release()
        with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle: assert len(bundle.archives) == 1


def test_aggregate_budget_includes_generated_maps_manifest_and_archives(tmp_path):
    source = make(tmp_path, size=(1024, 1024), master="1K")
    with staged(source, LEGACY) as inputs:
        with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC") as bundle:
            total = sum(item.size for item in bundle.maps) + sum(item.size for item in bundle.archives) + len(bundle.plan.web_manifest())
        with pytest.raises(PackagingZipError, match="SIZE_LIMIT"):
            with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC", max_bytes=total - 1): pass
        assert len(list(source[1].iterdir())) == 1


def test_copy_branch_redecodes_actual_bytes_even_if_report_was_forged(tmp_path):
    from app.inventory import inventory_material
    source = make(tmp_path, size=(1024, 1024), master="1K")
    path = source[2] / source[3]["images"][0]["path"]; path.write_bytes(b"Synthetic invalid image bytes")
    source[3]["inventory"] = inventory_material(source[0], (IDENTITY,))
    source[3]["images"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with staged(source) as inputs:
        with pytest.raises(PackagingAssemblyError, match="COPY_IMAGE_MISMATCH"):
            with assemble_packages(inputs, workspace_root=source[1], storage_timezone="UTC"): pass
        assert len(list(source[1].iterdir())) == 1
