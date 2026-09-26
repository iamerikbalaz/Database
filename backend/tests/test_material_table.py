"""Human tracking uses actual sessions, revision checks and durable receipts."""
from uuid import uuid4
from sqlalchemy import func, select

from app.db.models import PBRMaterial, ResourceChangeEvent, ResourceCommand, PBRMaterialMetadataSnapshot
from test_application_access import access_case  # noqa: F401
from test_material_operations import _preflight


def write(client, material, change, key=None):
    return client.patch(f"/api/materials/{material['id']}/table",
        json={"expected_updated_at": material["updated_at"], **change}, headers={"Idempotency-Key": str(key or uuid4())})


def test_note_tags_search_is_literal_case_insensitive_and_keeps_assignment_scope(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        first, second = [client.get(f"/api/materials/{row.id}").json() for row in case.materials]
        assert write(client, first, {"note": "Review\n#Release_100% #Autumn"}).status_code == 200
        assert write(client, second, {"note": "#ReleaseX100Y #Autumn"}).status_code == 200
        exact = client.get("/api/materials", params={"search": "#release_100%"})
        assert exact.status_code == 200
        assert [row["id"] for row in exact.json()] == [first["id"]]
        assert len(client.get("/api/materials", params={"search": "#autumn"}).json()) == 2
        assert client.get("/api/materials", params={"search": "%' OR 1=1 --"}).json() == []
        assert client.get("/api/materials", params={"search": "#autumn", "workflow_status": "DONE"}).json() == []
    with case.client("PROCESSOR") as client:
        own = client.get("/api/materials", params={"search": "#autumn"})
        assert own.status_code == 200
        assert [row["id"] for row in own.json()] == [first["id"]]


def test_checked_is_human_and_correction_reopens_without_publishing(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        path = f"/api/materials/{case.materials[0].id}"
        material = client.get(path).json()
        assert material["checked_status"] == "no"
        assert write(client, material, {"checked_status": "OK"}).status_code == 409
        with case.database.session() as session:
            row = session.get(PBRMaterial, case.materials[0].id)
            row.folder_path = "library/" + row.technical_identity
            session.commit()
        material = client.get(path).json()
        case.worker.queue(_preflight(material["technical_identity"]))
        done = write(client, material, {"workflow_status": "DONE"})
        assert done.status_code == 200, done.text
        assert done.json()["checked_status"] == "no"
        checked = write(client, done.json(), {"checked_status": "OK"}).json()
        assert checked["checked_status"] == "OK"
        note = write(client, checked, {"note": "Line 1\nLine 2"}).json()
        assert note["checked_status"] == "OK" and note["note"] == "Line 1\nLine 2"
        correction = write(client, note, {"checked_status": "Correction"}).json()
        assert correction["workflow_status"] == "IN_PROGRESS" and correction["checked_status"] == "Correction"
        case.worker.queue(_preflight(material["technical_identity"]))
        again = write(client, correction, {"workflow_status": "DONE"}).json()
        assert again["checked_status"] == "no" and again["is_published"] is False


def test_optimistic_conflict_and_idempotency_preserve_history(access_case):
    case = access_case; key = uuid4()
    with case.client("ADMIN") as client:
        material = client.get(f"/api/materials/{case.materials[0].id}").json()
        first = write(client, material, {"note": "first"}, key)
        assert first.status_code == 200
        assert write(client, material, {"note": "lost overwrite"}).status_code == 409
        assert write(client, first.json(), {"note": "later"}).status_code == 200
        repeat = write(client, material, {"note": "first"}, key)
        assert repeat.json() == first.json() and repeat.headers["Idempotency-Replayed"] == "true"
        assert write(client, material, {"note": "different"}, key).status_code == 409
        assert client.get(f"/api/resource-commands/{key}").json()["response"] == first.json()
        history = client.get(f"/api/materials/{material['id']}/history")
        assert history.status_code == 200
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ResourceChangeEvent)) == 2
        assert session.scalar(select(func.count()).select_from(ResourceCommand)) == 2


def test_assignment_can_clear_project_and_published_is_only_manual_evidence(access_case):
    with access_case.client("ADMIN") as client:
        material = client.get(f"/api/materials/{access_case.materials[0].id}").json()
        published = write(client, material, {"is_published": True}).json()
        assert published["is_published"] is True and published["publication_status"] == "NOT_PUBLISHED"
        historical = write(client, published, {"project_id": None}).json()
        assert historical["project_id"] is None
        restored = write(client, historical, {"project_id": material["project_id"]}).json()
        assert restored["project_id"] == material["project_id"]
        assert not access_case.worker.calls


def test_processor_can_edit_own_note_and_status_but_not_human_signoff_or_others(access_case):
    case = access_case
    with case.client("PROCESSOR") as client:
        material = client.get(f"/api/materials/{case.materials[0].id}").json()
        first = write(client, material, {"note": "Working"})
        assert first.status_code == 200
        assert write(client, material, {"note": "Working"}, uuid4()).status_code == 409
        for change in ({"checked_status": "Correction"}, {"is_published": True}, {"project_id": None}):
            assert write(client, first.json(), change).status_code == 403
        assert write(client, {**first.json(), "id": str(case.materials[1].id)}, {"note": "Other"}).status_code == 404
    with case.client("LEADERSHIP") as client:
        assert write(client, first.json(), {"note": "No access"}).status_code == 403


def test_table_schema_is_narrow_and_no_folderless_done(access_case):
    with access_case.client("ADMIN") as client:
        material = client.get(f"/api/materials/{access_case.materials[0].id}").json()
        assert write(client, material, {"workflow_status": "DONE"}).status_code == 409
        for change in ({"checked_status": "VALID"}, {"note": "x" * 10001}, {"note": "x", "is_published": True}, {"publication_status": "PUBLISHED_CURRENT"}, {}):
            assert write(client, material, change).status_code == 422
        assert client.patch(f"/api/materials/{material['id']}/table", json={"expected_updated_at": material["updated_at"], "note": "x"}).status_code == 422


def test_unlinked_identity_uses_reservations_and_linked_identity_requires_a_plan(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        material = client.get(f"/api/materials/{case.materials[0].id}").json()
        changed = write(client, material, {"main_category_code": "K03"})
        assert changed.status_code == 200, changed.text
        assert changed.json()["technical_identity"].endswith("_K03")
        with case.database.session() as session:
            from app.db.models import PublishedBrand
            source = session.get(PublishedBrand, case.materials[0].published_brand_id)
            target = PublishedBrand(company_id=source.company_id, name="Second brand", folder_prefix="OTHER", brand_identifier="second")
            session.add(target); session.commit(); brand_id = target.id
        key = uuid4()
        transfer = write(client, changed.json(), {"published_brand_id": str(brand_id)}, key)
        assert transfer.status_code == 200, transfer.text
        assert transfer.json()["technical_identity"].startswith("OTHER_0001_")
        assert write(client, changed.json(), {"published_brand_id": str(brand_id)}, key).json() == transfer.json()
        with case.database.session() as session:
            assert session.get(PublishedBrand, brand_id).next_sequence_number == 2
            row = session.get(PBRMaterial, case.materials[0].id); row.folder_path = "library/" + row.technical_identity; session.commit()
        current = client.get(f"/api/materials/{case.materials[0].id}").json()
        assert write(client, current, {"main_category_code": "G03"}).status_code == 409
        assert not case.worker.calls


def test_unavailable_preflight_is_a_known_no_write_outcome(access_case):
    from app.worker_client import WorkerUnavailableError
    case = access_case
    with case.database.session() as session:
        material = session.get(PBRMaterial, case.materials[0].id)
        material.folder_path = "library/" + material.technical_identity; session.commit()
    case.worker.queue(WorkerUnavailableError("PRIVATE worker detail"))
    with case.client("ADMIN") as client:
        material = client.get(f"/api/materials/{case.materials[0].id}").json()
        failed = write(client, material, {"workflow_status": "DONE"})
        assert failed.status_code == 503 and failed.json()["detail"]["code"] == "TABLE_PREFLIGHT_UNAVAILABLE"
        assert "PRIVATE" not in failed.text
        assert client.get(f"/api/materials/{case.materials[0].id}").json() == material
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ResourceCommand)) == 0
        assert session.scalar(select(func.count()).select_from(PBRMaterialMetadataSnapshot)) == 0
