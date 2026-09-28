"""Owned temporary folders only; no NAS, network, or configured source roots."""
import hashlib
import json
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.api import create_app
from app.file_journal import JournalError
from app.metadata_edit import execute_metadata_edit, inspect_metadata, normalize_values, rewrite_metadata

VALUES = {"hex_color": "#abcdef", "width_cm": "12.5", "height_cm": "34"}


@pytest.fixture
def folders(tmp_path):
    root = tmp_path / "source"; root.mkdir()
    material = root / "TEST_0001_G01"; material.mkdir()
    journal = tmp_path / "journal"; journal.mkdir(mode=0o700)
    return root, material, journal


def save(folders, key=None, expected=None, values=None, enabled=True):
    root, material, journal = folders
    return execute_metadata_edit(root, journal, key or str(uuid4()), (material.name,), expected, values or VALUES, enabled=enabled)


def test_create_root_only_and_replay_after_lost_response(folders):
    root, material, journal = folders
    nested = material / "16K"; nested.mkdir(); (nested / "metadata.txt").write_text("untouched")
    key = str(uuid4()); result = save(folders, key)
    info = (material / "metadata.txt").stat()
    assert result["status"] == "COMPLETED"
    assert result["metadata"]["hex_color"] == "#ABCDEF"
    assert info.st_mode & 0o777 == 0o644
    assert save(folders, key) == result
    assert (material / "metadata.txt").stat().st_mtime_ns == info.st_mtime_ns
    assert (nested / "metadata.txt").read_text() == "untouched"
    assert sorted(p.name for p in material.iterdir()) == ["16K", "metadata.txt"]


def test_preserve_unknown_json_keys_and_exact_numbers():
    raw = b'{"FOLDER":"IDENTITY","COLOR":{"measured_from":"x"},"TEXTURE_SIZE":{"px":{"width":8192},"cm":{"other":7}},"unknown":{"exact":0.123456789012345678901234567890}}'
    result = rewrite_metadata(raw, VALUES)
    assert b'0.123456789012345678901234567890' in result
    parsed = json.loads(result)
    assert parsed["FOLDER"] == "IDENTITY"
    assert parsed["COLOR"]["measured_from"] == "x"
    assert parsed["TEXTURE_SIZE"]["cm"]["other"] == 7
    assert parsed["TEXTURE_SIZE"]["px"] == {"width": 8192}


def test_legacy_lines_preserved_during_conversion():
    raw = b"texture size: 12x34 cm\r\ncustom: preserved\r\n"
    result = json.loads(rewrite_metadata(raw, VALUES))
    assert result["LEGACY_SOURCE_TEXT"] == raw.decode()


def test_hash_conflict_is_durable_and_never_writes(folders):
    _, material, _ = folders
    source = material / "metadata.txt"; source.write_bytes(b"texture size: 12x34 cm")
    key = str(uuid4()); before = source.stat()
    result = save(folders, key)
    assert result["status"] == "REJECTED"
    source.unlink()
    assert save(folders, key) == result and not source.exists()
    assert before.st_size > 0


def test_same_key_different_values_is_rejected(folders):
    key = str(uuid4()); save(folders, key)
    with pytest.raises(JournalError, match="JOURNAL_REQUEST_CONFLICT"):
        save(folders, key, values={**VALUES, "width_cm": "77"})


def test_recover_crash_after_atomic_replace(folders, monkeypatch):
    import app.metadata_edit as module
    def crash(): raise RuntimeError("synthetic interrupted response")
    monkeypatch.setattr(module, "_after_replace", crash)
    key = str(uuid4())
    with pytest.raises(RuntimeError): save(folders, key)
    source = folders[1] / "metadata.txt"; before = source.stat()
    monkeypatch.setattr(module, "_after_replace", lambda: None)
    assert save(folders, key)["status"] == "COMPLETED"
    assert source.stat().st_ino == before.st_ino


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "oversized"])
def test_unsafe_metadata_never_modified(folders, tmp_path, kind):
    _, material, _ = folders
    source = material / "metadata.txt"; outside = tmp_path / "outside.txt"; outside.write_bytes(b"private")
    if kind == "symlink": source.symlink_to(outside)
    elif kind == "hardlink": os.link(outside, source)
    elif kind == "fifo": os.mkfifo(source)
    elif kind == "directory": source.mkdir()
    else: source.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    assert save(folders)["status"] == "REJECTED"
    assert outside.read_bytes() == b"private"


@pytest.mark.parametrize("patch", [{"hex_color": "red"}, {"width_cm": True}, {"height_cm": "1e2"}, {"width_cm": "0"}, {"height_cm": "0.00001"}, {"width_cm": "100000000"}, {"FOLDER": "changed"}])
def test_strict_values(patch):
    with pytest.raises(JournalError): normalize_values({**VALUES, **patch})


@pytest.mark.parametrize("raw", [b'{"COLOR":{},"COLOR":{}}', b'{"WEB_APP_PART":{}}', b'{"COLOR":false}', b'{"unknown":NaN}', b'not supported'])
def test_unsupported_sources_are_preserved(raw):
    with pytest.raises(JournalError): rewrite_metadata(raw, VALUES)


def test_gate_and_traversal_fail_before_mutation(folders):
    with pytest.raises(JournalError, match="SOURCE_MUTATIONS_DISABLED"): save(folders, enabled=False)
    root, _, journal = folders
    with pytest.raises(JournalError): execute_metadata_edit(root, journal, str(uuid4()), ("..",), None, VALUES, enabled=True)
    assert list(folders[1].iterdir()) == []


def test_existing_file_mode_and_inspection_preserved(folders):
    root, material, _ = folders
    raw = b"texture size: 12x34 cm"; source = material / "metadata.txt"; source.write_bytes(raw); source.chmod(0o640)
    before = source.stat()
    assert inspect_metadata(root, (material.name,))["editable"]
    assert source.stat().st_mtime_ns == before.st_mtime_ns
    save(folders, expected=hashlib.sha256(raw).hexdigest())
    assert source.stat().st_mode & 0o777 == 0o640


def test_worker_api_requires_gate_token_and_exact_fields(folders):
    root, material, journal = folders
    request = {"folder_path": material.name, "operation_id": str(uuid4()), "expected_sha256": None, "values": VALUES}
    with TestClient(create_app(root, mutations_enabled=True, mutation_token="s" * 40, journal_root=journal)) as client:
        assert client.post("/internal/material-metadata", json={"folder_path": material.name}).json()["writes_enabled"]
        assert client.post("/internal/material-metadata-edit", json=request).status_code == 401
        response = client.post("/internal/material-metadata-edit", json=request, headers={"Authorization": "Bearer " + "s" * 40})
        assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    with TestClient(create_app(root, mutations_enabled=False)) as client:
        assert client.post("/internal/material-metadata-edit", json=request).status_code == 503


def test_json_is_created_beside_untouched_legacy_and_receipt_binds_filename(folders):
    root, material, journal = folders
    legacy = material / "metadata.txt"; legacy.write_bytes(b"texture size: 1x2 cm")
    identity = {"FOLDER": material.name, "MANUFACTURER": "Test", "PRODUCT_NUMBER": "0001",
        "PRODUCT_NAME": "Test sample", "CATEGORY": "G01", "BASE_NAME": material.name.rsplit("_", 1)[0]}
    key = str(uuid4())
    result = execute_metadata_edit(root, journal, key, (material.name,), None, VALUES,
        enabled=True, source_filename="metadata.json", identity=identity)
    assert result["status"] == "COMPLETED"
    assert result["metadata"]["source_filename"] == "metadata.json"
    assert json.loads((material / "metadata.json").read_bytes())["FOLDER"] == material.name
    assert legacy.read_bytes() == b"texture size: 1x2 cm"
    assert inspect_metadata(root, (material.name,))["sha256"] == result["metadata"]["sha256"]
    assert execute_metadata_edit(root, journal, key, (material.name,), None, VALUES,
        enabled=True, source_filename="metadata.json", identity=identity) == result
    with pytest.raises(JournalError, match="JOURNAL_REQUEST_CONFLICT"):
        save(folders, key)


def test_json_creation_enriches_complete_template_from_verified_filename_inventory(folders):
    root, material, journal = folders
    (material / "8K").mkdir(); (material / "SOURCE").mkdir()
    for shortcut in ("COL", "DIFF", "ID", "SPEC"):
        (material / "8K" / f"TEST_0001_{shortcut}_8K.jpg").write_bytes(b"Not decoded or copied as image measurements")
    (material / "SOURCE" / f"{material.name}.sbs").write_bytes(b"Not decoded or used as sample-size provenance")
    identity = {"FOLDER": material.name, "MANUFACTURER": "Test", "PRODUCT_NUMBER": "0001",
        "PRODUCT_NAME": "Test sample", "CATEGORY": "G01", "BASE_NAME": "TEST_0001"}
    result = execute_metadata_edit(root, journal, str(uuid4()), (material.name,), None, VALUES,
        enabled=True, source_filename="metadata.json", identity=identity)
    assert result["status"] == "COMPLETED"
    document = json.loads((material / "metadata.json").read_bytes())
    assert document["MAPS_SHORTCUTS"] == ["COL", "DIFF", "ID", "SPEC"]
    assert document["RESOLUTIONS"]["8K"] == {"MAPS_SHORTCUTS": ["COL", "DIFF", "ID", "SPEC"], "FILE_COUNT": 4, "UNRECOGNIZED": []}
    assert document["SOURCE"] == {"CROPS": [], "SBS": f"SOURCE/{material.name}.sbs", "REFERENCES": False, "ARCHIVE": False}
    assert document["TEXTURE_SIZE_SOURCE"] is None and document["COLOR"]["measured_from"] is None
    assert document["COLOR"]["runner_up"] == {"hex": None, "delta_e": None}


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_json_generation_rejects_unsafe_inventory_without_writing_metadata(folders, tmp_path, kind):
    root, material, journal = folders
    outside = tmp_path / "outside"; outside.write_bytes(b"Do not read")
    entry = material / "unsafe"
    if kind == "symlink": entry.symlink_to(outside)
    elif kind == "hardlink": os.link(outside, entry)
    else: os.mkfifo(entry)
    result = execute_metadata_edit(root, journal, str(uuid4()), (material.name,), None, VALUES,
        enabled=True, source_filename="metadata.json")
    assert result["status"] == "REJECTED" and result["failure_code"] == "SOURCE_METADATA_UNSAFE_FILE"
    assert not (material / "metadata.json").exists() and outside.read_bytes() == b"Do not read"
