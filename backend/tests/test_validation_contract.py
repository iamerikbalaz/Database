"""The auth hardening must preserve field errors used by public resource forms."""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.base import Base
from app.db.session import Database
from domain_support import create_domain_app as create_app


@dataclass(frozen=True)
class _Resource:
    path: str
    name_field: str
    other_field: str
    payload: dict[str, str]


RESOURCES = [
    pytest.param(
        _Resource("/api/companies", "name", "country", {"name": "Example Company"}),
        id="company",
    ),
    pytest.param(
        _Resource(
            "/api/projects",
            "name",
            "project_number",
            {
                "company_id": "00000000-0000-4000-8000-000000000001",
                "project_number": "PRJ-001",
                "name": "Example Project",
            },
        ),
        id="project",
    ),
    pytest.param(
        _Resource(
            "/api/materials",
            "material_name",
            "main_category_code",
            {
                "project_id": "00000000-0000-4000-8000-000000000002",
                "published_brand_id": "00000000-0000-4000-8000-000000000003",
                "material_name": "Example Material",
                "main_category_code": "STONE",
                "assigned_processor_id": "00000000-0000-4000-8000-000000000004",
            },
        ),
        id="material",
    ),
]


@pytest.fixture
def resource_client() -> Iterator[TestClient]:
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    application = create_app(Settings(_env_file=None), database)
    with TestClient(application) as client:
        yield client


@pytest.mark.parametrize("resource", RESOURCES)
def test_resource_missing_field_preserves_form_error_contract(
    resource_client: TestClient, resource: _Resource
) -> None:
    payload = dict(resource.payload)
    del payload[resource.name_field]

    response = resource_client.post(resource.path, json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "missing",
                "loc": ["body", resource.name_field],
                "msg": "Field required",
                "input": payload,
            }
        ]
    }


@pytest.mark.parametrize("resource", RESOURCES)
@pytest.mark.parametrize(
    ("invalid", "error_type", "message", "context"),
    [
        (
            "   ",
            "string_too_short",
            "String should have at least 1 character",
            {"min_length": 1},
        ),
        (
            "x" * 256,
            "string_too_long",
            "String should have at most 255 characters",
            {"max_length": 255},
        ),
        (["not-a-string"], "string_type", "Input should be a valid string", None),
    ],
    ids=["too-short", "too-long", "wrong-type"],
)
def test_resource_field_validation_preserves_input_message_and_context(
    resource_client: TestClient,
    resource: _Resource,
    invalid: object,
    error_type: str,
    message: str,
    context: dict[str, int] | None,
) -> None:
    payload = {**resource.payload, resource.name_field: invalid}
    expected = {
        "type": error_type,
        "loc": ["body", resource.name_field],
        "msg": message,
        "input": invalid,
    }
    if context is not None:
        expected["ctx"] = context

    response = resource_client.post(resource.path, json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": [expected]}


@pytest.mark.parametrize("resource", RESOURCES)
def test_resource_multiple_errors_keep_individual_field_locations(
    resource_client: TestClient, resource: _Resource
) -> None:
    payload = {
        **resource.payload,
        resource.name_field: ["not-a-string"],
        resource.other_field: "",
        "unexpected_field": "public-extra-value",
    }

    response = resource_client.post(resource.path, json=payload)

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail"}
    assert len(body["detail"]) == 3
    # The form maps each unchanged body-field loc to its own input; no general
    # auth error or collapsed location may replace this contract.
    errors_by_field = {error["loc"][1]: error for error in body["detail"]}
    assert errors_by_field == {
        resource.name_field: {
            "type": "string_type",
            "loc": ["body", resource.name_field],
            "msg": "Input should be a valid string",
            "input": ["not-a-string"],
        },
        resource.other_field: {
            "type": "string_too_short",
            "loc": ["body", resource.other_field],
            "msg": "String should have at least 1 character",
            "input": "",
            "ctx": {"min_length": 1},
        },
        "unexpected_field": {
            "type": "extra_forbidden",
            "loc": ["body", "unexpected_field"],
            "msg": "Extra inputs are not permitted",
            "input": "public-extra-value",
        },
    }


@pytest.mark.parametrize("resource", RESOURCES)
def test_resource_lists_remain_public(resource_client: TestClient, resource: _Resource) -> None:
    assert not resource_client.cookies
    response = resource_client.get(resource.path)

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("path", ["/api/authentication", "/api/auth-other"])
@pytest.mark.parametrize("location", [("body", "name"), ("body", "items", 0, "name")])
def test_auth_lookalike_paths_retain_public_validation_contract(
    resource_client: TestClient, path: str, location: tuple[str | int, ...]
) -> None:
    error = {
        "type": "value_error",
        "loc": location,
        "msg": "Ordinary public name is invalid",
        "input": "public-form-value",
        "ctx": {"rule": "public-rule", "limit": 255},
    }

    async def ordinary_resource() -> None:
        raise RequestValidationError([error], body={"name": "public-form-value"})

    resource_client.app.add_api_route(path, ordinary_resource, methods=["POST"])

    response = resource_client.post(path, json={"name": "public-form-value"})

    assert response.status_code == 422
    assert response.json() == {"detail": [{**error, "loc": list(location)}]}

