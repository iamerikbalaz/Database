import hashlib
import os
from uuid import uuid4, uuid5

import pytest
from PIL import Image

from app.local_filesystem import LocalFilesError
from app.local_materials import LocalMaterialLibrary
from app.local_preview_edits import LocalPreviewEdits
from app.preview_edits import changes_for
from app.db.models import PBRMaterial
from app.main import create_app
from test_application_access import access_case

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PREVIEW editing capability")


@pytest.fixture
def local(tmp_path):
    root = tmp_path / "materials"; preview = root / "BRAND/BRAND_0001_TEST_G03/PREVIEW"; preview.mkdir(parents=True)
    for i, name in enumerate(("SPHERE_1.png", "SPHERE_2.png", "OLD_1.png")):
        Image.new("RGB", (16, 16), (i * 50, 100, 180)).save(preview / name)
    (preview / "keep.txt").write_bytes(b"out of scope")
    (preview.parent / "texture_map.png").write_bytes(b"outside PREVIEW")
    library = LocalMaterialLibrary(root, tmp_path / "journal")
    adapter = LocalPreviewEdits(library)
    return library, adapter, preview


def item(adapter, **changes):
    snapshot = adapter.snapshot("BRAND/BRAND_0001_TEST_G03")
    selector = {"action": "BULK", "find": "SPHERE", "replace": "FABRIC", "delete_containing": "OLD", "case_sensitive": True, **changes}
    planned, issues = changes_for(snapshot, selector)
    assert not issues
    return {"material_id": str(uuid4()), "folder_path": "BRAND/BRAND_0001_TEST_G03", "snapshot": snapshot, "changes": planned}


def test_combined_rename_delete_quarantine_and_replay_touch_only_selected_previews(local):
    library, adapter, preview = local; planned = item(adapter); operation = uuid4()
    original = {path.name: path.read_bytes() for path in preview.iterdir()}
    result = adapter.execute(operation, planned)
    assert result == {"status": "COMPLETED", "error_code": None, "renamed": 2, "deleted": 1}
    assert {path.name for path in preview.iterdir()} == {"FABRIC_1.png", "FABRIC_2.png", "keep.txt"}
    assert (preview / "FABRIC_1.png").read_bytes() == original["SPHERE_1.png"]
    quarantine = library.journal.root / str(uuid5(operation, planned["material_id"])) / "quarantine"
    assert [path.read_bytes() for path in quarantine.iterdir()] == [original["OLD_1.png"]]
    assert adapter.execute(operation, planned) == result
    assert (preview / "keep.txt").read_bytes() == b"out of scope"
    assert (preview.parent / "texture_map.png").read_bytes() == b"outside PREVIEW"


@pytest.mark.parametrize("phase", ["staging", "applying"])
def test_crash_after_rename_recovers_without_overwrite(local, monkeypatch, phase):
    library, adapter, preview = local; planned = item(adapter); operation = uuid4()
    fs = library.fs if phase == "staging" else library.journal
    original = fs.rename_handle; count = 0
    def interrupted(handle, target):
        nonlocal count
        original(handle, target); count += 1
        if count == 1: raise OSError("synthetic crash after successful move")
    monkeypatch.setattr(fs, "rename_handle", interrupted)
    with pytest.raises(OSError): adapter.execute(operation, planned)
    monkeypatch.setattr(fs, "rename_handle", original)
    assert adapter.execute(operation, planned)["status"] == "COMPLETED"
    assert {path.name for path in preview.iterdir()} == {"FABRIC_1.png", "FABRIC_2.png", "keep.txt"}


def test_case_only_rename_uses_staging_and_preserves_bytes(local):
    _, adapter, preview = local
    planned = item(adapter, action="RENAME", filename="SPHERE_1.png", new_name="sphere_1.PNG")
    original = (preview / "SPHERE_1.png").read_bytes()
    assert adapter.execute(uuid4(), planned)["renamed"] == 1
    assert "sphere_1.PNG" in [path.name for path in preview.iterdir()]
    assert (preview / "sphere_1.PNG").read_bytes() == original


def test_corrupt_png_can_be_quarantined_without_decoding_or_touching_other_extensions(local):
    library, adapter, preview = local
    damaged = b"\x89PNG\r\n\x1a\ntruncated image data\x00\xff"
    (preview / "damaged.png").write_bytes(damaged)
    (preview / "damaged.jpg").write_bytes(damaged)
    planned = item(adapter, action="DELETE_MATCHING", find="damaged", delete_containing=None)
    assert planned["changes"] == [{"from": "damaged.png", "to": None}]
    assert "damaged.jpg" not in {entry["name"] for entry in planned["snapshot"]["files"]}
    operation = uuid4()
    assert adapter.execute(operation, planned)["deleted"] == 1
    assert not (preview / "damaged.png").exists()
    assert (preview / "damaged.jpg").read_bytes() == damaged
    quarantine = library.journal.root / str(uuid5(operation, planned["material_id"])) / "quarantine/0000.png"
    assert quarantine.read_bytes() == damaged
    assert hashlib.sha256(quarantine.read_bytes()).hexdigest() == hashlib.sha256(damaged).hexdigest()


def test_authenticated_api_uses_real_preview_handles_and_retained_quarantine(access_case, local):
    case = access_case; library, _, preview = local
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.folder_path = "BRAND/BRAND_0001_TEST_G03"
        material.checked_status = "OK"; material.is_published = True
        session.commit()
    settings = case.app.state.settings.model_copy(update={"source_mutations_enabled": True})
    case.app = create_app(settings, case.database, case.worker, local_library=library)
    material_id = str(case.materials[0].id)
    original = (preview / "SPHERE_1.png").read_bytes()
    with case.client("ADMIN") as client:
        selected = client.get("/api/materials/" + material_id).json()
        payload = {"materials": [{"id": material_id, "expected_updated_at": selected["updated_at"]}],
            "action": "DELETE", "filename": "SPHERE_1.png"}
        planned = client.post("/api/material-preview-edits/plan", json=payload)
        assert planned.status_code == 200 and planned.json()["can_apply"], planned.text
        body = {**payload, "confirmed": True, "idempotency_key": str(uuid4()),
            "expected_proposal_hash": planned.json()["proposal_hash"]}
        applied = client.post("/api/material-preview-edits", json=body)
        assert applied.status_code == 200 and applied.json()["status"] == "COMPLETED", applied.text
        assert client.post("/api/material-preview-edits", json=body).json() == applied.json()
        current = client.get("/api/materials/" + material_id).json()
        assert not current["is_published"] and current["checked_status"] == "no"
        activity = client.get("/api/materials/" + material_id + "/activity").json()["items"]
        assert any(event["action"] == "PREVIEW_FILES_EDITED" and event["author"]["display_name"] == "ADMIN" for event in activity)
    assert not (preview / "SPHERE_1.png").exists()
    deleted = list(library.journal.root.glob("*/quarantine/*.png"))
    assert len(deleted) == 1 and deleted[0].read_bytes() == original
    assert (preview.parent / "texture_map.png").read_bytes() == b"outside PREVIEW"


def test_external_file_change_rejects_before_any_mutation(local):
    _, adapter, preview = local; planned = item(adapter)
    (preview / "SPHERE_1.png").write_bytes(b"external edit")
    result = adapter.execute(uuid4(), planned)
    assert result == {"status": "REJECTED", "error_code": "PREVIEW_SOURCE_CHANGED"}
    assert {path.name for path in preview.iterdir()} == set(planned["snapshot"]["names"])


def test_foreign_target_added_during_recovery_is_never_overwritten(local, monkeypatch):
    library, adapter, preview = local; planned = item(adapter); operation = uuid4()
    original = library.journal.rename_handle
    monkeypatch.setattr(library.journal, "rename_handle", lambda *_: (_ for _ in ()).throw(OSError("before final rename")))
    with pytest.raises(OSError): adapter.execute(operation, planned)
    (preview / "FABRIC_1.png").write_bytes(b"foreign")
    monkeypatch.setattr(library.journal, "rename_handle", original)
    with pytest.raises(LocalFilesError): adapter.execute(operation, planned)
    assert (preview / "FABRIC_1.png").read_bytes() == b"foreign"


def test_delete_precedence_and_literal_stem_matching(local):
    _, adapter, _ = local
    planned = item(adapter, find="SPHERE", replace="NEW", delete_containing="SPHERE_1")
    assert planned["changes"] == [{"from": "SPHERE_1.png", "to": None}, {"from": "SPHERE_2.png", "to": "NEW_2.png"}]
    snapshot = adapter.snapshot("BRAND/BRAND_0001_TEST_G03")
    changes, issues = changes_for(snapshot, {"action": "DELETE_MATCHING", "find": ".png", "case_sensitive": True})
    assert changes == [] and issues == []
