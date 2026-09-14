from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi import Body, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.auth.cli import provision_first_admin
from app.auth.schemas import ChangePasswordRequest, LoginRequest
from app.auth.security import (
    MAX_RAW_PASSWORD_LENGTH,
    PasswordPolicyError,
    normalize_email,
    normalize_password,
    validate_new_password,
    validate_password_input,
)
from app.auth.validation import safe_request_validation_handler
from app.core.config import Settings
from app.db.base import Base
from app.db.session import Database
from app.main import create_app


ORIGIN = "https://localhost"
EMAIL = "account@example.invalid"
PASSWORD = "Quartz meadow river! 2026"


class NestedInput(BaseModel):
    password: int


@pytest.fixture
def validation_client() -> Iterator[TestClient]:
    settings = Settings(_env_file=None, cors_origins=ORIGIN)
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    provision_first_admin(
        database, settings, email=EMAIL, display_name="Site Operator", password=PASSWORD
    )
    with TestClient(create_app(settings, database), base_url=ORIGIN) as client:
        yield client


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ABC@Example.Invalid", "abc@example.invalid"),
        ("  Abc@Example.Invalid \t", "abc@example.invalid"),
        ("GROẞ@Example.Invalid", "groß@example.invalid"),
        ("groß@example.invalid", "groß@example.invalid"),
        ("E\u0301@Example.Invalid", "e\u0301@example.invalid"),
    ],
)
def test_email_normalization_matches_existing_storage(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected
    assert LoginRequest(email=raw, password=PASSWORD).email == expected
    assert normalize_email(expected) == expected


def test_email_does_not_merge_distinct_existing_unicode_addresses() -> None:
    assert normalize_email("groß@example.invalid") != normalize_email("gross@example.invalid")
    assert normalize_email("é@example.invalid") != normalize_email("e\u0301@example.invalid")
    assert normalize_email("Ａ@example.invalid") != normalize_email("a@example.invalid")


@pytest.mark.parametrize("email", ["groß@example.invalid", "ｇｒｏｓｓ@example.invalid"])
def test_email_identity_strategy_does_not_weaken_password_context_policy(email: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_new_password(
            "The gross account phrase!", email=email, display_name="Site Operator"
        )


@pytest.mark.parametrize("length", [14, 15, 256, 257])
def test_normalized_password_boundaries_are_shared_by_all_entry_points(length: int) -> None:
    # Raw code-point count is almost twice the normalized count.
    candidate = "e\u0301" * (length - 1) + "X"
    expected = "é" * (length - 1) + "X"
    validators = [
        lambda: validate_password_input(candidate),
        lambda: validate_new_password(candidate, email=EMAIL, display_name="Site Operator"),
        lambda: LoginRequest(email=EMAIL, password=candidate).password,
        lambda: ChangePasswordRequest(
            current_password=candidate, new_password=candidate
        ).new_password,
    ]
    for validate in validators:
        if length in {15, 256}:
            assert validate() == expected
        else:
            with pytest.raises((PasswordPolicyError, ValidationError)):
                validate()


def test_combining_unicode_regression_can_be_created_and_logged_in() -> None:
    candidate = ("e\u0301" * 128) + "X"
    settings = Settings(_env_file=None, cors_origins=ORIGIN)
    database = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    provision_first_admin(
        database, settings, email=EMAIL, display_name="Site Operator", password=candidate
    )
    with TestClient(create_app(settings, database), base_url=ORIGIN) as client:
        for representation in (candidate, normalize_password(candidate)):
            response = client.post(
                "/api/auth/login",
                json={"email": EMAIL, "password": representation},
                headers={"Origin": ORIGIN},
            )
            assert response.status_code == 200
        response = client.post(
            "/api/auth/change-password",
            json={"current_password": candidate, "new_password": candidate + "Y"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]},
        )
        assert response.status_code == 200
        response = client.post(
            "/api/auth/login",
            json={"email": EMAIL, "password": normalize_password(candidate + "Y")},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200


@pytest.mark.parametrize("route", ["login", "change-password"])
@pytest.mark.parametrize(
    "invalid",
    ["short-secret!", "long-secret!" * 30, {"value": "wrong-type-secret!"}, 123456789],
    ids=["short", "long", "wrong-object-type", "wrong-number-type"],
)
def test_sensitive_validation_never_reflects_inputs_or_names(
    validation_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    route: str,
    invalid: object,
) -> None:
    client = validation_client
    headers = {"Origin": ORIGIN}
    if route == "change-password":
        logged_in = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=headers
        )
        assert logged_in.status_code == 200
        headers["X-CSRF-Token"] = logged_in.json()["csrf_token"]
        payload = {"current_password": invalid, "new_password": invalid}
    else:
        payload = {"email": EMAIL, "password": invalid}

    caplog.set_level(logging.DEBUG)
    response = client.post(f"/api/auth/{route}", json=payload, headers=headers)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    errors = response.json()["detail"]
    assert len(errors) == (2 if route == "change-password" else 1)
    assert all("input" not in error and "ctx" not in error for error in errors)
    for name in ("password", "current_password", "new_password", "confirmation"):
        assert name not in response.text
    secrets = [str(invalid)] if not isinstance(invalid, dict) else list(invalid.values())
    for secret in [*secrets, PASSWORD, headers.get("X-CSRF-Token", "unused-token-marker")]:
        assert secret not in response.text + caplog.text
    assert "$argon2" not in response.text + caplog.text


def test_multiple_errors_redact_secrets_copied_into_other_fields(
    validation_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    marker = "short-secret!"
    response = validation_client.post(
        "/api/auth/login",
        json={"email": marker, "password": marker, "confirmation": marker},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 422
    assert len(response.json()["detail"]) == 3
    for secret in (marker, "password", "confirmation"):
        assert secret not in response.text
    assert marker not in caplog.text


def test_tokens_hashes_and_confirmation_are_never_reflected_in_errors(
    validation_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    client = validation_client
    cookie = "unique-session-cookie-value"
    csrf = "unique-csrf-header-value"
    confirmation = "unique-confirmation-value"
    password_hash = "$argon2id$v=19$m=19456,t=2,p=1$unique-salt$unique-hash"
    client.cookies.set("__Host-reawote_session", cookie)
    caplog.set_level(logging.DEBUG)
    response = client.post(
        "/api/auth/login",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "confirmation": confirmation,
            "copied_cookie": cookie,
            "copied_header": csrf,
            "unrelated_value": password_hash,
        },
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )
    assert response.status_code == 422
    assert len(response.json()["detail"]) == 4
    for secret in (cookie, csrf, confirmation, password_hash, PASSWORD):
        assert secret not in response.text + caplog.text
    for name in ("password", "confirmation", "session_token", "csrf_token"):
        assert name not in response.text


def test_missing_field_error_does_not_reflect_parent_secrets(
    validation_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    response = validation_client.post(
        "/api/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "email"]
    assert PASSWORD not in response.text + caplog.text
    assert "password" not in response.text


@pytest.mark.parametrize("payload", ["bare-secret-body-value", ["bare-secret-body-value"]])
def test_invalid_auth_body_cannot_reflect_a_bare_secret(
    validation_client: TestClient, payload: object, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    response = validation_client.post(
        "/api/auth/login", json=payload, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 422
    assert "input" not in response.json()["detail"][0]
    assert "bare-secret-body-value" not in response.text + caplog.text


def test_extreme_raw_input_is_rejected_without_reflection(validation_client: TestClient) -> None:
    candidate = "giant-secret!" * MAX_RAW_PASSWORD_LENGTH
    response = validation_client.post(
        "/api/auth/login",
        json={"email": EMAIL, "password": candidate},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 422
    assert "giant-secret!" not in response.text
    assert len(response.content) < 1024


def test_regular_validation_preserves_useful_input(validation_client: TestClient) -> None:
    response = validation_client.post(
        "/api/auth/login",
        json={"email": "bad-email", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "email"]
    assert error["input"] == "bad-email"
    assert error["type"] == "string_pattern_mismatch"


def test_nested_locations_and_parent_inputs_are_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = FastAPI()
    application.add_exception_handler(RequestValidationError, safe_request_validation_handler)

    @application.post("/nested")
    def nested(payload: list[NestedInput] = Body()):
        return {"ok": True}

    @application.post("/parent")
    def parent(payload: int = Body()):
        return {"ok": True}

    caplog.set_level(logging.DEBUG)
    with TestClient(application) as client:
        nested_response = client.post("/nested", json=[{"password": "nested-secret-value"}])
        parent_response = client.post(
            "/parent",
            json={"nested": [{"password": "nested-secret-value"}], "ordinary": "useful"},
        )
        malformed_response = client.post(
            "/parent", content='{"password":"malformed-secret-value",',
            headers={"Content-Type": "application/json"},
        )
    for response in (nested_response, parent_response, malformed_response):
        assert response.status_code == 422
        assert "password" not in response.text
        assert "nested-secret-value" not in response.text + caplog.text
        assert "malformed-secret-value" not in response.text + caplog.text
    error = nested_response.json()["detail"][0]
    assert error["loc"] == ["body", 0, "[redacted]"]
    assert "input" not in error
    assert parent_response.json()["detail"][0]["input"]["ordinary"] == "useful"


def test_errors_without_body_protect_sensitive_inputs_context_and_dynamic_locations(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = FastAPI()
    application.add_exception_handler(RequestValidationError, safe_request_validation_handler)
    marker = "secret-only-present-in-error-input"
    dynamic_key = "secret-used-as-a-dictionary-key"
    context_secret = "secret-only-present-in-custom-context"

    class ReflectedError(ValueError):
        def __init__(self):
            self.new_password = context_secret
            super().__init__(context_secret)

    @application.post("/errors")
    def errors():
        raise RequestValidationError([
            {"type": "string_type", "loc": ("query", "password"), "input": marker,
             "msg": "Invalid value"},
            {"type": "int_parsing", "loc": ("body", "nested", 0, "new_password", dynamic_key),
             "input": "invalid", "ctx": {"error": ValueError(marker)}, "msg": marker},
            {"type": "value_error", "loc": ("body", "ordinary"), "input": marker,
             "ctx": {"error": ReflectedError()}, "msg": f"Invalid value: {context_secret}"},
        ])

    caplog.set_level(logging.DEBUG)
    with TestClient(application) as client:
        response = client.post("/errors")
    assert response.status_code == 422
    assert len(response.json()["detail"]) == 3
    for secret in (marker, dynamic_key, context_secret):
        assert secret not in response.text + caplog.text
    for name in ("password", "new_password"):
        assert name not in response.text
    assert response.json()["detail"][1]["loc"] == ["body", "nested", 0, "[redacted]"]


def test_malformed_json_prevents_other_errors_from_reflecting_the_unparsed_body() -> None:
    application = FastAPI()
    application.add_exception_handler(RequestValidationError, safe_request_validation_handler)
    marker = "bare-value-without-any-sensitive-key"

    @application.post("/errors")
    def errors():
        raise RequestValidationError([
            {"type": "json_invalid", "loc": ("body", 1), "input": marker,
             "ctx": {"error": marker}, "msg": marker},
            {"type": "value_error", "loc": ("body", "ordinary"), "input": marker,
             "ctx": {"error": ValueError(marker)}, "msg": marker},
        ], body=f'"{marker}",')

    with TestClient(application) as client:
        response = client.post("/errors")
    assert response.status_code == 422
    assert marker not in response.text
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
