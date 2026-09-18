"""Safe request-validation responses, including nested and parent-body errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, get_args

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter
from pydantic_core import ErrorType

from app.auth.security import MAX_RAW_PASSWORD_LENGTH, normalize_password


REDACTED = "[redacted]"
AUTH_ERROR_MESSAGE = "Invalid request."
_ERROR_TYPES = frozenset(get_args(ErrorType))
_LOCATION_SOURCES = frozenset({"body", "query", "path", "header", "cookie"})


def is_auth_api_path(path: str) -> bool:
    return path == "/api/auth" or path.startswith("/api/auth/")


def _schema_location_is_static(schema: dict, parts: tuple, definitions: dict) -> bool:
    """Only schema property names and structural array indexes are safe to echo.

    Extra fields, dictionary keys and union-discriminator values are supplied by
    clients; they must never become response metadata, even on future auth routes.
    """
    seen_refs: set[str] = set()
    while "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/$defs/") or reference in seen_refs:
            return False
        seen_refs.add(reference)
        schema = definitions.get(reference.removeprefix("#/$defs/"), {})
    if not parts:
        return True
    if "anyOf" in schema:
        return any(
            _schema_location_is_static(branch, parts, definitions) for branch in schema["anyOf"]
        )
    part, *remaining = parts
    if type(part) is str and part in schema.get("properties", {}):
        child = schema["properties"][part]
    elif type(part) is int and part >= 0 and schema.get("type") == "array":
        child = schema.get("items", {})
    else:
        return False
    return _schema_location_is_static(child, tuple(remaining), definitions)


def _safe_auth_location(request: Request, location: object) -> list[str | int]:
    if not isinstance(location, (tuple, list)) or not location:
        return ["body"]
    source = location[0]
    if type(source) is not str or source not in _LOCATION_SOURCES:
        return ["body"]
    fallback = [source]
    route = request.scope.get("route")
    if source == "body":
        field = getattr(route, "body_field", None)
        remaining = tuple(location[1:])
    else:
        fields = getattr(getattr(route, "dependant", None), source + "_params", ())
        field = next((item for item in fields if len(location) > 1
                      and type(location[1]) is str and item.alias == location[1]), None)
        remaining = tuple(location[2:])
    if field is None:
        return fallback
    try:
        schema = TypeAdapter(field.field_info.annotation).json_schema()
        if _schema_location_is_static(schema, remaining, schema.get("$defs", {})):
            return list(location)
    except Exception:
        # An unsupported schema cannot justify reflecting arbitrary metadata.
        # Never log the original exception or fall back to its input/ctx/body.
        pass
    return fallback


def _auth_validation_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for error in exc.errors():
        error_type = error.get("type")
        errors.append({
            "type": error_type if type(error_type) is str and error_type in _ERROR_TYPES
            else "value_error",
            "loc": _safe_auth_location(request, error.get("loc")),
            "msg": AUTH_ERROR_MESSAGE,
        })
    # Construct an allowlisted response. No input, ctx, body, custom message or
    # arbitrary extra metadata is inspected, serialized or logged on auth paths.
    return JSONResponse(
        status_code=422, content={"detail": errors}, headers={"Cache-Control": "no-store"}
    )


# The existing non-auth contract remains separate from the fail-closed auth path.
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
    path = request.url.path
    if (is_auth_api_path(path) or path.startswith(("/api/online-categories", "/api/collections", "/api/material-imports", "/api/material-archives", "/api/publication-batches"))
            or path.startswith("/api/ai/")
            or (path.startswith("/api/materials/") and ("/identity-" in path or "/content" in path or "/preview" in path or "/folder-discovery" in path or "/publishing-context" in path or "/ai-service-credentials" in path))):
        return _auth_validation_response(request, exc)
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
        # Malformed JSON may put its entire unparsed body in input, before field
        # names can be traversed. Never reflect any of it or decoder context.
        error = {
            key: value
            for key, value in original.items()
            if not ((sensitive or malformed_json) and key in {"input", "ctx"})
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
