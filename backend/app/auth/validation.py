"""Safe request-validation responses, including nested and parent-body errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth.security import MAX_RAW_PASSWORD_LENGTH, normalize_password


REDACTED = "[redacted]"
SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "confirmation",
        "password_hash",
        "session_token",
        "csrf_token",
        "x_csrf_token",
        "authorization",
        "cookie",
    }
)


def _sensitive_name(value: object) -> bool:
    return isinstance(value, str) and value.lower().replace("-", "_") in SENSITIVE_FIELDS


def _collect_values(value: Any, protected: set[str], *, sensitive: bool = False) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if sensitive:
                _collect_values(key, protected, sensitive=True)
            _collect_values(item, protected, sensitive=sensitive or _sensitive_name(key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_values(item, protected, sensitive=sensitive)
    elif isinstance(value, BaseException):
        # Inspect structured attributes without calling str/repr on the original
        # exception. Custom validators can put reflected secrets in ctx.error.
        _collect_values(vars(value), protected, sensitive=sensitive)
        _collect_values(value.args, protected, sensitive=sensitive)
    elif sensitive and value is not None:
        rendered = str(value)
        if rendered:
            protected.add(rendered)
            if isinstance(value, str) and len(value) <= MAX_RAW_PASSWORD_LENGTH:
                protected.add(normalize_password(value))


def _contains_secret(value: str, protected: set[str]) -> bool:
    lowered = value.lower()
    return (
        "$argon2" in lowered
        or any(name in lowered.replace("-", "_") for name in SENSITIVE_FIELDS)
        or any(secret and secret in value for secret in protected)
    )


def _sanitize(value: Any, protected: set[str]) -> Any:
    if isinstance(value, BaseException):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            REDACTED if _contains_secret(str(key), protected) else key: (
                REDACTED if _sensitive_name(key) else _sanitize(item, protected)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, protected) for item in value]
    if isinstance(value, str):
        return REDACTED if _contains_secret(value, protected) else value
    if value is not None and str(value) in protected:
        return REDACTED
    return value


async def safe_request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Do not log/serialize the exception itself: errors(), body, ctx, and str(exc)
    # can all contain the original secret. Collect before removing parent inputs
    # so copies reflected in a different invalid field are protected as well.
    protected: set[str] = set()
    _collect_values(exc.body, protected)
    original_errors = exc.errors()
    malformed_json = any(error.get("type") == "json_invalid" for error in original_errors)
    for error in original_errors:
        sensitive = any(_sensitive_name(part) for part in error.get("loc", ()))
        _collect_values(
            error.get("input"), protected,
            sensitive=sensitive or error.get("type") == "json_invalid",
        )
        _collect_values(error.get("ctx"), protected, sensitive=sensitive)
    for token in request.cookies.values():
        if token:
            protected.add(token)
    for name in ("authorization", "x-csrf-token"):
        token = request.headers.get(name)
        if token:
            protected.add(token)

    errors = []
    for original in original_errors:
        location = original.get("loc", ())
        sensitive = any(_sensitive_name(part) for part in location)
        # A malformed auth body may itself be a bare secret (or a list of them),
        # with no field names available for classification.
        auth_body_error = request.url.path.startswith("/api/auth/") and tuple(location) == (
            "body",
        )
        # Malformed JSON may put its entire unparsed body in input, before field
        # names can be traversed. Never reflect any of it or decoder context.
        error = {
            key: value
            for key, value in original.items()
            if not ((sensitive or malformed_json or auth_body_error) and key in {"input", "ctx"})
        }
        if sensitive:
            error["msg"] = "Invalid secret input."
            first_sensitive = next(
                index for index, part in enumerate(location) if _sensitive_name(part)
            )
            # Suffixes can be user-controlled dictionary keys inside an invalid
            # secret value. Do not expose them even when exc.body is unavailable.
            error["loc"] = [*location[:first_sensitive], REDACTED]
        elif malformed_json:
            error["msg"] = "Invalid JSON input."
        errors.append(jsonable_encoder(_sanitize(error, protected)))

    return JSONResponse(status_code=422, content={"detail": errors})
