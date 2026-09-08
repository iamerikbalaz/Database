from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.base import Base
from app.db.models import InternalUser, PBRMaterial, PublishedBrand
from app.db.session import Database
from app.main import create_app


DEFAULT_PROCESSOR_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def material_client() -> Iterator[tuple[TestClient, Database]]:
    database = Database("sqlite+pysqlite:///:memory:")
    with database.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        session.add(
            InternalUser(
                id=DEFAULT_PROCESSOR_ID,
                display_name="Default processor",
                email="default.processor@example.com",
                role="PROCESSOR",
            )
        )
        session.commit()
    application = create_app(Settings(), database)
    with TestClient(application) as client:
        yield client, database


def create_company(client: TestClient, name: str = "Acme") -> dict[str, object]:
    response = client.post("/api/companies", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_brand(
    client: TestClient,
    company_id: object,
    *,
    name: str = "Acme Surfaces",
    folder_prefix: str = "ACME",
) -> dict[str, object]:
    response = client.post(
        "/api/brands",
        json={
            "company_id": company_id,
            "name": name,
            "folder_prefix": folder_prefix,
            "brand_identifier": f"{folder_prefix.lower()}-brand",
        },
    )
    assert response.status_code == 201
    return response.json()


def create_project(
    client: TestClient,
    company_id: object,
    *,
    project_number: str = "PRJ-001",
) -> dict[str, object]:
    response = client.post(
        "/api/projects",
        json={
            "company_id": company_id,
            "project_number": project_number,
            "name": "Material library",
        },
    )
    assert response.status_code == 201
    return response.json()


def create_internal_user(
    client: TestClient,
    *,
    display_name: str = "Second processor",
    email: str = "second.processor@example.com",
    is_active: bool = True,
) -> dict[str, object]:
    response = client.post(
        "/api/internal-users",
        json={
            "display_name": display_name,
            "email": email,
            "role": "PROCESSOR",
            "is_active": is_active,
        },
    )
    assert response.status_code == 201
    return response.json()


def material_payload(
    project_id: object,
    brand_id: object,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_id": project_id,
        "published_brand_id": brand_id,
        "material_name": "Reflex Crystal",
        "main_category_code": "G03",
        "assigned_processor_id": str(DEFAULT_PROCESSOR_ID),
    }
    payload.update(overrides)
    return payload


def create_material(
    client: TestClient,
    project_id: object,
    brand_id: object,
    **overrides: object,
) -> dict[str, object]:
    response = client.post(
        "/api/materials",
        json=material_payload(project_id, brand_id, **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


def setup_material_parents(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    company = create_company(client)
    return create_project(client, company["id"]), create_brand(client, company["id"])


def test_create_material_assigns_first_number_and_defaults(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)

    response = client.post("/api/materials", json=material_payload(project["id"], brand["id"]))

    assert response.status_code == 201
    material = response.json()
    assert UUID(material["id"])
    assert material["project_id"] == project["id"]
    assert material["published_brand_id"] == brand["id"]
    assert material["sequence_number"] == 1
    assert material["technical_identity"] == "ACME_0001_G03"
    assert material["folder_path"] is None
    assert material["workflow_status"] == "IN_PROGRESS"
    assert material["validation_status"] == "NOT_CHECKED"
    assert material["is_published"] is False
    assert material["publication_status"] == "NOT_PUBLISHED"
    assert material["created_at"]
    assert material["updated_at"]
    assert client.get(f"/api/materials/{material['id']}").json() == material
    assert client.get("/api/materials").json() == [material]
    assert client.delete(f"/api/materials/{material['id']}").status_code == 405


def test_sequences_are_per_brand(material_client: tuple[TestClient, Database]) -> None:
    client, _ = material_client
    company = create_company(client)
    project = create_project(client, company["id"])
    first_brand = create_brand(client, company["id"])
    second_brand = create_brand(
        client,
        company["id"],
        name="Second brand",
        folder_prefix="SECOND",
    )

    first = create_material(client, project["id"], first_brand["id"])
    second = create_material(client, project["id"], first_brand["id"])
    other_brand = create_material(client, project["id"], second_brand["id"])

    assert (first["sequence_number"], second["sequence_number"]) == (1, 2)
    assert second["technical_identity"] == "ACME_0002_G03"
    assert other_brand["sequence_number"] == 1
    assert other_brand["technical_identity"] == "SECOND_0001_G03"
    identities = {
        first["technical_identity"],
        second["technical_identity"],
        other_brand["technical_identity"],
    }
    assert len(identities) == 3


def test_project_and_brand_may_belong_to_different_companies(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project_company = create_company(client, "Project customer")
    brand_company = create_company(client, "Brand owner")
    project = create_project(client, project_company["id"])
    brand = create_brand(client, brand_company["id"])

    material = create_material(client, project["id"], brand["id"])

    assert material["project_id"] == project["id"]
    assert material["published_brand_id"] == brand["id"]


@pytest.mark.parametrize(
    ("missing_field", "expected_detail"),
    [
        ("project_id", "Project not found."),
        ("published_brand_id", "Published brand not found."),
    ],
)
def test_material_parents_must_exist(
    material_client: tuple[TestClient, Database],
    missing_field: str,
    expected_detail: str,
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    payload = material_payload(project["id"], brand["id"])
    payload[missing_field] = str(uuid4())

    response = client.post("/api/materials", json=payload)

    assert response.status_code == 404
    assert response.json()["detail"] == expected_detail


def test_exhausted_sequence_returns_controlled_conflict(
    material_client: tuple[TestClient, Database],
) -> None:
    client, database = material_client
    project, brand = setup_material_parents(client)
    with database.session() as session:
        stored_brand = session.get(PublishedBrand, UUID(str(brand["id"])))
        assert stored_brand is not None
        stored_brand.next_sequence_number = 10000
        session.commit()

    response = client.post("/api/materials", json=material_payload(project["id"], brand["id"]))

    assert response.status_code == 409
    assert response.json()["detail"] == "Published brand sequence is exhausted."
    assert client.get("/api/materials").json() == []


def test_sequence_9999_is_allocated_before_exhaustion(
    material_client: tuple[TestClient, Database],
) -> None:
    client, database = material_client
    project, brand = setup_material_parents(client)
    with database.session() as session:
        stored_brand = session.get(PublishedBrand, UUID(str(brand["id"])))
        assert stored_brand is not None
        stored_brand.next_sequence_number = 9999
        session.commit()

    material = create_material(client, project["id"], brand["id"])
    conflict = client.post("/api/materials", json=material_payload(project["id"], brand["id"]))

    assert material["sequence_number"] == 9999
    assert material["technical_identity"] == "ACME_9999_G03"
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "field_name",
    [
        "id",
        "sequence_number",
        "technical_identity",
        "published_brand_id",
        "created_at",
        "next_sequence_number",
    ],
)
def test_managed_material_fields_cannot_be_patched(
    material_client: tuple[TestClient, Database],
    field_name: str,
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(client, project["id"], brand["id"])
    values: dict[str, object] = {
        "id": str(uuid4()),
        "sequence_number": 7,
        "technical_identity": "MANUAL_0007_G01",
        "published_brand_id": str(uuid4()),
        "created_at": "2026-01-01T00:00:00Z",
        "next_sequence_number": 8,
    }

    response = client.patch(
        f"/api/materials/{material['id']}",
        json={field_name: values[field_name]},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_sequence_number_cannot_be_supplied_on_create(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)

    response = client.post(
        "/api/materials",
        json=material_payload(project["id"], brand["id"], sequence_number=42),
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("workflow_status", "DONE"),
        ("validation_status", "VALID"),
        ("is_published", True),
        ("publication_status", "PUBLISHED_CURRENT"),
    ],
)
def test_material_status_fields_are_read_only(
    material_client: tuple[TestClient, Database],
    method: str,
    field_name: str,
    value: object,
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    if method == "post":
        response = client.post(
            "/api/materials",
            json=material_payload(project["id"], brand["id"], **{field_name: value}),
        )
    else:
        material = create_material(client, project["id"], brand["id"])
        response = client.patch(
            f"/api/materials/{material['id']}",
            json={field_name: value},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_create_rejects_missing_processor(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)

    response = client.post(
        "/api/materials",
        json=material_payload(
            project["id"],
            brand["id"],
            assigned_processor_id=str(uuid4()),
        ),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Internal user not found."


def test_create_rejects_inactive_processor(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    inactive_user = create_internal_user(client, is_active=False)

    response = client.post(
        "/api/materials",
        json=material_payload(
            project["id"],
            brand["id"],
            assigned_processor_id=inactive_user["id"],
        ),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Internal user is inactive."


def test_patch_rejects_missing_and_inactive_processor(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(client, project["id"], brand["id"])
    inactive_user = create_internal_user(client, is_active=False)

    missing_response = client.patch(
        f"/api/materials/{material['id']}",
        json={"assigned_processor_id": str(uuid4())},
    )
    inactive_response = client.patch(
        f"/api/materials/{material['id']}",
        json={"assigned_processor_id": inactive_user["id"]},
    )

    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "Internal user not found."
    assert inactive_response.status_code == 409
    assert inactive_response.json()["detail"] == "Internal user is inactive."


def test_patch_updates_allowed_metadata(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    first_company = create_company(client, "First")
    second_company = create_company(client, "Second")
    project = create_project(client, first_company["id"])
    other_project = create_project(client, second_company["id"], project_number="PRJ-002")
    brand = create_brand(client, first_company["id"])
    material = create_material(client, project["id"], brand["id"])
    processor = create_internal_user(client)

    response = client.patch(
        f"/api/materials/{material['id']}",
        json={
            "project_id": other_project["id"],
            "material_name": "Updated name",
            "assigned_processor_id": processor["id"],
            "folder_path": "materials/ACME_0001_G03",
        },
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["project_id"] == other_project["id"]
    assert updated["technical_identity"] == "ACME_0001_G03"
    assert updated["assigned_processor_id"] == processor["id"]
    assert updated["workflow_status"] == "IN_PROGRESS"
    assert updated["validation_status"] == "NOT_CHECKED"
    assert updated["is_published"] is False
    assert updated["publication_status"] == "NOT_PUBLISHED"


def test_folder_path_can_be_set_once_and_repeated_as_noop(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(client, project["id"], brand["id"])
    folder_path = "materials/ACME_0001_G03"

    first_response = client.patch(
        f"/api/materials/{material['id']}",
        json={"folder_path": folder_path},
    )
    repeated_response = client.patch(
        f"/api/materials/{material['id']}",
        json={"folder_path": folder_path},
    )

    assert first_response.status_code == 200
    assert first_response.json()["folder_path"] == folder_path
    assert repeated_response.status_code == 200
    assert repeated_response.json() == first_response.json()


@pytest.mark.parametrize("replacement", ["materials/renamed", None])
def test_existing_folder_path_cannot_be_changed_or_cleared(
    material_client: tuple[TestClient, Database],
    replacement: str | None,
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(
        client,
        project["id"],
        brand["id"],
        folder_path="materials/ACME_0001_G03",
    )

    response = client.patch(
        f"/api/materials/{material['id']}",
        json={"folder_path": replacement},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "folder_path cannot be changed after it has been set."
    )
    assert client.get(f"/api/materials/{material['id']}").json() == material


def test_folder_path_cannot_be_cleared_to_enable_a_later_category_change(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(
        client,
        project["id"],
        brand["id"],
        folder_path="materials/ACME_0001_G03",
    )

    clear_response = client.patch(
        f"/api/materials/{material['id']}",
        json={"folder_path": None},
    )
    category_response = client.patch(
        f"/api/materials/{material['id']}",
        json={"main_category_code": "G04"},
    )
    restore_response = client.patch(
        f"/api/materials/{material['id']}",
        json={"folder_path": material["folder_path"]},
    )

    assert clear_response.status_code == 409
    assert category_response.status_code == 409
    assert restore_response.status_code == 200
    assert restore_response.json() == material
    assert client.get(f"/api/materials/{material['id']}").json() == material


def test_category_change_without_folder_path_regenerates_identity(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(client, project["id"], brand["id"])

    response = client.patch(
        f"/api/materials/{material['id']}",
        json={"main_category_code": "g04"},
    )

    assert response.status_code == 200
    assert response.json()["main_category_code"] == "G04"
    assert response.json()["technical_identity"] == "ACME_0001_G04"


def test_category_and_first_folder_path_can_be_set_together_consistently(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(client, project["id"], brand["id"])

    response = client.patch(
        f"/api/materials/{material['id']}",
        json={
            "main_category_code": "G04",
            "folder_path": "materials/ACME_0001_G04",
        },
    )

    assert response.status_code == 200
    assert response.json()["main_category_code"] == "G04"
    assert response.json()["technical_identity"] == "ACME_0001_G04"
    assert response.json()["folder_path"] == "materials/ACME_0001_G04"


def test_category_change_with_folder_path_returns_conflict(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(
        client,
        project["id"],
        brand["id"],
        folder_path="materials/ACME_0001_G03",
    )

    response = client.patch(
        f"/api/materials/{material['id']}",
        json={"main_category_code": "G04", "folder_path": None},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "main_category_code cannot be changed while folder_path is set."
    )
    assert client.get(f"/api/materials/{material['id']}").json() == material


def test_brand_prefix_cannot_change_after_material_allocation(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    create_material(client, project["id"], brand["id"])

    response = client.patch(f"/api/brands/{brand['id']}", json={"folder_prefix": "RENAMED"})

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "folder_prefix cannot be changed after materials have been created."
    )


def test_material_filters_can_be_combined(
    material_client: tuple[TestClient, Database],
) -> None:
    client, database = material_client
    company = create_company(client)
    first_project = create_project(client, company["id"])
    second_project = create_project(client, company["id"], project_number="PRJ-002")
    first_brand = create_brand(client, company["id"])
    second_brand = create_brand(
        client,
        company["id"],
        name="Second brand",
        folder_prefix="SECOND",
    )
    match = create_material(client, first_project["id"], first_brand["id"])
    warning = create_material(client, first_project["id"], first_brand["id"])
    create_material(client, second_project["id"], second_brand["id"])
    with database.session() as session:
        stored_match = session.get(PBRMaterial, UUID(str(match["id"])))
        stored_warning = session.get(PBRMaterial, UUID(str(warning["id"])))
        assert stored_match is not None
        assert stored_warning is not None
        stored_match.workflow_status = "DONE"
        stored_match.validation_status = "VALID"
        stored_match.publication_status = "PUBLISHED_CURRENT"
        stored_match.is_published = True
        stored_warning.validation_status = "WARNING"
        session.commit()
    match = client.get(f"/api/materials/{match['id']}").json()

    response = client.get(
        "/api/materials",
        params={
            "project_id": first_project["id"],
            "published_brand_id": first_brand["id"],
            "workflow_status": "DONE",
            "validation_status": "VALID",
            "publication_status": "PUBLISHED_CURRENT",
            "is_published": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == [match]
    assert client.get(
        "/api/materials",
        params={"project_id": first_project["id"], "validation_status": "ERROR"},
    ).json() == []


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("project_id", "not-a-uuid"),
        ("workflow_status", "UNKNOWN"),
        ("validation_status", "UNKNOWN"),
        ("publication_status", "UNKNOWN"),
        ("is_published", "not-a-boolean"),
    ],
)
def test_material_filters_reject_invalid_values(
    material_client: tuple[TestClient, Database],
    field_name: str,
    value: str,
) -> None:
    client, _ = material_client
    assert client.get("/api/materials", params={field_name: value}).status_code == 422


@pytest.mark.parametrize(
    "field_name",
    [
        "project_id",
        "published_brand_id",
        "material_name",
        "main_category_code",
        "assigned_processor_id",
    ],
)
def test_create_rejects_explicit_null_for_required_fields(
    material_client: tuple[TestClient, Database],
    field_name: str,
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    payload = material_payload(project["id"], brand["id"])
    payload[field_name] = None

    assert client.post("/api/materials", json=payload).status_code == 422


@pytest.mark.parametrize(
    "field_name",
    [
        "project_id",
        "material_name",
        "main_category_code",
        "assigned_processor_id",
    ],
)
def test_patch_rejects_explicit_null_for_required_fields(
    material_client: tuple[TestClient, Database],
    field_name: str,
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    material = create_material(client, project["id"], brand["id"])

    response = client.patch(
        f"/api/materials/{material['id']}",
        json={field_name: None},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("method", ["post", "patch"])
def test_material_payloads_reject_unexpected_fields(
    material_client: tuple[TestClient, Database],
    method: str,
) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    if method == "post":
        response = client.post(
            "/api/materials",
            json=material_payload(project["id"], brand["id"], unexpected="value"),
        )
    else:
        material = create_material(client, project["id"], brand["id"])
        response = client.patch(
            f"/api/materials/{material['id']}",
            json={"unexpected": "value"},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_missing_material_detail_and_patch_return_404(
    material_client: tuple[TestClient, Database],
) -> None:
    client, _ = material_client
    missing_id = uuid4()
    assert client.get(f"/api/materials/{missing_id}").status_code == 404
    assert client.patch(f"/api/materials/{missing_id}", json={}).status_code == 404


def test_material_list_has_stable_order(material_client: tuple[TestClient, Database]) -> None:
    client, _ = material_client
    project, brand = setup_material_parents(client)
    create_material(client, project["id"], brand["id"])
    create_material(client, project["id"], brand["id"])

    first = client.get("/api/materials").json()
    second = client.get("/api/materials").json()

    assert first == second
    assert first == sorted(first, key=lambda item: (item["created_at"], item["id"]))


def test_database_constraints_reject_invalid_material_rows(
    material_client: tuple[TestClient, Database],
) -> None:
    client, database = material_client
    project, brand = setup_material_parents(client)
    processor = create_internal_user(
        client,
        display_name="Constraint processor",
        email="constraint.processor@example.com",
    )
    stored_values = {
        "project_id": UUID(str(project["id"])),
        "published_brand_id": UUID(str(brand["id"])),
        "material_name": "Invalid",
        "main_category_code": "G03",
        "assigned_processor_id": UUID(str(processor["id"])),
        "technical_identity": "ACME_0000_G03",
    }

    for override in (
        {"sequence_number": 0},
        {"sequence_number": 1, "workflow_status": "UNKNOWN"},
        {"sequence_number": 1, "validation_status": "UNKNOWN"},
        {"sequence_number": 1, "publication_status": "UNKNOWN"},
    ):
        with database.session() as session:
            session.add(PBRMaterial(**stored_values, **override))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()


def test_database_enforces_unique_brand_sequence_and_technical_identity(
    material_client: tuple[TestClient, Database],
) -> None:
    client, database = material_client
    project, brand = setup_material_parents(client)
    material = create_material(client, project["id"], brand["id"])
    processor = create_internal_user(
        client,
        display_name="Unique constraint processor",
        email="unique.constraint.processor@example.com",
    )

    for technical_identity in ("ACME_DUPLICATE_G03", material["technical_identity"]):
        with database.session() as session:
            session.add(
                PBRMaterial(
                    project_id=UUID(str(project["id"])),
                    published_brand_id=UUID(str(brand["id"])),
                    sequence_number=(
                        1 if technical_identity != material["technical_identity"] else 2
                    ),
                    material_name="Duplicate",
                    main_category_code="G03",
                    assigned_processor_id=UUID(str(processor["id"])),
                    technical_identity=technical_identity,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()


def test_database_restricts_deleting_an_assigned_processor(
    material_client: tuple[TestClient, Database],
) -> None:
    client, database = material_client
    project, brand = setup_material_parents(client)
    material = create_material(client, project["id"], brand["id"])

    with database.session() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                delete(InternalUser).where(InternalUser.id == DEFAULT_PROCESSOR_ID)
            )
            session.commit()
        session.rollback()

    assert client.get(f"/api/materials/{material['id']}").status_code == 200
