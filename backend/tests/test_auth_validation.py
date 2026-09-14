from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Annotated, Literal

import pytest
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, ValidationError

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
def test_sensitive_validation_never_reflects_inputs(
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
    expected_fields = ["current_password", "new_password"] if route == "change-password" else ["password"]
    assert [error["loc"] for error in errors] == [["body", name] for name in expected_fields]
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
    assert marker not in response.text + caplog.text
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


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


def test_extreme_raw_input_is_rejected_without_reflection(
    validation_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    candidate = "giant-secret!" * MAX_RAW_PASSWORD_LENGTH
    response = validation_client.post(
        "/api/auth/login",
        json={"email": EMAIL, "password": candidate},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 422
    assert "giant-secret!" not in response.text + caplog.text
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
    assert len(response.content) < 1024


def test_auth_validation_also_omits_non_secret_inputs(validation_client: TestClient) -> None:
    response = validation_client.post(
        "/api/auth/login",
        json={"email": "bad-email", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "email"]
    assert "input" not in error and "ctx" not in error
    assert "bad-email" not in response.text
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


SECRET_MARKER = "Synthetic-6fcd213f-e845-4791-a0bd-3c031b1fd4b7!"


def assert_private_auth_error(response, caplog: pytest.LogCaptureFixture) -> None:
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert SECRET_MARKER not in response.text + caplog.text
    assert response.json()["detail"]
    for error in response.json()["detail"]:
        assert set(error) == {"type", "loc", "msg"}
        assert "input" not in error and "ctx" not in error
        assert error["msg"] == "Invalid request."


@pytest.mark.parametrize("route", ["login", "change-password"])
@pytest.mark.parametrize("nested", [False, True], ids=["long-string", "nested-object-array"])
@pytest.mark.parametrize("field", [
    "password", "current_password", "new_password", "confirm_password",
    "password_confirmation", "old_password", "password ", "PASSWORD", "future_credential",
])
def test_auth_validation_is_closed_for_every_field_name(
    validation_client: TestClient, caplog: pytest.LogCaptureFixture,
    route: str, nested: bool, field: str,
) -> None:
    client = validation_client
    headers = {"Origin": ORIGIN}
    if route == "login":
        payload = {"email": EMAIL, "password": PASSWORD}
    else:
        logged_in = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=headers
        )
        assert logged_in.status_code == 200
        headers["X-CSRF-Token"] = logged_in.json()["csrf_token"]
        payload = {"current_password": PASSWORD, "new_password": "Violet harbor lanterns 2048"}
    payload[field] = {"arbitrary": [SECRET_MARKER]} if nested else SECRET_MARKER * 8
    caplog.set_level(logging.DEBUG)
    response = client.post(f"/api/auth/{route}", json=payload, headers=headers)
    assert_private_auth_error(response, caplog)


@pytest.mark.parametrize("mode", ["multiple", "malformed", "scalar", "array"])
def test_auth_validation_cannot_reflect_any_part_of_invalid_bodies(
    validation_client: TestClient, caplog: pytest.LogCaptureFixture, mode: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    if mode == "malformed":
        response = validation_client.post(
            "/api/auth/login", content='{"unexpected":"' + SECRET_MARKER + '",',
            headers={"Content-Type": "application/json", "Origin": ORIGIN},
        )
    else:
        bodies = {
            "multiple": {"email": SECRET_MARKER, "password": {"data": SECRET_MARKER},
                         SECRET_MARKER: SECRET_MARKER, "unexpected": [SECRET_MARKER]},
            "scalar": SECRET_MARKER,
            "array": [SECRET_MARKER, {"arbitrary": SECRET_MARKER}],
        }
        response = validation_client.post(
            "/api/auth/login", json=bodies[mode], headers={"Origin": ORIGIN}
        )
    assert_private_auth_error(response, caplog)
    if mode == "multiple":
        assert len(response.json()["detail"]) == 4


class FutureAuthItem(BaseModel):
    number: int


class FutureAuthBody(BaseModel):
    entries: list[FutureAuthItem]


@pytest.mark.parametrize("path", ["/api/auth", "/api/auth/future/nested"])
def test_auth_scope_keeps_only_declared_nested_locations(
    caplog: pytest.LogCaptureFixture, path: str,
) -> None:
    application = FastAPI()
    application.add_exception_handler(RequestValidationError, safe_request_validation_handler)

    @application.post(path)
    def future(payload: FutureAuthBody):
        return {"ok": True}

    caplog.set_level(logging.DEBUG)
    with TestClient(application) as client:
        response = client.post(path, json={"entries": [{"number": SECRET_MARKER}]})
    assert_private_auth_error(response, caplog)
    assert response.json()["detail"][0]["loc"] == ["body", "entries", 0, "number"]
    assert response.json()["detail"][0]["type"] == "int_parsing"


@pytest.mark.parametrize("path", ["/api/auth", "/api/auth/future"])
def test_auth_errors_do_not_inspect_body_context_or_untrusted_metadata(
    caplog: pytest.LogCaptureFixture, path: str,
) -> None:
    application = FastAPI()
    application.add_exception_handler(RequestValidationError, safe_request_validation_handler)

    class UnreadableBodyError(RequestValidationError):
        def __getattribute__(self, name):
            if name == "body":
                pytest.fail("Auth sanitization must not inspect the request body")
            return super().__getattribute__(name)

    class UnserializableSecret:
        def __str__(self):
            pytest.fail("Auth sanitization must not stringify input or context")

    @application.post(path)
    def errors(request: Request):
        async def forbidden_body_read():
            pytest.fail("Auth sanitization must not read the request body again")
        request.body = forbidden_body_read
        raise UnreadableBodyError([
            {"type": SECRET_MARKER, "loc": ("body", SECRET_MARKER), "msg": SECRET_MARKER,
             "input": UnserializableSecret(), "ctx": {"error": UnserializableSecret()},
             "unexpected_metadata": SECRET_MARKER},
            {"type": "extra_forbidden", "loc": ("body", "unknown", SECRET_MARKER),
             "msg": SECRET_MARKER, "input": SECRET_MARKER},
        ])

    caplog.set_level(logging.DEBUG)
    with TestClient(application) as client:
        response = client.post(path)
    assert_private_auth_error(response, caplog)
    assert response.json()["detail"][0] == {
        "type": "value_error", "loc": ["body"], "msg": "Invalid request.",
    }


class FutureAuthChoiceA(BaseModel):
    kind: Literal["a"]
    number: int


class FutureAuthChoiceB(BaseModel):
    kind: Literal["b"]
    number: int


class FutureAuthDynamicBody(BaseModel):
    values: dict[str, int]
    choice: Annotated[FutureAuthChoiceA | FutureAuthChoiceB, Field(discriminator="kind")]


@pytest.mark.parametrize(
    ("payload", "expected_type", "expected_location"),
    [
        (
            {"values": {SECRET_MARKER: SECRET_MARKER}, "choice": {"kind": "a", "number": 1}},
            "int_parsing",
            ["body"],
        ),
        (
            {"values": {}, "choice": {"kind": SECRET_MARKER, "number": SECRET_MARKER}},
            "union_tag_invalid",
            ["body", "choice"],
        ),
    ],
    ids=["dynamic-dictionary-key", "dynamic-union-discriminator"],
)
def test_auth_schema_rejects_dynamic_dictionary_keys_and_union_tags(
    caplog: pytest.LogCaptureFixture,
    payload: dict,
    expected_type: str,
    expected_location: list[str],
) -> None:
    application = FastAPI()
    application.add_exception_handler(RequestValidationError, safe_request_validation_handler)

    @application.post("/api/auth/future/dynamic")
    def future(payload: FutureAuthDynamicBody):
        return {"ok": True}

    caplog.set_level(logging.DEBUG)
    with TestClient(application) as client:
        response = client.post("/api/auth/future/dynamic", json=payload)
    assert_private_auth_error(response, caplog)
    assert response.json()["detail"] == [{
        "type": expected_type, "loc": expected_location, "msg": "Invalid request.",
    }]
