"""Native read-only preflight and actual API Done transitions, never live data."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.db.models import PBRMaterial
from app.local_filesystem import LocalFilesError
from app.local_materials import LocalMaterialLibrary
from app.main import create_app
from app.worker_client import WorkerUnavailableError
from test_application_access import access_case  # noqa: F401
from test_material_operations import _preflight

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows filesystem adapter")
FOLDER = "Brand/BRAND_0001_NAME_G03"
RAW = b'{"COLOR":{"hex":"aabbcc"},"TEXTURE_SIZE":{"cm":{"width":12.5,"height":4}}}'


@pytest.fixture
def library(tmp_path):
    (tmp_path / "library" / FOLDER / "8K").mkdir(parents=True)
    return LocalMaterialLibrary(tmp_path / "library", tmp_path / "journal")


def test_local_preflight_reads_only_metadata_and_chooses_highest_numeric_resolution(library, monkeypatch):
    folder = library.fs.path(FOLDER)
    (folder / "metadata.json").write_bytes(RAW)
    (folder / "4K").mkdir(); (folder / "12K").mkdir()
    (folder / "16K").write_bytes(b"not a directory")
    texture = folder / "12K" / "large-map.png"; texture.write_bytes(b"never decoded or hashed")
    timestamp = int(datetime(2026, 3, 4, tzinfo=ZoneInfo("Europe/Prague")).timestamp() * 1_000_000_000)
    os.utime(folder / "12K", ns=(timestamp, timestamp))
    reads = []; read = library.fs.read
    def observed(relative, **kwargs):
        reads.append(relative); return read(relative, **kwargs)
    monkeypatch.setattr(library.fs, "read", observed)
    monkeypatch.setattr(library.fs, "tree", lambda *a, **kw: pytest.fail("Preflight must not walk or hash the complete tree"))
    result = library.preflight(FOLDER)
    assert result.can_continue and result.master_resolution == "12K"
    assert result.policy.value == "CURRENT_ON_OR_AFTER_2026_03_04"
    assert result.metadata_status.value == "VALID" and result.source_filename == "metadata.json"
    assert result.hex_color == "#AABBCC" and result.width_cm == Decimal("12.5") and result.height_cm == Decimal("4")
    assert result.sha256 == hashlib.sha256(RAW).hexdigest()
    assert [item.code for item in result.warnings] == ["NON_STANDARD_RESOLUTION"]
    assert reads == [FOLDER + "/metadata.json"]
    assert texture.read_bytes() == b"never decoded or hashed" and (folder / "metadata.json").read_bytes() == RAW
    assert not list(library.journal.root.iterdir())
    os.utime(folder / "12K", ns=(timestamp - 100, timestamp - 100))
    assert library.preflight(FOLDER).policy.value == "LEGACY_BEFORE_2026_03_04"


@pytest.mark.parametrize("raw,status", [(None, "MISSING"), (b"not JSON", "INVALID"), (b"\xff", "INVALID"),
    (b"x" * (4 * 1024 * 1024 + 1), "INVALID"), (b'{"COLOR":{"hex":"FFFFFF"},"TEXTURE_SIZE":{"cm":{"width":1e-999999999,"height":1e999999999}}}', "WARNING"),
    (b'{"TEXTURE_SIZE":{"cm":{"width":1e99999999999999999999999999999999999999999999}}}', "INVALID")],
    ids=["missing", "invalid-json", "invalid-utf8", "oversized", "out-of-range-exponents", "invalid-exponent"])
def test_missing_invalid_or_extreme_metadata_is_advisory_and_unmodified(library, raw, status):
    metadata = library.fs.path(FOLDER) / "metadata.json"
    if raw is not None: metadata.write_bytes(raw)
    result = library.preflight(FOLDER)
    assert result.can_continue and not result.errors and result.metadata_status.value == status
    assert result.metadata_warnings or result.metadata_errors
    assert metadata.read_bytes() == raw if raw is not None else not metadata.exists()
    assert not list(library.journal.root.iterdir())


def test_exact_dimensions_with_long_trailing_zeros_remain_valid(library):
    (library.fs.path(FOLDER) / "metadata.json").write_bytes(RAW.replace(b"12.5", b"12.5000000000000000000000000000000000"))
    result = library.preflight(FOLDER)
    assert result.can_continue and result.metadata_status.value == "VALID" and result.width_cm == Decimal("12.5")


def test_txt_fallback_only_when_canonical_json_is_absent(library):
    folder = library.fs.path(FOLDER)
    (folder / "metadata.txt").write_text("texture size: 20.5x10 cm", encoding="utf-8")
    result = library.preflight(FOLDER)
    assert result.can_continue and result.source_filename == "metadata.txt" and result.width_cm == Decimal("20.5")
    (folder / "metadata.json").write_bytes(b"broken")
    result = library.preflight(FOLDER)
    assert result.can_continue and result.source_filename == "metadata.json" and result.metadata_status.value == "INVALID"
    assert result.width_cm is None


def test_hardlinked_metadata_is_never_read_or_replaced_by_legacy_fallback(library, tmp_path):
    outside = tmp_path / "outside.json"; outside.write_bytes(RAW)
    folder = library.fs.path(FOLDER)
    os.link(outside, folder / "metadata.json")
    (folder / "metadata.txt").write_text("texture size: 20x10 cm", encoding="utf-8")
    result = library.preflight(FOLDER)
    assert result.can_continue and result.metadata_status.value == "INVALID"
    assert result.sha256 is None and result.raw_content is None and result.width_cm is None
    assert result.source_filename == "metadata.json" and outside.read_bytes() == RAW


def test_no_resolution_and_unsafe_resolution_keep_done_blocked(library, monkeypatch):
    folder = library.fs.path(FOLDER); (folder / "8K").rmdir()
    (folder / "PREVIEW").mkdir(); (folder / "SOURCE").mkdir(); (folder / "metadata.json").write_bytes(RAW)
    result = library.preflight(FOLDER)
    assert not result.can_continue and [item.code for item in result.errors] == ["NO_RESOLUTION"]
    (folder / "8K").mkdir()
    opened = library.fs.opened
    @contextmanager
    def rejected_resolution(path, **kwargs):
        if path.name == "8K": raise LocalFilesError("LOCAL_PATH_UNSAFE")
        with opened(path, **kwargs) as handle: yield handle
    monkeypatch.setattr(library.fs, "opened", rejected_resolution)
    result = library.preflight(FOLDER)
    assert not result.can_continue and "UNSAFE_RESOLUTION" in [item.code for item in result.errors]


@pytest.mark.parametrize("folder", ["../outside", "C:/outside", "Brand/../../outside", ""])
def test_unsafe_relative_paths_rejected_before_file_reads(library, folder, monkeypatch):
    monkeypatch.setattr(library.fs, "read", lambda *a, **kw: pytest.fail("unsafe path read"))
    with pytest.raises(WorkerUnavailableError): library.preflight(folder)


def test_actual_local_injection_preflight_done_checked_and_correction_roundtrip(access_case, tmp_path, monkeypatch):
    case = access_case; material_id = case.materials[0].id
    folder = "Brand/" + case.materials[0].technical_identity
    (tmp_path / "library" / folder / "8K").mkdir(parents=True)
    metadata = tmp_path / "library" / folder / "metadata.json"; metadata.write_bytes(RAW)
    library = LocalMaterialLibrary(tmp_path / "library", tmp_path / "journal")
    with case.database.session() as session:
        session.get(PBRMaterial, material_id).folder_path = folder; session.commit()
    settings = case.app.state.settings.model_copy(update={"worker_base_url": "http://127.0.0.1:1"})
    monkeypatch.setattr("app.worker_client.WorkerClient.preflight", lambda *a: pytest.fail("Remote worker must not be called"))
    case.app = create_app(settings, case.database, local_library=library)
    with case.client("ADMIN") as client:
        url = f"/api/materials/{material_id}"
        before = client.get(url).json()
        result = client.post(url + "/folder-preflight", json={"folder_path": folder})
        assert result.status_code == 200 and result.json()["can_continue"], result.text
        assert client.get(url).json() == before
        def update(field, value):
            current = client.get(url).json()
            return client.patch(url + "/table", json={"expected_updated_at": current["updated_at"], field: value},
                headers={"Idempotency-Key": str(uuid4())})
        for field, value in (("workflow_status", "DONE"), ("checked_status", "OK"), ("checked_status", "Correction")):
            changed = update(field, value); assert changed.status_code == 200, changed.text
        rejected = update("checked_status", "OK")
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "CHECKED_REQUIRES_DONE"
        assert update("workflow_status", "DONE").status_code == 200
        checked = update("checked_status", "OK")
        assert checked.status_code == 200 and checked.json()["checked_status"] == "OK"
        assert checked.json()["automatic_file_check_status"] == "NOT_CHECKED" and not checked.json()["is_published"]
    assert metadata.read_bytes() == RAW and not list(library.journal.root.iterdir())


def test_explicit_worker_injection_keeps_precedence_over_local_library(access_case, library, monkeypatch):
    case = access_case
    monkeypatch.setattr(library, "preflight", lambda *a: pytest.fail("Explicit worker must take precedence"))
    case.worker.queue(_preflight(case.materials[0].technical_identity))
    case.app = create_app(case.app.state.settings, case.database, case.worker, local_library=library)
    with case.client("ADMIN") as client:
        result = client.post(f"/api/materials/{case.materials[0].id}/folder-preflight",
            json={"folder_path": "Brand/" + case.materials[0].technical_identity})
        assert result.status_code == 200, result.text
    assert len(case.worker.calls) == 1
