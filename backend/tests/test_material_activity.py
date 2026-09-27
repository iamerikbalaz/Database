"""Unified read-only material history retains real actor and cursor boundaries."""
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.db.models import (MaterialAuditEvent, MaterialContentRevision, PBRMaterialMetadataSnapshot,
    ResourceChangeEvent)
from app.material_review import canonical_hash
from test_application_access import access_case
from test_material_archives import attach, apply, prepare
from test_material_metadata import _snapshot_values


def seed(case):
    identifier = case.materials[0].id
    stamp = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
    with case.client("ADMIN") as client:
        assert client.patch(f"/api/materials/{identifier}", json={"material_name": "Edited by admin"}).status_code == 200
    with case.database.session() as session:
        snapshot = {"description": "Written by assigned processor", "credits": 4, "tags": ["stone"]}
        session.add(MaterialContentRevision(material_id=identifier, revision=1, actor_id=case.users["PROCESSOR"].id,
            snapshot=snapshot, snapshot_hash=canonical_hash(snapshot), reason="Content saved", created_at=stamp))
        metadata = PBRMaterialMetadataSnapshot(material_id=identifier, sequence_number=1,
            **_snapshot_values(color="#A1B2C3", width=Decimal(10)), created_at=stamp)
        session.add(metadata)
        session.add(MaterialAuditEvent(material_id=identifier, actor_id=case.users["ADMIN"].id,
            event_type="MATERIAL_REOPENED", generation=1, result={"audit": {"reason": "Reopened"}, "private": "DO-NOT-DISCLOSE"}, created_at=stamp))
        session.commit()
        return identifier, metadata.id


def test_merged_history_authors_pagination_and_metadata_redaction(access_case):
    case = access_case; identifier, metadata_id = seed(case)
    url = f"/api/materials/{identifier}/activity"
    with case.client("ADMIN") as client:
        full = client.get(url).json()
        items = full["items"]
        assert {item["source"] for item in items} == {"record", "content", "metadata", "workflow"}
        authors = {item["source"]: item["author"] for item in items}
        assert authors["record"] == {"id": str(case.users["ADMIN"].id), "display_name": case.users["ADMIN"].display_name}
        assert authors["content"]["id"] == str(case.users["PROCESSOR"].id)
        assert authors["metadata"] is None
        assert "source_content" not in str(full) and "DO-NOT-DISCLOSE" not in str(full)
        pages = []; cursor = None
        while True:
            page = client.get(url, params={"limit": 1, **({"after": cursor} if cursor else {})}).json()
            pages.extend(page["items"]); cursor = page["next_cursor"]
            if not cursor: break
        assert pages == items and len({item["id"] for item in pages}) == len(items)
        assert client.get(url, params={"after": "record:" + str(uuid4())}).status_code == 409
        assert client.get(url, params={"after": "bad"}).status_code == 409
        assert client.get(url, params={"limit": 101}).status_code == 422
    with case.database.session() as session:
        assert len(list(session.scalars(select(PBRMaterialMetadataSnapshot)))) == 1
        assert len(list(session.scalars(select(ResourceChangeEvent)))) == 1


@pytest.mark.parametrize("role,status", [(None, 401), ("PROCESSOR", 200), ("OTHER", 404), ("LEADERSHIP", 200), ("PRODUCTION_LEAD", 200)])
def test_activity_uses_current_material_visibility(access_case, role, status):
    case = access_case; identifier, _ = seed(case)
    with case.client(role) as client:
        assert client.get(f"/api/materials/{identifier}/activity").status_code == status


def test_archived_history_is_admin_only_and_foreign_cursors_are_rejected(access_case):
    case = attach(access_case); identifier, _ = seed(case)
    with case.client("ADMIN") as client:
        cursor = client.get(f"/api/materials/{identifier}/activity").json()["items"][0]["id"]
        assert client.get(f"/api/materials/{case.materials[1].id}/activity", params={"after": cursor}).status_code == 409
        assert apply(client, identifier, prepare(client, identifier)).status_code == 200
        page = client.get(f"/api/materials/{identifier}/activity")
        assert page.status_code == 200
        assert any(item["source"] == "archive" and item["changes"][0]["after"] is True for item in page.json()["items"])
    with case.client("PROCESSOR") as client:
        assert client.get(f"/api/materials/{identifier}/activity").status_code == 404


def test_snapshot_author_requires_explicit_link_not_processor_or_timestamp(access_case):
    case = access_case; identifier, metadata_id = seed(case)
    with case.database.session() as session:
        session.add(MaterialAuditEvent(material_id=identifier, actor_id=case.users["ADMIN"].id,
            event_type="SOURCE_METADATA_COMPLETED", generation=1,
            result={"audit": {"metadata_snapshot_id": str(metadata_id)}}))
        session.commit()
    with case.client("ADMIN") as client:
        items = client.get(f"/api/materials/{identifier}/activity").json()["items"]
        metadata = next(item for item in items if item["source"] == "metadata")
        assert metadata["author"]["id"] == str(case.users["ADMIN"].id)
