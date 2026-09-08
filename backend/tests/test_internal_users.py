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


def create_user(client: TestClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "Material Processor",
        "email": "processor@example.com",
        "role": "PROCESSOR",
    }
    payload.update(overrides)
    response = client.post("/api/internal-users", json=payload)
    assert response.status_code == 201
    return response.json()


def test_internal_user_create_list_detail_patch_without_delete(client: TestClient) -> None:
    response = client.post(
        "/api/internal-users",
        json={
            "display_name": "  Material Processor  ",
            "email": "  PROCESSOR@EXAMPLE.COM  ",
            "role": "PROCESSOR",
        },
    )

    assert response.status_code == 201
    user = response.json()
    assert user["display_name"] == "Material Processor"
    assert user["email"] == "processor@example.com"
    assert user["role"] == "PROCESSOR"
    assert user["is_active"] is True
    assert user["created_at"]
    assert user["updated_at"]
    assert client.get(f"/api/internal-users/{user['id']}").json() == user
    assert client.get("/api/internal-users").json() == [user]

    patch_response = client.patch(
        f"/api/internal-users/{user['id']}",
        json={"role": "PRODUCTION_LEAD", "is_active": False},
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["role"] == "PRODUCTION_LEAD"
    assert patch_response.json()["is_active"] is False
    assert client.delete(f"/api/internal-users/{user['id']}").status_code == 405


def test_internal_user_email_is_unique(client: TestClient) -> None:
    create_user(client)

    response = client.post(
        "/api/internal-users",
        json={
            "display_name": "Duplicate",
            "email": "PROCESSOR@EXAMPLE.COM",
            "role": "ADMIN",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "email already exists."


@pytest.mark.parametrize("method", ["post", "patch"])
def test_internal_user_role_is_validated(client: TestClient, method: str) -> None:
    if method == "post":
        response = client.post(
            "/api/internal-users",
            json={
                "display_name": "Invalid role",
                "email": "invalid@example.com",
                "role": "UNKNOWN",
            },
        )
    else:
        user = create_user(client)
        response = client.patch(
            f"/api/internal-users/{user['id']}",
            json={"role": "UNKNOWN"},
        )

    assert response.status_code == 422


def test_internal_user_filters_can_be_combined(client: TestClient) -> None:
    match = create_user(client, display_name="Active Processor")
    create_user(
        client,
        display_name="Inactive Processor",
        email="inactive@example.com",
        is_active=False,
    )
    create_user(
        client,
        display_name="Administrator",
        email="admin@example.com",
        role="ADMIN",
    )

    response = client.get(
        "/api/internal-users",
        params={"role": "PROCESSOR", "is_active": True, "search": "processor"},
    )

    assert response.status_code == 200
    assert response.json() == [match]


@pytest.mark.parametrize("field_name", ["display_name", "email", "role", "is_active"])
def test_internal_user_patch_rejects_explicit_null(
    client: TestClient,
    field_name: str,
) -> None:
    user = create_user(client)

    response = client.patch(
        f"/api/internal-users/{user['id']}",
        json={field_name: None},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("method", ["post", "patch"])
def test_internal_user_rejects_unexpected_fields(client: TestClient, method: str) -> None:
    if method == "post":
        response = client.post(
            "/api/internal-users",
            json={
                "display_name": "Unexpected",
                "email": "unexpected@example.com",
                "role": "LEADERSHIP",
                "password": "not-supported",
            },
        )
    else:
        user = create_user(client)
        response = client.patch(
            f"/api/internal-users/{user['id']}",
            json={"password": "not-supported"},
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"


def test_missing_internal_user_returns_404(client: TestClient) -> None:
    missing_id = uuid4()
    assert client.get(f"/api/internal-users/{missing_id}").status_code == 404
    assert client.patch(f"/api/internal-users/{missing_id}", json={}).status_code == 404
