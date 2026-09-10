from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    ImmutableAuditSnapshotError,
    InternalUser,
    PBRMaterialMetadata,
    PBRMaterialMetadataSnapshot,
)
from app.db.session import Database
from app.main import create_app


PROCESSOR_ID = UUID("00000000-0000-0000-0000-000000000099")


@pytest.fixture
def metadata_client() -> tuple[TestClient, Database]:
    database = Database("sqlite+pysqlite:///:memory:")
    with database.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add(
            InternalUser(
                id=PROCESSOR_ID,
                display_name="Metadata processor",
                email="metadata.processor@example.com",
                role="PROCESSOR",
            )
        )
        session.commit()
    application = create_app(Settings(), database)
    with TestClient(application) as client:
        yield client, database


def _create_material(client: TestClient, suffix: str = "") -> dict[str, object]:
    company = client.post("/api/companies", json={"name": f"Metadata company{suffix}"})
    assert company.status_code == 201
    company_id = company.json()["id"]
    project = client.post(
        "/api/projects",
        json={
            "company_id": company_id,
            "project_number": f"META-{suffix or '001'}",
            "name": "Metadata project",
        },
    )
    assert project.status_code == 201
    brand = client.post(
        "/api/brands",
        json={
            "company_id": company_id,
            "name": f"Metadata brand{suffix}",
            "folder_prefix": f"META{suffix or 'BASE'}",
            "brand_identifier": f"metadata-brand-{suffix or 'base'}",
        },
    )
    assert brand.status_code == 201
    response = client.post(
        "/api/materials",
        json={
            "project_id": project.json()["id"],
            "published_brand_id": brand.json()["id"],
            "material_name": "Audited stone",
            "main_category_code": "G03",
            "assigned_processor_id": str(PROCESSOR_ID),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _snapshot_values(*, color: str, width: Decimal) -> dict[str, object]:
    return {
        "status": "VALID",
        "source_filename": "metadata.txt",
        "source_sha256": "a" * 64,
        "source_content": f"color={color}\nwidth={width}\nheight=75.25",
        "hex_color": color,
        "width_cm": width,
        "height_cm": Decimal("75.2500"),
        "master_resolution": "16K",
        "warnings": [],
        "loaded_at": datetime(2026, 9, 9, 10, 30, tzinfo=UTC),
    }


def test_material_metadata_migration_follows_material_core_and_is_the_only_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert scripts.get_heads() == ["20260909_0005"]
    revision = scripts.get_revision("20260909_0005")
    assert revision is not None
    assert revision.down_revision == "20260908_0004"
    assert [item.revision for item in scripts.walk_revisions(base="base", head="heads")] == [
        "20260909_0005",
        "20260908_0004",
        "20260907_0003",
        "20260904_0002",
        "20260904_0001",
    ]


def test_new_material_has_persisted_empty_metadata_state(
    metadata_client: tuple[TestClient, Database],
) -> None:
    client, database = metadata_client
    material = _create_material(client)

    response = client.get(f"/api/materials/{material['id']}/metadata")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "material_id": material["id"],
        "current_snapshot_id": None,
        "status": "NOT_SCANNED",
        "source_filename": None,
        "source_sha256": None,
        "hex_color": None,
        "width_cm": None,
        "height_cm": None,
        "master_resolution": None,
        "warnings": [],
        "loaded_at": None,
        "updated_at": body["updated_at"],
    }
    assert body["updated_at"]
    assert client.get(f"/api/materials/{material['id']}/metadata/snapshots").json() == []
    with database.session() as session:
        assert session.get(PBRMaterialMetadata, UUID(str(material["id"]))) is not None


def test_current_metadata_and_audit_snapshots_have_explicit_read_contract_and_order(
    metadata_client: tuple[TestClient, Database],
) -> None:
    client, database = metadata_client
    material = _create_material(client)
    material_id = UUID(str(material["id"]))
    first_values = _snapshot_values(color="#A1B2C3", width=Decimal("120.5000"))
    second_values = _snapshot_values(color="#D4E5F6", width=Decimal("121.7500"))
    second_values["status"] = "WARNING"
    second_values["warnings"] = [
        {
            "code": "DIMENSION_SOURCE_MISMATCH",
            "path": "metadata.txt",
            "message": "The decoded dimensions differ from the reference.",
        }
    ]

    with database.session() as session:
        second = PBRMaterialMetadataSnapshot(
            material_id=material_id,
            sequence_number=2,
            **second_values,
        )
        first = PBRMaterialMetadataSnapshot(
            material_id=material_id,
            sequence_number=1,
            **first_values,
        )
        session.add_all([second, first])
        session.flush()
        current = session.get(PBRMaterialMetadata, material_id)
        assert current is not None
        current.current_snapshot_id = second.id
        for field_name, value in second_values.items():
            setattr(current, field_name, value)
        session.commit()
        second_id = str(second.id)

    current_response = client.get(f"/api/materials/{material['id']}/metadata")
    history_response = client.get(f"/api/materials/{material['id']}/metadata/snapshots")

    assert current_response.status_code == 200
    current_body = current_response.json()
    assert current_body["current_snapshot_id"] == second_id
    assert current_body["status"] == "WARNING"
    assert current_body["source_filename"] == "metadata.txt"
    assert current_body["source_sha256"] == "a" * 64
    assert "source_content" not in current_body
    assert current_body["hex_color"] == "#D4E5F6"
    assert current_body["width_cm"] == "121.7500"
    assert current_body["height_cm"] == "75.2500"
    assert current_body["master_resolution"] == "16K"
    assert current_body["warnings"][0]["code"] == "DIMENSION_SOURCE_MISMATCH"
    assert current_body["loaded_at"].startswith("2026-09-09T10:30:00")
    material_body = client.get(f"/api/materials/{material['id']}").json()
    assert material_body["workflow_status"] == "IN_PROGRESS"
    assert material_body["validation_status"] == "NOT_CHECKED"

    assert history_response.status_code == 200
    history = history_response.json()
    assert [item["sequence_number"] for item in history] == [1, 2]
    assert [item["hex_color"] for item in history] == ["#A1B2C3", "#D4E5F6"]
    assert [item["id"] for item in history][1] == second_id
    assert all(item["source_filename"] == "metadata.txt" for item in history)
    assert all(item["created_at"] for item in history)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("status", "UNKNOWN"),
        ("source_filename", "folder/metadata.txt"),
        ("source_sha256", "not-a-sha256"),
        ("source_sha256", "A" * 64),
        ("hex_color", "#abc123"),
        ("hex_color", "123456"),
        ("width_cm", Decimal("0")),
        ("height_cm", Decimal("-0.0001")),
        ("master_resolution", "0K"),
        ("master_resolution", "16k"),
    ],
)
def test_database_rejects_invalid_current_metadata_values(
    metadata_client: tuple[TestClient, Database],
    field_name: str,
    invalid_value: object,
) -> None:
    client, database = metadata_client
    material = _create_material(client)

    with database.session() as session:
        current = session.get(PBRMaterialMetadata, UUID(str(material["id"])))
        assert current is not None
        setattr(current, field_name, invalid_value)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_database_enforces_positive_unique_snapshot_order(
    metadata_client: tuple[TestClient, Database],
) -> None:
    client, database = metadata_client
    material = _create_material(client)
    material_id = UUID(str(material["id"]))

    with database.session() as session:
        session.add(
            PBRMaterialMetadataSnapshot(
                material_id=material_id,
                sequence_number=0,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with database.session() as session:
        session.add(
            PBRMaterialMetadataSnapshot(
                material_id=material_id,
                sequence_number=1,
            )
        )
        session.commit()
        session.add(
            PBRMaterialMetadataSnapshot(
                material_id=material_id,
                sequence_number=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_orm_rejects_snapshot_update_and_preserves_original(
    metadata_client: tuple[TestClient, Database],
) -> None:
    client, database = metadata_client
    material = _create_material(client)
    material_id = UUID(str(material["id"]))

    with database.session() as session:
        snapshot = PBRMaterialMetadataSnapshot(
            material_id=material_id,
            sequence_number=1,
            hex_color="#A1B2C3",
        )
        session.add(snapshot)
        session.commit()
        snapshot_id = snapshot.id

        snapshot.hex_color = "#D4E5F6"
        with pytest.raises(ImmutableAuditSnapshotError, match="cannot be updated"):
            session.commit()
        session.rollback()

        stored = session.get(PBRMaterialMetadataSnapshot, snapshot_id)
        assert stored is not None
        assert stored.hex_color == "#A1B2C3"
        assert stored.sequence_number == 1


def test_orm_rejects_snapshot_delete_and_keeps_sequence_occupied(
    metadata_client: tuple[TestClient, Database],
) -> None:
    client, database = metadata_client
    material = _create_material(client)
    material_id = UUID(str(material["id"]))

    with database.session() as session:
        snapshot = PBRMaterialMetadataSnapshot(
            material_id=material_id,
            sequence_number=1,
        )
        session.add(snapshot)
        session.commit()
        snapshot_id = snapshot.id

        session.delete(snapshot)
        with pytest.raises(ImmutableAuditSnapshotError, match="cannot be deleted"):
            session.commit()
        session.rollback()

        assert session.get(PBRMaterialMetadataSnapshot, snapshot_id) is not None
        session.add(
            PBRMaterialMetadataSnapshot(
                material_id=material_id,
                sequence_number=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_current_metadata_remains_updatable(
    metadata_client: tuple[TestClient, Database],
) -> None:
    client, database = metadata_client
    material = _create_material(client)
    material_id = UUID(str(material["id"]))

    with database.session() as session:
        current = session.get(PBRMaterialMetadata, material_id)
        assert current is not None
        current.status = "WARNING"
        current.warnings = [{"code": "REVIEW", "message": "Needs review."}]
        session.commit()

        session.refresh(current)
        assert current.status == "WARNING"
        assert current.warnings == [{"code": "REVIEW", "message": "Needs review."}]


@pytest.mark.parametrize(
    "invalid_warnings",
    [
        pytest.param({"code": "OBJECT", "message": "Not a list."}, id="object"),
        pytest.param(["not-an-object"], id="invalid-item"),
        pytest.param([{"code": "", "message": "Empty code."}], id="empty-code"),
        pytest.param([{"code": "MISSING_MESSAGE"}], id="missing-message"),
    ],
)
def test_orm_rejects_invalid_warnings_in_sqlite(
    metadata_client: tuple[TestClient, Database],
    invalid_warnings: object,
) -> None:
    client, database = metadata_client
    material = _create_material(client)

    with database.session() as session:
        current = session.get(PBRMaterialMetadata, UUID(str(material["id"])))
        assert current is not None
        with pytest.raises(ValueError, match="Material metadata warning"):
            current.warnings = invalid_warnings  # type: ignore[assignment]

    response = client.get(f"/api/materials/{material['id']}/metadata")
    assert response.status_code == 200
    assert response.json()["warnings"] == []


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("metadata", {"status": "VALID"}),
        ("metadata_snapshots", []),
        ("metadata_status", "VALID"),
        ("status", "VALID"),
        ("source_filename", "metadata.txt"),
        ("source_sha256", "a" * 64),
        ("source_content", "#112233; 10x20 cm"),
        ("hex_color", "#112233"),
        ("width_cm", "10.5"),
        ("height_cm", "20.5"),
        ("master_resolution", "16K"),
        ("warnings", []),
        ("loaded_at", "2026-09-09T10:30:00Z"),
        ("current_snapshot_id", "00000000-0000-0000-0000-000000000001"),
    ],
)
def test_general_material_create_and_patch_reject_metadata_fields(
    metadata_client: tuple[TestClient, Database],
    method: str,
    field_name: str,
    value: object,
) -> None:
    client, _ = metadata_client
    material = _create_material(client)
    if method == "post":
        company = client.post("/api/companies", json={"name": "Rejected metadata"}).json()
        project = client.post(
            "/api/projects",
            json={
                "company_id": company["id"],
                "project_number": f"REJECT-{field_name}",
                "name": "Rejected metadata",
            },
        ).json()
        brand = client.post(
            "/api/brands",
            json={
                "company_id": company["id"],
                "name": f"Rejected {field_name}",
                "folder_prefix": f"REJECT{field_name.upper()}",
                "brand_identifier": f"reject-{field_name}",
            },
        ).json()
        response = client.post(
            "/api/materials",
            json={
                "project_id": project["id"],
                "published_brand_id": brand["id"],
                "material_name": "Must reject metadata",
                "main_category_code": "G03",
                "assigned_processor_id": str(PROCESSOR_ID),
                field_name: value,
            },
        )
    else:
        response = client.patch(
            f"/api/materials/{material['id']}",
            json={field_name: value},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_metadata_routes_are_read_only_and_missing_material_is_404(
    metadata_client: tuple[TestClient, Database],
) -> None:
    client, _ = metadata_client
    material = _create_material(client)
    metadata_path = f"/api/materials/{material['id']}/metadata"
    snapshots_path = f"{metadata_path}/snapshots"

    assert client.post(metadata_path, json={}).status_code == 405
    assert client.patch(metadata_path, json={}).status_code == 405
    assert client.delete(metadata_path).status_code == 405
    assert client.post(snapshots_path, json={}).status_code == 405
    assert client.patch(snapshots_path, json={}).status_code == 405
    assert client.delete(snapshots_path).status_code == 405

    missing_id = "00000000-0000-0000-0000-000000000123"
    assert client.get(f"/api/materials/{missing_id}/metadata").status_code == 404
    assert client.get(f"/api/materials/{missing_id}/metadata/snapshots").status_code == 404
