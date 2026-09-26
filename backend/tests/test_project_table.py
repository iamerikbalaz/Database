from uuid import uuid4
import pytest
from app.project_import import parse_project_folder, plan_project_import, manufacturer_key
from test_application_access import access_case  # noqa: F401

ROOT = r"R:\0. PROJECTS"
NAME = "0246_ENGLISH-DEKOR_VISUALIZATIONS+SCANNING_FABRICS_032026"


def test_project_folder_parser_keeps_original_identity_and_never_infers_status():
    folder = parse_project_folder(NAME, ROOT + "\\" + NAME, ROOT)
    assert folder.project_number == "0246" and folder.manufacturer == "ENGLISH-DEKOR"
    assert folder.job_type == "VISUALIZATIONS+SCANNING" and folder.material_specifier == "FABRICS"
    assert folder.folder_month == "2026-03" and folder.name == NAME
    assert manufacturer_key("English-Dekor") == manufacturer_key(" ENGLISH DEKOR ")
    for name in ("246_ENGLISH_DEKOR_FABRICS_032026", NAME.replace("032026", "132026"), NAME.replace("032026", "82026")):
        with pytest.raises(ValueError): parse_project_folder(name, ROOT + "\\" + name, ROOT)
    with pytest.raises(ValueError): parse_project_folder(NAME, ROOT + "\\other\\" + NAME, ROOT)
    with pytest.raises(ValueError): parse_project_folder(NAME, "Y:\\0. PROJECTS\\" + NAME, ROOT)


def test_legacy_project_missing_specifier_requires_explicit_option():
    name = "0247_EXAMPLE_SCANNING_032026"
    with pytest.raises(ValueError): parse_project_folder(name, ROOT + "\\" + name, ROOT)
    parsed = parse_project_folder(name, ROOT + "\\" + name, ROOT, allow_legacy=True)
    assert parsed.material_specifier is None and parsed.warning == "LEGACY_MISSING_SPECIFIER"


def test_import_plan_is_deterministic_idempotent_and_quarantines_duplicate_numbers():
    folders = [{"name": NAME, "path": ROOT + "\\" + NAME}]
    plan = plan_project_import(folders, [], root=ROOT)
    assert len(plan["create"]) == 1 and not plan["conflicts"]
    existing = [{"id": "existing", "project_number": "0246", "folder_path": folders[0]["path"]}]
    assert len(plan_project_import(folders, existing, root=ROOT)["existing"]) == 1
    assert len(plan_project_import(folders, [{**existing[0], "folder_path": None}], root=ROOT)["link"]) == 1
    assert plan_project_import(folders, [{**existing[0], "folder_path": "R:\\Other"}], root=ROOT)["conflicts"][0]["reason"] == "PROJECT_NUMBER_FOLDER_CONFLICT"
    legacy = "0246_OTHER_SCANNING_032026"
    duplicate = folders + [{"name": legacy, "path": ROOT + "\\" + legacy}]
    conflict = plan_project_import(duplicate, [], root=ROOT, allow_legacy=True)
    assert not conflict["create"] and len(conflict["conflicts"]) == 2
    assert conflict == plan_project_import(list(reversed(duplicate)), [], root=ROOT, allow_legacy=True)


@pytest.mark.parametrize("resource,change", [("projects", {"notes": "#sample imported"}), ("companies", {"country": "Czech Republic"})])
def test_table_optimistic_writes_and_same_key_recovery(access_case, resource, change):
    with access_case.client("ADMIN") as client:
        row = client.get("/api/" + resource).json()[0]
        key = str(uuid4()); payload = {"expected_updated_at": row["updated_at"], **change}
        path = f"/api/{resource}/{row['id']}"
        saved = client.patch(path, json=payload, headers={"Idempotency-Key": key})
        assert saved.status_code == 200, saved.text
        assert saved.json()["updated_at"] != row["updated_at"]
        assert client.patch(path, json=payload, headers={"Idempotency-Key": str(uuid4())}).status_code == 409
        replay = client.patch(path, json=payload, headers={"Idempotency-Key": key})
        assert replay.json() == saved.json() and replay.headers["Idempotency-Replayed"] == "true"
        assert client.get("/api/resource-commands/" + key).json()["response"] == saved.json()
    with access_case.client("PROCESSOR") as client:
        assert client.patch(path, json={**change, "expected_updated_at": saved.json()["updated_at"]}, headers={"Idempotency-Key": str(uuid4())}).status_code == 403


def test_project_folder_reference_is_audited_searchable_and_does_not_call_worker(access_case):
    with access_case.client("ADMIN") as client:
        company = client.get("/api/companies").json()[0]
        payload = {"company_id": company["id"], "project_number": "0246", "name": NAME, "folder_path": ROOT + "\\" + NAME}
        created = client.post("/api/projects", json=payload, headers={"Idempotency-Key": str(uuid4())})
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["status"] == "NOT_STARTED" and row["folder_path"] == payload["folder_path"]
        history = client.get(f"/api/projects/{row['id']}/history").json()
        assert history["items"][0]["after"]["folder_path"] == payload["folder_path"]
        assert row["id"] in {item["id"] for item in client.get("/api/projects", params={"search": "R:\\0. PROJECTS"}).json()}
    assert not access_case.worker.calls
