"""All-or-nothing import, permanent number ownership and immutable replay."""
import base64
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import MaterialImportBatch, MaterialImportRow, MaterialNumberReservation, PBRMaterial, PBRMaterialMetadata, PublishedBrand
from test_application_access import access_case
from test_import_preview import plan_payload

ROOT = "/api/material-imports"


def confirmation(client, body):
    result = client.post(ROOT + "/preview", json=body)
    assert result.status_code == 200 and result.json()["can_confirm"]
    return {**body, "expected_preview_hash": result.json()["preview_hash"], "idempotency_key": str(uuid4()),
            "acknowledge_unverified": True, "reason": "Verified synthetic historical import mapping"}


def test_confirm_preserves_exact_identity_advances_counter_and_replays_original_snapshot(access_case):
    case = access_case; brand_id = case.materials[0].published_brand_id
    with case.client("ADMIN") as client:
        body = confirmation(client, plan_payload(case, ("SAFE_0007_G03", "SAFE_0004_G03")))
        response = client.post(ROOT + "/confirm", json=body)
        assert response.status_code == 200
        result = response.json()
        assert result["row_count"] == 2 and len(result["rows"]) == 2
        assert result["idempotency_key"] == body["idempotency_key"]
        assert [row["technical_identity"] for row in result["rows"]] == ["SAFE_0007_G03", "SAFE_0004_G03"]
        assert len({row["material_id"] for row in result["rows"]}) == 2
        assert client.post(ROOT + "/confirm", json=body).json() == result
        with case.database.session() as session:
            assert session.get(PublishedBrand, brand_id).next_sequence_number == 8
            for row in result["rows"]:
                material = session.get(PBRMaterial, UUID(row["material_id"]))
                assert material.folder_path is None and material.workflow_status == "IN_PROGRESS"
                assert material.validation_status == "NOT_CHECKED" and not material.is_published
                assert material.publication_status == "NOT_PUBLISHED" and material.metadata_state.status == "NOT_SCANNED"
                reservation = session.get(MaterialNumberReservation, (brand_id, material.sequence_number))
                assert reservation.material_id == material.id and reservation.actor_id == case.users["ADMIN"].id
                material.material_name = "Subsequently edited material"
            session.commit()
        assert client.post(ROOT + "/confirm", json=body).json() == result
        assert client.get(ROOT + "/" + result["id"]).json() == result
        listed = client.get(ROOT).json()
        assert len(listed["items"]) == 1 and listed["items"][0]["id"] == result["id"] and listed["next_after"] is None
        assert "snapshot" not in listed["items"][0]
        normal = client.post("/api/materials", json={"project_id": str(case.materials[0].project_id),
            "published_brand_id": str(brand_id), "assigned_processor_id": str(case.materials[0].assigned_processor_id),
            "material_name": "Subsequent ordinary creation", "main_category_code": "G03"})
        assert normal.status_code == 201 and normal.json()["technical_identity"] == "SAFE_0008_G03"


@pytest.mark.parametrize("change", ["reason", "expected_preview_hash", "source", "idempotency_key"])
def test_reused_import_request_never_mutates_or_duplicates_original_batch(access_case, change):
    case = access_case
    with case.client("ADMIN") as client:
        body = confirmation(client, plan_payload(case))
        first = client.post(ROOT + "/confirm", json=body)
        assert first.status_code == 200
        changed = {**body}
        if change == "reason": changed[change] = "Different reason"
        elif change == "expected_preview_hash": changed[change] = "a" * 64
        elif change == "idempotency_key": changed[change] = str(uuid4())
        else: changed["source"] = plan_payload(case, ("SAFE_0008_G03",))["source"]
        result = client.post(ROOT + "/confirm", json=changed)
        assert result.status_code == 409
        assert result.json()["detail"]["code"] == ("IMPORT_BLOCKED" if change == "idempotency_key" else "IMPORT_IDEMPOTENCY_CONFLICT")
        assert client.post(ROOT + "/confirm", json=body).json() == first.json()
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialImportBatch)) == 1
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 3


@pytest.mark.parametrize("change", ["brand_counter", "source", "expected_preview_hash", "brand_name"])
def test_stale_or_altered_confirmation_is_rejected_before_any_write(access_case, change):
    case = access_case
    with case.client("ADMIN") as client:
        body = confirmation(client, plan_payload(case))
        if change == "source": body["source"] = plan_payload(case, ("SAFE_0009_G03",))["source"]
        elif change == "expected_preview_hash": body[change] = "a" * 64
        else:
            with case.database.session() as session:
                brand = session.get(PublishedBrand, case.materials[0].published_brand_id)
                if change == "brand_counter": brand.next_sequence_number += 1
                else: brand.name = "Corrected synthetic name"
                session.commit()
        response = client.post(ROOT + "/confirm", json=body)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "IMPORT_PREVIEW_CHANGED"
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialImportBatch)) == 0
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 2


def test_late_reservation_conflict_blocks_entire_batch(access_case):
    case = access_case; material = case.materials[0]
    with case.client("ADMIN") as client:
        body = confirmation(client, plan_payload(case, ("SAFE_0007_G03", "SAFE_0008_G03")))
        with case.database.session() as session:
            session.add(MaterialNumberReservation(brand_id=material.published_brand_id, sequence_number=8, material_id=material.id))
            session.commit()
        result = client.post(ROOT + "/confirm", json=body)
        assert result.status_code == 409
        assert result.json()["detail"]["findings"] == [{"row": 3, "field": "identity", "code": "IMPORT_NUMBER_RESERVED"}]
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(PBRMaterial)) == 2
        assert session.get(PublishedBrand, material.published_brand_id).next_sequence_number == 3


def test_final_audit_insert_failure_rolls_back_materials_metadata_batch_reservations_and_counter(access_case):
    case = access_case
    def fail(*_):
        raise IntegrityError("Synthetic audit failure", None, Exception("Synthetic failure"))
    with case.client("ADMIN") as client:
        body = confirmation(client, plan_payload(case, ("SAFE_0007_G03", "SAFE_0008_G03")))
        event.listen(MaterialImportRow, "before_insert", fail)
        try:
            result = client.post(ROOT + "/confirm", json=body)
        finally:
            event.remove(MaterialImportRow, "before_insert", fail)
        assert result.status_code == 409 and result.json()["detail"]["code"] == "IMPORT_DATABASE_CONFLICT"
        with case.database.session() as session:
            for model in (MaterialImportBatch, MaterialImportRow, MaterialNumberReservation):
                assert session.scalar(select(func.count()).select_from(model)) == 0
            for model in (PBRMaterial, PBRMaterialMetadata):
                assert session.scalar(select(func.count()).select_from(model)) == 2
            assert session.get(PublishedBrand, case.materials[0].published_brand_id).next_sequence_number == 3
        assert client.post(ROOT + "/confirm", json=body).status_code == 200


def test_highest_historical_number_exhausts_counter_and_unused_lower_number_does_not_rewind(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        body = confirmation(client, plan_payload(case, ("SAFE_9999_G03",)))
        assert client.post(ROOT + "/confirm", json=body).status_code == 200
        lower = confirmation(client, plan_payload(case, ("SAFE_0004_G03",)))
        assert client.post(ROOT + "/confirm", json=lower).status_code == 200
    with case.database.session() as session:
        assert session.get(PublishedBrand, case.materials[0].published_brand_id).next_sequence_number == 10000


@pytest.mark.parametrize("kind,operation", [(MaterialImportBatch, "update"), (MaterialImportBatch, "delete"),
    (MaterialImportRow, "update"), (MaterialImportRow, "delete")])
def test_import_audit_is_append_only_through_orm(access_case, kind, operation):
    with access_case.client("ADMIN") as client:
        assert client.post(ROOT + "/confirm", json=confirmation(client, plan_payload(access_case))).status_code == 200
    with access_case.database.session() as session:
        record = session.scalar(select(kind))
        if operation == "delete": session.delete(record)
        else: record.snapshot = {"changed": True}
        with pytest.raises(Exception, match="append-only"):
            session.commit()


def test_batch_history_has_stable_bounded_cursor_pagination(access_case):
    with access_case.client("ADMIN") as client:
        identifiers = set()
        for number in (7, 8, 9):
            body = confirmation(client, plan_payload(access_case, (f"SAFE_{number:04d}_G03",)))
            identifiers.add(client.post(ROOT + "/confirm", json=body).json()["id"])
        seen = []; after = None
        while True:
            page = client.get(ROOT, params={"limit": 1, **({"after": after} if after else {})}).json()
            assert len(page["items"]) == 1
            seen.append(page["items"][0]["id"])
            after = page["next_after"]
            if not after: break
            assert len(seen) < 4
        assert set(seen) == identifiers and len(seen) == 3
        assert client.get(ROOT, params={"limit": 51}).status_code == 422
        assert client.get(ROOT, params={"after": str(uuid4())}).status_code == 404
        assert client.get(ROOT + "/" + str(uuid4())).status_code == 404


@pytest.mark.parametrize("role,status", [(None, 401), ("PROCESSOR", 403), ("PRODUCTION_LEAD", 403), ("LEADERSHIP", 403)])
def test_confirm_and_history_remain_admin_only(access_case, role, status):
    with access_case.client("ADMIN") as client:
        body = confirmation(client, plan_payload(access_case))
        batch = client.post(ROOT + "/confirm", json=body).json()
    with access_case.client(role) as client:
        assert client.post(ROOT + "/confirm", json=body).status_code == status
        assert client.get(ROOT).status_code == status
        assert client.get(ROOT + "/" + batch["id"]).status_code == status


def test_csv_multiline_source_coordinates_are_preserved_in_audit(access_case):
    with access_case.client("ADMIN") as client:
        body = plan_payload(access_case, ("SAFE_0007_G03", "SAFE_0008_G03"))
        raw = base64.b64decode(body["source"]["data"]).replace(b"Synthetic import", b'"Synthetic\nimport"', 1)
        body["source"]["data"] = base64.b64encode(raw).decode()
        result = client.post(ROOT + "/confirm", json=confirmation(client, body))
        assert result.status_code == 200
        assert [row["source_row"] for row in result.json()["rows"]] == [2, 4]
