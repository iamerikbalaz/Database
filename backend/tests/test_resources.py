from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.base import Base
from app.db.session import Database
from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    application = create_app(Settings(), database)
    with TestClient(application) as test_client:
        yield test_client


def create_company(client: TestClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": "Acme"}
    payload.update(overrides)
    response = client.post("/api/companies", json=payload)
    assert response.status_code == 201
    return response.json()


def create_brand(
    client: TestClient,
    company_id: object,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "company_id": company_id,
        "name": "Acme Surfaces",
        "folder_prefix": "ACME",
        "brand_identifier": "acme-surfaces",
    }
    payload.update(overrides)
    response = client.post("/api/brands", json=payload)
    assert response.status_code == 201
    return response.json()


def create_project(
    client: TestClient,
    company_id: object,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "company_id": company_id,
        "project_number": "PRJ-001",
        "name": "Material library",
    }
    payload.update(overrides)
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201
    return response.json()


def test_company_crud_without_delete(client: TestClient) -> None:
    response = client.post(
        "/api/companies",
        json={
            "name": "  Acme  ",
            "legal_name": "Acme s.r.o.",
            "country": "CZ",
            "address": "Prague",
            "website": "https://example.com",
            "vat_id": "CZ123",
            "notion_page_id": "notion-company-1",
        },
    )

    assert response.status_code == 201
    company = response.json()
    assert company["name"] == "Acme"
    assert company["website"] == "https://example.com/"
    assert company["is_active"] is True
    assert company["created_at"]
    assert company["updated_at"]

    assert client.get(f"/api/companies/{company['id']}").json() == company
    assert client.get("/api/companies").json() == [company]

    response = client.patch(
        f"/api/companies/{company['id']}",
        json={"legal_name": None, "is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["legal_name"] is None
    assert response.json()["is_active"] is False
    assert client.delete(f"/api/companies/{company['id']}").status_code == 405


def test_company_validation_and_unique_notion_id(client: TestClient) -> None:
    assert client.post("/api/companies", json={"name": "   "}).status_code == 422
    assert (
        client.post(
            "/api/companies",
            json={"name": "Invalid website", "website": "not-a-url"},
        ).status_code
        == 422
    )
    company = create_company(client, notion_page_id="same-notion-id")

    response = client.post(
        "/api/companies",
        json={"name": "Other", "notion_page_id": "same-notion-id"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "notion_page_id already exists."
    assert client.patch(f"/api/companies/{company['id']}", json={"name": None}).status_code == 422


def test_published_brand_crud_and_unique_values(client: TestClient) -> None:
    company = create_company(client)
    brand = create_brand(client, company["id"])
    assert brand["next_sequence_number"] == 1
    assert brand["is_active"] is True
    assert client.get(f"/api/brands/{brand['id']}").json() == brand
    assert client.get("/api/brands").json() == [brand]

    response = client.patch(
        f"/api/brands/{brand['id']}",
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["next_sequence_number"] == 1
    assert response.json()["is_active"] is False

    duplicate_prefix = client.post(
        "/api/brands",
        json={
            "company_id": company["id"],
            "name": "Second",
            "folder_prefix": "ACME",
            "brand_identifier": "second",
        },
    )
    assert duplicate_prefix.status_code == 409
    assert duplicate_prefix.json()["detail"] == "folder_prefix already exists."

    duplicate_identifier = client.post(
        "/api/brands",
        json={
            "company_id": company["id"],
            "name": "Third",
            "folder_prefix": "THIRD",
            "brand_identifier": "acme-surfaces",
        },
    )
    assert duplicate_identifier.status_code == 409
    assert duplicate_identifier.json()["detail"] == "brand_identifier already exists."
    assert client.delete(f"/api/brands/{brand['id']}").status_code == 405


def test_project_crud_and_unique_project_number(client: TestClient) -> None:
    company = create_company(client)
    response = client.post(
        "/api/projects",
        json={
            "company_id": company["id"],
            "project_number": "PRJ-001",
            "name": "Material library",
            "due_date": "2026-12-01",
            "notes": "Initial scope",
        },
    )

    assert response.status_code == 201
    project = response.json()
    assert project["status"] == "NOT_STARTED"
    assert project["due_date"] == "2026-12-01"
    assert client.get(f"/api/projects/{project['id']}").json() == project
    assert client.get("/api/projects").json() == [project]

    response = client.patch(
        f"/api/projects/{project['id']}",
        json={"status": "IN_PROGRESS", "due_date": None, "notes": None},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "IN_PROGRESS"
    assert response.json()["due_date"] is None
    assert response.json()["notes"] is None

    duplicate = client.post(
        "/api/projects",
        json={
            "company_id": company["id"],
            "project_number": "PRJ-001",
            "name": "Duplicate",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "project_number already exists."
    assert client.delete(f"/api/projects/{project['id']}").status_code == 405


@pytest.mark.parametrize("method", ["post", "patch"])
def test_brand_sequence_number_cannot_be_set_through_api(
    client: TestClient,
    method: str,
) -> None:
    company = create_company(client)
    payload: dict[str, object] = {"next_sequence_number": 7}
    url = "/api/brands"
    if method == "post":
        payload.update(
            {
                "company_id": company["id"],
                "name": "Managed sequence",
                "folder_prefix": "MANAGED",
                "brand_identifier": "managed-sequence",
            }
        )
    else:
        brand = create_brand(client, company["id"])
        url = f"/api/brands/{brand['id']}"

    response = getattr(client, method)(url, json=payload)

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_patch_unique_conflicts_return_409(client: TestClient) -> None:
    company = create_company(client)
    first_brand = create_brand(client, company["id"])
    second_brand = create_brand(
        client,
        company["id"],
        name="Second brand",
        folder_prefix="SECOND",
        brand_identifier="second-brand",
    )
    first_project = create_project(client, company["id"])
    second_project = create_project(
        client,
        company["id"],
        project_number="PRJ-002",
        name="Second project",
    )

    conflicts = (
        (
            f"/api/brands/{second_brand['id']}",
            {"folder_prefix": first_brand["folder_prefix"]},
            "folder_prefix already exists.",
        ),
        (
            f"/api/brands/{second_brand['id']}",
            {"brand_identifier": first_brand["brand_identifier"]},
            "brand_identifier already exists.",
        ),
        (
            f"/api/projects/{second_project['id']}",
            {"project_number": first_project["project_number"]},
            "project_number already exists.",
        ),
    )
    for url, payload, detail in conflicts:
        response = client.patch(url, json=payload)
        assert response.status_code == 409
        assert response.json()["detail"] == detail


@pytest.mark.parametrize(
    ("resource", "create_payload", "required_fields"),
    [
        ("companies", {"name": "Company"}, ("name", "is_active")),
        (
            "brands",
            {
                "name": "Brand",
                "folder_prefix": "BRAND",
                "brand_identifier": "brand",
                "is_active": True,
            },
            ("company_id", "name", "folder_prefix", "brand_identifier", "is_active"),
        ),
        (
            "projects",
            {"project_number": "PRJ-NULL", "name": "Project", "status": "NOT_STARTED"},
            ("company_id", "project_number", "name", "status"),
        ),
    ],
)
def test_patch_rejects_explicit_null_for_required_fields(
    client: TestClient,
    resource: str,
    create_payload: dict[str, object],
    required_fields: tuple[str, ...],
) -> None:
    company = create_company(client)
    if resource == "companies":
        item = company
    else:
        create_payload["company_id"] = company["id"]
        response = client.post(f"/api/{resource}", json=create_payload)
        assert response.status_code == 201
        item = response.json()

    for field_name in required_fields:
        response = client.patch(
            f"/api/{resource}/{item['id']}",
            json={field_name: None},
        )
        assert response.status_code == 422, field_name


def test_brand_company_can_be_changed_to_existing_company(client: TestClient) -> None:
    first_company = create_company(client, name="First")
    second_company = create_company(client, name="Second")
    brand = create_brand(client, first_company["id"])

    response = client.patch(
        f"/api/brands/{brand['id']}",
        json={"company_id": second_company["id"]},
    )

    assert response.status_code == 200
    assert response.json()["company_id"] == second_company["id"]


def test_brand_company_cannot_be_changed_to_missing_company(client: TestClient) -> None:
    company = create_company(client)
    brand = create_brand(client, company["id"])

    response = client.patch(
        f"/api/brands/{brand['id']}",
        json={"company_id": str(uuid4())},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found."


@pytest.mark.parametrize("resource", ["companies", "brands", "projects"])
def test_create_rejects_unexpected_fields(client: TestClient, resource: str) -> None:
    company = create_company(client)
    payloads = {
        "companies": {"name": "Unexpected"},
        "brands": {
            "company_id": company["id"],
            "name": "Unexpected",
            "folder_prefix": "UNEXPECTED",
            "brand_identifier": "unexpected",
        },
        "projects": {
            "company_id": company["id"],
            "project_number": "PRJ-EXTRA",
            "name": "Unexpected",
        },
    }
    payloads[resource]["unexpected"] = "value"

    response = client.post(f"/api/{resource}", json=payloads[resource])

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("resource", ["companies", "brands", "projects"])
def test_missing_detail_and_patch_return_404(client: TestClient, resource: str) -> None:
    missing_id = uuid4()
    assert client.get(f"/api/{resource}/{missing_id}").status_code == 404
    assert client.patch(f"/api/{resource}/{missing_id}", json={}).status_code == 404


@pytest.mark.parametrize("resource", ["brands", "projects"])
def test_required_company_must_exist(client: TestClient, resource: str) -> None:
    payload = (
        {
            "company_id": str(uuid4()),
            "name": "Brand",
            "folder_prefix": "BRAND",
            "brand_identifier": "brand",
        }
        if resource == "brands"
        else {
            "company_id": str(uuid4()),
            "project_number": "PRJ-404",
            "name": "Project",
        }
    )
    response = client.post(f"/api/{resource}", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found."


def test_project_status_is_validated(client: TestClient) -> None:
    company = create_company(client)
    invalid_project = client.post(
        "/api/projects",
        json={
            "company_id": company["id"],
            "project_number": "PRJ-INVALID",
            "name": "Invalid",
            "status": "UNKNOWN",
        },
    )
    assert invalid_project.status_code == 422
