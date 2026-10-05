"""Names-only record through completion and recoverable real NTFS folder creation."""
import os
from uuid import uuid4
import pytest
from app.local_materials import LocalMaterialLibrary
from app.main import create_app
from test_application_access import access_case  # noqa: F401

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows NTFS folder adapter")


def test_draft_to_identity_to_owned_folder_recovers_atomic_move_without_new_record(access_case, tmp_path, monkeypatch):
    root = tmp_path / "materials"; root.mkdir()
    library = LocalMaterialLibrary(root, tmp_path / "journal")
    case = access_case
    case.app = create_app(case.app.state.settings, case.database, case.worker, local_library=library)
    with case.client("ADMIN") as client:
        names = {"idempotency_key": str(uuid4()), "names": ["Real Draft"]}
        draft = client.post("/api/material-create-batches", json=names)
        assert draft.status_code == 200, draft.text
        identifier = draft.json()["items"][0]["material_id"]
        assert not list(root.iterdir()) and not list(library.journal.root.iterdir())
        url = "/api/materials/" + identifier
        for field, value in (("main_category_code", "G03"), ("published_brand_id", str(case.materials[0].published_brand_id))):
            material = client.get(url).json()
            response = client.patch(url + "/table", json={"expected_updated_at": material["updated_at"], field: value}, headers={"Idempotency-Key": str(uuid4())})
            assert response.status_code == 200, response.text
        complete = response.json()
        assert complete["technical_identity"] == "SAFE_0003_REAL-DRAFT_G03" and complete["folder_path"] is None
        assert not list(root.iterdir())
        request = {"idempotency_key": str(uuid4()), "expected_updated_at": complete["updated_at"]}
        rename = library.journal.rename_handle
        def crash_after_move(*args):
            rename(*args)
            raise OSError("Synthetic interruption after atomic move")
        monkeypatch.setattr(library.journal, "rename_handle", crash_after_move)
        interrupted = client.post(url + "/create-folder", json=request)
        assert interrupted.status_code == 200 and interrupted.json()["status"] == "PARTIAL", interrupted.text
        folder = root / "SAFE" / complete["technical_identity"]
        assert {child.name for child in folder.iterdir()} == {"PREVIEW", "SOURCE"}
        assert client.get(url).json()["folder_path"] is None
        monkeypatch.setattr(library.journal, "rename_handle", rename)
        resumed = client.post(url + "/create-folder", json=request)
        assert resumed.status_code == 200 and resumed.json()["status"] == "COMPLETED", resumed.text
        user_file = folder / "SOURCE" / "user-file.txt"; user_file.write_text("retained user data")
        assert client.post(url + "/create-folder", json=request).json() == resumed.json()
        assert user_file.read_text() == "retained user data"
        material = client.get(url).json()
        assert material["sequence_number"] == 3 and material["folder_path"] == "SAFE/SAFE_0003_REAL-DRAFT_G03"
        assert client.post("/api/material-create-batches", json=names).json() == draft.json()
        assert len(client.get("/api/materials").json()) == 3
