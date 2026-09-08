import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.preflight import ZipPolicy, preflight_material, select_zip_policy


BOUNDARY = datetime(2026, 3, 4, tzinfo=ZoneInfo("Europe/Prague"))
LEGACY_FIXTURE = Path(__file__).parent / "fixtures" / "metadata_legacy_web_app.json"
LEGACY_SHA256 = "1c895c2fe94a216e3c8b352b34cebb4568fb8304f8d141a76a7f227df7359c9b"
BOUNDARY_NS = int(BOUNDARY.timestamp()) * 1_000_000_000
VALID = {"WEB_APP_PART": {"TEXTURE_RESOLUTIONS": {"4K": "4096x4096"},
                          "MAPS_SHORTCUTS": ["COL"]}, "DESKTOP_APP_PART": {}}


def inspect(path, root=None):
    return preflight_material(path, allowed_root=root or path, boundary=BOUNDARY)


def material(tmp_path, resolutions=("4K",)):
    path = tmp_path / "Material with spaces"
    path.mkdir()
    for resolution in resolutions:
        (path / resolution).mkdir()
    return path


def codes(result):
    return {finding.code for finding in result.warnings}


@pytest.mark.parametrize("resolutions,master", [
    ((), None), (("4K",), "4K"), (("8K", "16K"), "16K"),
    (("1K", "2K", "4K", "12K", "32K", "128K"), "128K"),
])
def test_resolution_selection(tmp_path, resolutions, master):
    result = inspect(material(tmp_path, resolutions))
    assert result.found_resolutions == list(resolutions)
    assert result.master_resolution == master
    assert result.can_continue == bool(resolutions)
    if not resolutions:
        assert result.errors[0].code == "NO_RESOLUTION"
        assert result.selected_zip_policy is None


def test_safe_pattern_direct_directories_only(tmp_path):
    path = material(tmp_path)
    for name in ("0K", "04K", "8k", "4K backup", "-8K", "1.5K", "１２K", "SOURCE"):
        (path / name).mkdir()
    (path / "SOURCE" / "64K").mkdir()
    (path / "128K").write_text("not a directory")
    assert inspect(path).found_resolutions == ["4K"]


@pytest.mark.parametrize("delta,policy", [
    (-1_000_000_000, ZipPolicy.LEGACY_BEFORE_2026_03_04),
    (0, ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04),
    (1_000_000_000, ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04),
])
def test_filesystem_boundary(tmp_path, delta, policy):
    path = material(tmp_path, ("4K", "16K"))
    timestamp = BOUNDARY_NS + delta
    os.utime(path / "16K", ns=(timestamp, timestamp))
    result = inspect(path)
    assert result.master_modified_ns == timestamp
    assert result.selected_zip_policy == policy
    assert datetime.fromisoformat(result.master_modified_at).timestamp() == timestamp / 1e9
    assert result.policy_boundary == "2026-03-04T00:00:00+01:00"
    assert result.policy_timezone == "Europe/Prague"


def test_default_timezone_and_configuration(tmp_path, monkeypatch):
    path = material(tmp_path)
    os.utime(path / "4K", ns=(BOUNDARY_NS, BOUNDARY_NS))
    monkeypatch.delenv("ZIP_POLICY_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    result = preflight_material(path, allowed_root=tmp_path)
    assert result.policy_timezone == "Europe/Prague"
    assert result.selected_zip_policy == ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04
    monkeypatch.setenv("ZIP_POLICY_TIMEZONE", "UTC")
    result = preflight_material(path, allowed_root=tmp_path)
    assert result.policy_timezone == "UTC"
    assert result.selected_zip_policy == ZipPolicy.LEGACY_BEFORE_2026_03_04


def test_non_standard_resolution_is_nonfatal_with_valid_metadata(tmp_path):
    path = material(tmp_path, ("4K", "8K", "12K"))
    (path / "metadata.json").write_text(json.dumps(VALID), encoding="utf-8")
    result = inspect(path)
    assert result.master_resolution == "12K"
    assert result.can_continue and not result.errors
    assert result.metadata_status == "VALID"
    assert codes(result) == {"NON_STANDARD_RESOLUTION"}
    assert result.warnings[0].path == str(path / "12K")


def test_nanosecond_and_timezone_boundary():
    assert select_zip_policy(BOUNDARY_NS - 1, boundary=BOUNDARY) == ZipPolicy.LEGACY_BEFORE_2026_03_04
    assert select_zip_policy(BOUNDARY_NS, boundary=BOUNDARY) == ZipPolicy.CURRENT_ON_OR_AFTER_2026_03_04
    utc_midnight = datetime(2026, 3, 4, tzinfo=timezone.utc)
    assert select_zip_policy(BOUNDARY_NS, boundary=utc_midnight) == ZipPolicy.LEGACY_BEFORE_2026_03_04


@pytest.mark.parametrize("boundary", [datetime(2026, 3, 4),
    datetime(2026, 3, 5, tzinfo=timezone.utc)])
def test_invalid_boundary_before_io(tmp_path, monkeypatch, boundary):
    def forbidden(*args, **kwargs):
        pytest.fail("No IO for invalid configuration")
    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(ValueError):
        preflight_material(tmp_path, allowed_root=tmp_path, boundary=boundary)


def test_missing_metadata_does_not_block(tmp_path):
    result = inspect(material(tmp_path))
    assert result.metadata_status == "MISSING"
    assert result.metadata_exists is False
    assert result.metadata_strict_json is None
    assert result.metadata_sha256 is None
    assert codes(result) == {"METADATA_MISSING"}
    assert result.can_continue and not result.errors


def test_valid_metadata_and_serializable_contract(tmp_path):
    path = material(tmp_path)
    raw = json.dumps(VALID).encode()
    (path / "metadata.json").write_bytes(raw)
    result = inspect(path)
    assert result.metadata_status == "VALID"
    assert result.metadata_exists and result.metadata_readable and result.metadata_strict_json
    assert all(result.metadata_fields_present.values())
    assert result.metadata_sha256 == hashlib.sha256(raw).hexdigest()
    assert not result.warnings and result.can_continue
    assert json.loads(json.dumps(result.to_dict()))["can_continue"] is True


def test_original_legacy_fixture(tmp_path):
    raw = LEGACY_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LEGACY_SHA256
    path = material(tmp_path)
    (path / "metadata.json").write_bytes(raw)
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    result = inspect(path)
    assert result.metadata_status == "LEGACY_NON_STANDARD_JSON"
    assert result.metadata_strict_json is False
    assert codes(result) == {"LEGACY_NON_STANDARD_JSON"}
    assert all(result.metadata_fields_present.values())
    assert result.metadata_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.metadata_sha256 == LEGACY_SHA256
    web = result.metadata_web_app_part
    assert web["TEXTURE_RESOLUTIONS"] == {
        "16K": "16384x5890", "1K": "1024x368", "2K": "2048x736",
        "4K": "4096x1473", "8K": "8192x2945"}
    assert web["IMAGE_RATIO"] == 0.359497
    assert web["MAPS_SHORTCUTS"] == ["AO", "COL", "DISP16", "DISP", "GLOSS", "NRM16", "NRM", "ROUGH"]
    assert not result.metadata_fields_present.get("COLOR.hex", False)
    assert not result.metadata_fields_present.get("TEXTURE_SIZE.cm", False)
    assert result.can_continue and not result.errors
    assert (path / "metadata.json").read_bytes() == raw
    assert LEGACY_FIXTURE.read_bytes() == raw


@pytest.mark.parametrize("raw", [b'{broken', b'\xff', b'{"a":NaN}',
    b'{"a":1,"a":2}', b'{"a":1,}',
    b'{"WEB_APP_PART":{"MAPS_SHORTCUTS":["COL",]},"DESKTOP_APP_PART":{},}'])
def test_corrupt_metadata_is_warning(tmp_path, raw):
    path = material(tmp_path)
    (path / "metadata.json").write_bytes(raw)
    result = inspect(path)
    assert result.metadata_status == "INVALID_JSON"
    assert result.metadata_strict_json is False
    assert result.metadata_sha256 == hashlib.sha256(raw).hexdigest()
    assert "METADATA_INVALID_JSON" in codes(result)
    assert result.can_continue and not result.errors


@pytest.mark.parametrize("data", [{}, [], {"WEB_APP_PART": None},
    {"WEB_APP_PART": {"TEXTURE_RESOLUTIONS": [], "MAPS_SHORTCUTS": {}}}])
def test_metadata_fields_and_types(tmp_path, data):
    path = material(tmp_path)
    (path / "metadata.json").write_text(json.dumps(data), encoding="utf-8")
    result = inspect(path)
    assert result.metadata_status == "INVALID_STRUCTURE"
    assert result.metadata_strict_json is True
    assert result.warnings and result.can_continue and not result.errors


def test_parser_resource_failure_is_warning(tmp_path, monkeypatch):
    path = material(tmp_path)
    (path / "metadata.json").write_text("{}")
    def exhausted(*args, **kwargs):
        raise RecursionError("parser depth exhausted")
    monkeypatch.setattr("app.preflight._load_json", exhausted)
    result = inspect(path)
    assert result.metadata_status == "INVALID_JSON" and result.can_continue


def test_legacy_parser_preserves_strings(tmp_path):
    path = material(tmp_path)
    data = {**VALID, "note": 'literal comma ,} and escaped quote "'}
    (path / "metadata.json").write_text(json.dumps(data)[:-1] + ",}")
    result = inspect(path)
    assert result.metadata_status == "LEGACY_NON_STANDARD_JSON"
    assert result.can_continue


@pytest.mark.parametrize("target", ["root", "4K", "metadata.json"])
@pytest.mark.parametrize("link_kind", ["symlink", "reparse"])
def test_reparse_points_rejected_without_following(tmp_path, monkeypatch, target, link_kind):
    import stat
    from types import SimpleNamespace
    path = material(tmp_path)
    (path / "metadata.json").write_text("{}")
    unsafe = path if target == "root" else path / target
    original_lstat = Path.lstat
    original_scandir = os.scandir
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)
    def link_info():
        return SimpleNamespace(
            st_mode=stat.S_IFLNK if link_kind == "symlink" else stat.S_IFDIR,
            st_file_attributes=0 if link_kind == "symlink" else 0x400)
    def lstat(self, *args, **kwargs):
        return link_info() if self == unsafe else original_lstat(self, *args, **kwargs)
    class Entries:
        def __enter__(self):
            return iter([SimpleNamespace(name="4K", path=str(unsafe),
                stat=lambda **kwargs: link_info())])
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(Path, "lstat", lstat)
    if target == "4K":
        monkeypatch.setattr(os, "scandir", lambda p: Entries() if p == path else original_scandir(p))
    result = inspect(path)
    if target == "metadata.json":
        assert result.metadata_status == "UNSAFE_FILE" and result.can_continue
    else:
        assert not result.can_continue and result.selected_zip_policy is None


def test_unreadable_metadata(tmp_path, monkeypatch):
    path = material(tmp_path)
    (path / "metadata.json").write_text("{}")
    original_open = Path.open
    def denied(self, *args, **kwargs):
        if self == path / "metadata.json":
            raise PermissionError("simulated read denial")
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    result = inspect(path)
    assert result.metadata_status == "UNREADABLE"
    assert result.metadata_exists and result.metadata_readable is False
    assert codes(result) == {"METADATA_UNREADABLE"}
    assert result.can_continue and not result.errors


def test_metadata_directory_and_size_limit(tmp_path):
    path = material(tmp_path)
    metadata = path / "metadata.json"
    metadata.mkdir()
    assert inspect(path).metadata_status == "UNSAFE_FILE"
    metadata.rmdir()
    metadata.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    result = inspect(path)
    assert result.metadata_status == "TOO_LARGE"
    assert result.can_continue


def test_path_guards_without_network_access(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected paths must not cause filesystem access")
    monkeypatch.setattr(Path, "lstat", forbidden)
    for path in ("//not-a-server/share/material", "relative/4K", tmp_path.parent / "outside"):
        result = inspect(path, tmp_path)
        assert not result.can_continue and result.metadata_status == "NOT_CHECKED"


def test_missing_material(tmp_path):
    result = inspect(tmp_path / "missing", tmp_path)
    assert not result.can_continue
    assert result.errors[0].code == "MATERIAL_INSPECTION_FAILED"


def test_material_is_file(tmp_path):
    path = tmp_path / "file"
    path.write_text("not a directory")
    result = inspect(path, tmp_path)
    assert not result.can_continue and result.selected_zip_policy is None


def test_material_unreadable(tmp_path, monkeypatch):
    path = material(tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError("directory listing denied")
    monkeypatch.setattr(os, "scandir", denied)
    result = inspect(path)
    assert not result.can_continue and result.selected_zip_policy is None
    assert result.errors[0].code == "MATERIAL_INSPECTION_FAILED"


def test_resolution_stat_failure(tmp_path, monkeypatch):
    path = material(tmp_path)
    original = Path.lstat
    def failed(self, *args, **kwargs):
        if self == path / "4K":
            raise PermissionError("master stat denied")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", failed)
    result = inspect(path)
    assert not result.can_continue and result.selected_zip_policy is None


def test_read_only_source_snapshot(tmp_path, monkeypatch):
    path = material(tmp_path, ("4K", "8K", "16K"))
    (path / "metadata.json").write_text(json.dumps(VALID), encoding="utf-8")
    for name in ("SOURCE", "PREVIEW"):
        (path / name).mkdir()
        (path / name / "file with spaces.txt").write_bytes(b"unchanged")
    for resolution in ("4K", "8K", "16K"):
        (path / resolution / "map with spaces.jpg").write_bytes(b"synthetic map")

    def snapshot():
        return {str(item.relative_to(path)): (
            item.stat().st_mtime_ns, item.stat().st_ctime_ns,
            item.read_bytes() if item.is_file() else None)
            for item in [path, *path.rglob("*")]}

    before = snapshot()
    def forbidden(*args, **kwargs):
        pytest.fail("Mutation attempted")
    with monkeypatch.context() as patch:
        for name in ("rename", "replace", "remove", "unlink", "mkdir", "rmdir", "utime"):
            patch.setattr(os, name, forbidden)
        original_open = Path.open
        def read_only(self, mode="r", *args, **kwargs):
            assert mode in ("r", "rb")
            return original_open(self, mode, *args, **kwargs)
        patch.setattr(Path, "open", read_only)
        assert inspect(path).can_continue
        assert inspect(path).can_continue
    assert snapshot() == before
