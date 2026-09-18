import json
from decimal import Decimal
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import MaterialAuditEvent, PBRMaterial, PBRMaterialMetadata, PBRMaterialMetadataSnapshot
from app.technical_client import TechnicalReport
from test_application_access import access_case
from test_content_approvals import approval_payload as content_approval
from test_catalog_content import content_payload
from test_inventory_client import rehash
from test_material_approvals import approval_case, approval_payload, run
from test_technical_client import technical_payload

PATH = "/api/publication-batches/preview"


def prepare_candidate(case, worker, path, *, empty=False):
    material = next(item for item in case.materials if path.endswith(str(item.id)))
    # Synthetic immutable source proof, never a production path/file. Actual
    # parser/inventory bytes and decoding are tested separately in retained E2E.
    with case.database.session() as session:
        session.get(PBRMaterial, material.id).workflow_status = "DONE"
        snapshot = PBRMaterialMetadataSnapshot(material_id=material.id, sequence_number=1,
            status="VALID", source_filename="metadata.txt", source_sha256="d" * 64,
            hex_color="#A1B2C3", width_cm=Decimal("12.5"), height_cm=Decimal("34"), master_resolution="4K")
        session.add(snapshot); session.flush()
        current = session.get(PBRMaterialMetadata, material.id)
        current.current_snapshot_id = snapshot.id
        for field in ("status", "source_filename", "source_sha256", "hex_color", "width_cm", "height_cm", "master_resolution"):
            setattr(current, field, getattr(snapshot, field))
        session.commit()
    def validate(folder):
        data = technical_payload(folder.rsplit("/", 1)[-1])
        data["inventory"]["entries"].append({"path": "metadata.txt", "kind": "file", "size": 1, "sha256": "d" * 64})
        data["inventory"]["total_bytes"] += 1
        rehash(data["inventory"])
        return TechnicalReport.model_validate_json(json.dumps(data))
    worker.validate = validate
    with case.client("ADMIN") as admin:
        category = admin.post("/api/online-categories", json={"idempotency_key": str(uuid4()), "value": "Publication " + uuid4().hex})
        assert category.status_code == 201
        assert admin.post(path + "/content", json=content_payload(category_ids=[category.json()["id"]], credits=10,
            description=None if empty else "Reviewed content", tags=[] if empty else ["stone"])).status_code == 200
        view = run(admin, path).json()
        technical = admin.post(path + "/approvals", json=approval_payload(view))
        assert technical.status_code == 200
        assert admin.post(path + "/approvals", json=approval_payload(technical.json(), "PUBLICATION")).status_code == 200
        content = admin.get(path + "/content-review").json()
        assert admin.post(path + "/content/approve", json=content_approval(content,
            **({"warnings_acknowledged": True, "note": "Reviewed empty content"} if empty else {}))).status_code == 200
    return material


def preview(client, *ids):
    result = client.post(PATH, json={"material_ids": [str(item) for item in ids]})
    assert result.status_code == 200
    return result.json()


def test_complete_approved_candidate_has_stable_preview_without_writes_or_raw_source(approval_case):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path)
    with case.database.session() as session:
        before = session.scalar(select(func.count()).select_from(MaterialAuditEvent))
    with case.client("LEADERSHIP") as leader:
        first = preview(leader, material.id)
        assert first == preview(leader, material.id) and first["can_prepare"] is True
        item = first["items"][0]
        assert item["errors"] == [] and item["warnings"] == []
        assert item["row"]["width_cm"] == "12.5" and item["row"]["height_cm"] == "34"
        assert len(item["snapshot_hash"]) == len(first["preview_hash"]) == 64
        assert not any(value in json.dumps(first) for value in ("source_content", "folder_path", "library/", "metadata.txt"))
        assert leader.get(path).json()["is_published"] is False
    with case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MaterialAuditEvent)) == before


@pytest.mark.parametrize("role,expected", [(None, 401), ("PROCESSOR", 403), ("OTHER", 403), ("PRODUCTION_LEAD", 403),
    ("ADMIN", 200), ("LEADERSHIP", 200)])
def test_preview_requires_actual_publication_role_and_csrf(access_case, role, expected):
    with access_case.client(role) as client:
        response = client.post(PATH, json={"material_ids": [str(access_case.materials[0].id)]})
        assert response.status_code == expected
        if expected == 200:
            assert response.json()["can_prepare"] is False
            client.headers.pop("X-CSRF-Token")
            assert client.post(PATH, json={"material_ids": [str(access_case.materials[0].id)]}).status_code == 403


def test_missing_production_review_metadata_and_decisions_report_blockers(access_case):
    with access_case.client("ADMIN") as client:
        result = preview(client, access_case.materials[0].id)
    assert result["can_prepare"] is False
    errors = result["items"][0]["errors"]
    assert {"PRODUCTION_DONE_REQUIRED", "CURRENT_TECHNICAL_REVIEW_REQUIRED", "METADATA_SNAPSHOT_REQUIRED",
        "TECHNICAL_APPROVAL_REQUIRED", "PUBLICATION_APPROVAL_REQUIRED", "CONTENT_APPROVAL_REQUIRED"} <= set(errors)


@pytest.mark.parametrize("change,expected", [("metadata-current", "METADATA_CURRENT_SNAPSHOT_MISMATCH"),
    ("source", "METADATA_SOURCE_REVISION_MISMATCH"), ("master", "METADATA_MASTER_REVISION_MISMATCH"),
    ("missing-color", "EXPORT_COLOR_INVALID"), ("content", "CONTENT_APPROVAL_REQUIRED"),
    ("reopen", "PRODUCTION_DONE_REQUIRED")])
def test_stale_or_incomplete_inputs_change_preview_and_block_preparation(approval_case, change, expected):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path)
    with case.client("ADMIN") as admin:
        before = preview(admin, material.id)
        if change == "content":
            draft = admin.get(path + "/content").json()
            assert admin.post(path + "/content", json=content_payload(expected_revision=draft["revision"],
                description="Changed", credits=10, category_ids=[item["id"] for item in draft["categories"]])).status_code == 200
        elif change == "reopen":
            generation = admin.get(path + "/review").json()["generation"]
            assert admin.post(path + "/reopen", json={"idempotency_key": str(uuid4()), "expected_generation": generation,
                "reason": "Review changed source"}).status_code == 200
        else:
            with case.database.session() as session:
                current = session.get(PBRMaterialMetadata, material.id)
                if change == "metadata-current": current.width_cm = Decimal("13")
                else:
                    # New immutable snapshot deliberately differs from approved
                    # source proof; never mutate an existing provenance row.
                    values = {field: getattr(current, field) for field in ("status", "source_filename", "source_sha256",
                        "hex_color", "width_cm", "height_cm", "master_resolution")}
                    values[{"source": "source_sha256", "master": "master_resolution", "missing-color": "hex_color"}[change]] = {
                        "source": "e" * 64, "master": "8K", "missing-color": None}[change]
                    if change == "missing-color": values["status"] = "WARNING"
                    snapshot = PBRMaterialMetadataSnapshot(material_id=material.id, sequence_number=2, **values)
                    session.add(snapshot); session.flush(); current.current_snapshot_id = snapshot.id
                    for field, value in values.items(): setattr(current, field, value)
                session.commit()
        after = preview(admin, material.id)
        assert after["can_prepare"] is False and expected in after["items"][0]["errors"]
        assert before["preview_hash"] != after["preview_hash"]


def test_empty_approved_content_warns_without_bypassing_approvals(approval_case):
    case, worker, path = approval_case
    material = prepare_candidate(case, worker, path, empty=True)
    with case.client("ADMIN") as client:
        result = preview(client, material.id)
    assert result["can_prepare"] is True
    assert {item["code"] for item in result["items"][0]["warnings"]} == {"CONTENT_DESCRIPTION_EMPTY", "CONTENT_TAGS_EMPTY"}


@pytest.mark.parametrize("mode", ["empty", "duplicate", "oversize", "unknown", "extra"])
def test_selection_is_bounded_exact_and_rejects_unexpected_input(access_case, mode):
    identifier = str(access_case.materials[0].id)
    data = {"material_ids": [identifier]}
    if mode == "empty": data["material_ids"] = []
    if mode == "duplicate": data["material_ids"] *= 2
    if mode == "oversize": data["material_ids"] *= 101
    if mode == "unknown": data["material_ids"].append(str(uuid4()))
    if mode == "extra": data["arbitrary-private-input"] = "do not reflect"
    with access_case.client("ADMIN") as client:
        result = client.post(PATH, json=data)
    assert result.status_code == (404 if mode == "unknown" else 422)
    assert "arbitrary-private-input" not in result.text and "do not reflect" not in result.text


def test_selection_order_is_canonical_and_identity_collisions_block(access_case):
    case = access_case
    with case.client("ADMIN") as client:
        ids = [item.id for item in case.materials]
        assert preview(client, *ids) == preview(client, *reversed(ids))
        with case.database.session() as session:
            session.get(PBRMaterial, ids[1]).technical_identity = case.materials[0].technical_identity.lower()
            session.commit()
        result = preview(client, *ids)
        assert all("PUBLICATION_IDENTITY_COLLISION" in item["errors"] for item in result["items"])


def test_preview_rechecks_session_expiry_after_domain_work(access_case, monkeypatch):
    from app import publication_preflight
    from app.auth import access
    case = access_case
    original_prepare = publication_preflight.prepare_publication
    original_now = access.database_now
    def expire_after_preparation(*args):
        prepared = original_prepare(*args)
        monkeypatch.setattr(access, "database_now", lambda session: original_now(session) + timedelta(days=365))
        return prepared
    with case.client("ADMIN") as client:
        monkeypatch.setattr(publication_preflight, "prepare_publication", expire_after_preparation)
        result = client.post(PATH, json={"material_ids": [str(case.materials[0].id)]})
        assert result.status_code == 401 and "preview_hash" not in result.text
