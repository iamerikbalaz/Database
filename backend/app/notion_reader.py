"""Opt-in, read-only company projection from an explicitly linked Notion page.

No startup IO, page discovery, database adoption or external mutation. See
docs/notion-reader-plan.md for the supported field contract and remaining gates.
"""
from contextvars import ContextVar
from datetime import datetime
import hashlib
import json
import logging
import math
import re
from threading import BoundedSemaphore
import time
from typing import Annotated, Awaitable, Callable, Literal
from uuid import UUID

import anyio
import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.schemas import CompanyUpdate

ORIGIN = "https://api.notion.com"
VERSION = "2026-03-11"
MAX_RESPONSE_BYTES = 2 * 1024**2
TYPES = {"name": "title", "legal_name": "rich_text", "country": "rich_text",
    "address": "rich_text", "vat_id": "rich_text", "website": "url"}
LIMITS = {"name": 255, "legal_name": 255, "country": 100, "address": 5000, "vat_id": 100, "website": 2048}
_private_io = ContextVar("notion_private_io", default=False)


class _PrivateIoFilter(logging.Filter):
    def filter(self, record): return not _private_io.get()


_filter = _PrivateIoFilter()
for _name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
    logging.getLogger(_name).addFilter(_filter)


class NotionError(RuntimeError):
    def __init__(self, code, retry_after=None):
        self.code = code if code in {"NOTION_DISABLED", "NOTION_CONFIGURATION_INVALID", "NOTION_SELECTION_INVALID",
            "NOTION_RESPONSE_INVALID", "NOTION_SCHEMA_CHANGED", "NOTION_PAGE_UNAVAILABLE", "NOTION_ACCESS_DENIED",
            "NOTION_RATE_LIMITED", "NOTION_UNAVAILABLE", "NOTION_BUSY", "NOTION_OPERATION_BLOCKED"} else "NOTION_UNAVAILABLE"
        self.retry_after = retry_after if type(retry_after) is int and 1 <= retry_after <= 999999999 else None
        super().__init__(self.code)


def _check(condition, code="NOTION_RESPONSE_INVALID"):
    if not condition: raise NotionError(code)


def page_id(value):
    """Notion uses several UUID versions; accept canonical or compact IDs, never URLs."""
    try:
        if not isinstance(value, str) or not re.fullmatch(r"(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})", value):
            raise ValueError()
        identifier = UUID(value)
        if identifier.int == 0: raise ValueError()
        return str(identifier)
    except (ValueError, TypeError): raise NotionError("NOTION_SELECTION_INVALID") from None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class Property(Strict):
    field: Literal["name", "legal_name", "country", "address", "vat_id", "website"]
    property_id: Annotated[str, Field(pattern=r"^[!-~]{1,128}$")]


class NotionConfiguration(Strict):
    enabled: bool = False
    data_source_id: str = ""
    properties: Annotated[tuple[Property, ...], Field(max_length=6)] = ()
    timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 20.0

    @model_validator(mode="after")
    def check(self):
        if self.enabled:
            _check(page_id(self.data_source_id) == self.data_source_id, "NOTION_CONFIGURATION_INVALID")
            fields = [item.field for item in self.properties]
            _check("name" in fields and len(set(fields)) == len(fields)
                and len({item.property_id for item in self.properties}) == len(fields), "NOTION_CONFIGURATION_INVALID")
        return self


class MappedValue(Strict):
    field: Literal["name", "legal_name", "country", "address", "vat_id", "website"]
    value: str | None


class CompanyObservation(Strict):
    page_id: str
    data_source_id: str
    database_id: str
    last_edited_time: str
    mapping_sha256: str
    values: tuple[MappedValue, ...] = Field(repr=False)
    observation_sha256: str

    @model_validator(mode="after")
    def verify(self):
        _check(all(page_id(value) == value for value in (self.page_id, self.data_source_id, self.database_id)))
        _check(re.fullmatch(r"[a-f0-9]{64}", self.mapping_sha256) is not None)
        _check(1 <= len(self.last_edited_time) <= 64 and "T" in self.last_edited_time
            and datetime.fromisoformat(self.last_edited_time).tzinfo is not None)
        fields = [item.field for item in self.values]
        _check(1 <= len(fields) <= 6 and fields == sorted(set(fields)) and "name" in fields)
        values = {item.field: item.value for item in self.values}
        _check(all(_safe_value(field, value) for field, value in values.items()))
        _check(CompanyUpdate.model_validate(values).model_dump(mode="json", exclude_unset=True) == values)
        _check(_hash(self.model_dump(mode="json", exclude={"observation_sha256"})) == self.observation_sha256)
        return self


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def mapping_hash(configuration):
    return _hash(sorted((item.field, item.property_id, TYPES[item.field]) for item in configuration.properties))


def _safe_value(field, value):
    return value is None or (len(value) <= LIMITS[field]
        and all(ord(char) >= 32 or char in "\n\r\t" for char in value) and "\x7f" not in value)


def configuration_from_settings(settings):
    if not settings.notion_enabled: return NotionConfiguration()
    return NotionConfiguration(enabled=True, data_source_id=settings.notion_company_data_source_id,
        properties=tuple(Property(field=field, property_id=identifier) for field, identifier in sorted(settings.notion_company_properties.items())),
        timeout_seconds=settings.notion_timeout_seconds)


def _properties(value):
    _check(type(value) is dict and 1 <= len(value) <= 500)
    result = {}
    for item in value.values():
        _check(type(item) is dict)
        identifier = item.get("id")
        _check(type(identifier) is str and re.fullmatch(r"[!-~]{1,128}", identifier) is not None and identifier not in result)
        result[identifier] = item
    return result


def _available(value, object_type, identifier):
    _check(type(value) is dict and value.get("object") == object_type and page_id(value.get("id")) == identifier)
    _check(value.get("in_trash") is False and value.get("archived", False) is False, "NOTION_PAGE_UNAVAILABLE")


def _schema(configuration, value):
    _available(value, "data_source", configuration.data_source_id)
    parent = value.get("parent")
    _check(type(parent) is dict and parent.get("type") == "database_id", "NOTION_SCHEMA_CHANGED")
    database_id = page_id(parent.get("database_id"))
    props = _properties(value.get("properties"))
    for mapping in configuration.properties:
        prop = props.get(mapping.property_id)
        _check(prop is not None and prop.get("type") == TYPES[mapping.field], "NOTION_SCHEMA_CHANGED")
    return database_id


def _value(mapping, prop):
    kind = TYPES[mapping.field]
    _check(type(prop) is dict and prop.get("id") == mapping.property_id and prop.get("type") == kind
        and prop.get("has_more", False) is False, "NOTION_SCHEMA_CHANGED")
    raw = prop.get(kind)
    if kind == "url":
        _check(raw is None or type(raw) is str)
        value = raw
    else:
        _check(type(raw) is list and len(raw) <= 100)
        parts = []
        for fragment in raw:
            # Mention/reference values can be incomplete on page retrieval.
            _check(type(fragment) is dict and fragment.get("type") == "text")
            content = fragment.get("text")
            _check(type(content) is dict and type(content.get("content")) is str and len(content["content"]) <= 2000)
            parts.append(content["content"])
        value = "".join(parts) or None
    _check(_safe_value(mapping.field, value))
    return value


def project(configuration, identifier, schema, page):
    """Pure projection; reject incomplete types and never return unmapped content."""
    try:
        configuration = NotionConfiguration.model_validate(configuration.model_dump(mode="python", warnings="error"))
        identifier = page_id(identifier)
        _check(configuration.enabled, "NOTION_DISABLED")
        database_id = _schema(configuration, schema)
        _available(page, "page", identifier)
        parent = page.get("parent")
        _check(type(parent) is dict and parent.get("type") == "data_source_id"
            and page_id(parent.get("data_source_id")) == configuration.data_source_id
            and page_id(parent.get("database_id")) == database_id, "NOTION_SCHEMA_CHANGED")
        edited = page.get("last_edited_time")
        _check(type(edited) is str and 1 <= len(edited) <= 64 and "T" in edited
            and datetime.fromisoformat(edited).tzinfo is not None)
        properties = _properties(page.get("properties"))
        values = {mapping.field: _value(mapping, properties.get(mapping.property_id)) for mapping in configuration.properties}
        # Apply the existing company's text/URL constraints, without defaults for unmapped fields.
        values = CompanyUpdate.model_validate(values).model_dump(mode="json", exclude_unset=True)
        mapped = tuple(MappedValue(field=field, value=value) for field, value in sorted(values.items()))
        binding = dict(page_id=identifier, data_source_id=configuration.data_source_id, database_id=database_id,
            last_edited_time=edited, mapping_sha256=mapping_hash(configuration),
            values=[item.model_dump(mode="json") for item in mapped])
        return CompanyObservation(**(binding | {"values": mapped, "observation_sha256": _hash(binding)}))
    except NotionError: raise
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError, RecursionError):
        raise NotionError("NOTION_RESPONSE_INVALID") from None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value: raise ValueError()
        value[key] = item
    return value


def _constant(_): raise ValueError()


async def _json(response):
    _check(response.headers.get("Content-Encoding", "identity").lower() == "identity")
    _check(response.headers.get("Content-Type", "").split(";", 1)[0].lower() == "application/json")
    length = response.headers.get("Content-Length")
    _check(length is None or (re.fullmatch(r"[0-9]{1,8}", length) is not None and int(length) <= MAX_RESPONSE_BYTES))
    raw = bytearray()
    async for block in response.aiter_raw(chunk_size=4096):
        _check(len(raw) + len(block) <= MAX_RESPONSE_BYTES); raw.extend(block)
    try:
        result = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
        _check(type(result) is dict)
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError): raise NotionError("NOTION_RESPONSE_INVALID") from None


class NotionReader:
    def __init__(self, configuration, token: SecretStr | None = None, *, transport=None):
        self.configuration = configuration; self._token = token; self._transport = transport
        self._slot = BoundedSemaphore(1); self._retry_at = 0.0

    @classmethod
    def from_settings(cls, settings):
        return cls(configuration_from_settings(settings), settings.notion_access_token)

    async def company(self, identifier, *, operation_guard: Callable[[], Awaitable[None]] | None = None):
        try: configuration = NotionConfiguration.model_validate(self.configuration.model_dump(mode="python", warnings="error"))
        except Exception: raise NotionError("NOTION_CONFIGURATION_INVALID") from None
        if not configuration.enabled: raise NotionError("NOTION_DISABLED")
        identifier = page_id(identifier)
        token = self._token.get_secret_value() if isinstance(self._token, SecretStr) else ""
        _check(32 <= len(token) <= 8192 and token.isascii() and all(33 <= ord(c) <= 126 for c in token), "NOTION_CONFIGURATION_INVALID")
        if not self._slot.acquire(blocking=False): raise NotionError("NOTION_BUSY")
        private = _private_io.set(True); client = None
        async def guard():
            if operation_guard is not None:
                try: _check(await operation_guard() is None, "NOTION_OPERATION_BLOCKED")
                except Exception: raise NotionError("NOTION_OPERATION_BLOCKED") from None
        try:
            remaining = self._retry_at - time.monotonic()
            if remaining > 0: raise NotionError("NOTION_RATE_LIMITED", math.ceil(remaining))
            with anyio.fail_after(configuration.timeout_seconds):
                async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(configuration.timeout_seconds), limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
                    async def get(path):
                        await guard()
                        # Rebuild explicit headers and clear cookies before each request.
                        client.cookies.clear()
                        response = None
                        try:
                            request = client.build_request("GET", ORIGIN + path, headers={"Authorization": "Bearer " + token,
                                "Notion-Version": VERSION, "Accept": "application/json", "Accept-Encoding": "identity"})
                            response = await client.send(request, stream=True)
                            if response.status_code in {429, 529}:
                                raw = response.headers.get("Retry-After", "")
                                delay = max(1, int(raw)) if re.fullmatch(r"[0-9]{1,9}", raw) else 60
                                self._retry_at = time.monotonic() + delay
                                raise NotionError("NOTION_RATE_LIMITED", delay)
                            if response.status_code in {401, 403}: raise NotionError("NOTION_ACCESS_DENIED")
                            if response.status_code == 404: raise NotionError("NOTION_PAGE_UNAVAILABLE")
                            if response.status_code != 200: raise NotionError("NOTION_UNAVAILABLE")
                            result = await _json(response)
                            await guard()
                            return result
                        finally:
                            if response is not None:
                                with anyio.CancelScope(shield=True): await response.aclose()
                    schema = await get("/v1/data_sources/" + configuration.data_source_id)
                    _schema(configuration, schema)
                    page = await get("/v1/pages/" + identifier)
                    result = project(configuration, identifier, schema, page)
                    await guard()
                    return result
        except NotionError: raise
        except Exception: raise NotionError("NOTION_UNAVAILABLE") from None
        finally:
            try:
                if client is not None:
                    with anyio.CancelScope(shield=True): await client.aclose()
            finally:
                _private_io.reset(private); self._slot.release()
