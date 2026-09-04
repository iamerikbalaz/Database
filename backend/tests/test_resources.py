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
    response = client.post(
        "/api/brands",
        json={
            "company_id": company["id"],
            "name": "Acme Surfaces",
            "folder_prefix": "ACME",
            "brand_identifier": "acme-surfaces",
        },
    )

    assert response.status_code == 201
    brand = response.json()
    assert brand["next_sequence_number"] == 1
    assert brand["is_active"] is True
    assert client.get(f"/api/brands/{brand['id']}").json() == brand
    assert client.get("/api/brands").json() == [brand]

    response = client.patch(
        f"/api/brands/{brand['id']}",
        json={"next_sequence_number": 7, "is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["next_sequence_number"] == 7
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


def test_project_status_and_sequence_number_are_validated(client: TestClient) -> None:
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
    invalid_brand = client.post(
        "/api/brands",
        json={
            "company_id": company["id"],
            "name": "Invalid",
            "folder_prefix": "INVALID",
            "brand_identifier": "invalid",
            "next_sequence_number": 0,
        },
    )
    assert invalid_project.status_code == 422
    assert invalid_brand.status_code == 422
