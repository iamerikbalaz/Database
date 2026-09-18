"""Create-only bounded GCS JSON API transport with independent full readback.

No production route invokes this adapter yet. Callers must establish approval and
durable ownership before calling upload; object receipts alone never publish data.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import re
import time
from threading import BoundedSemaphore
from typing import AsyncIterator, Awaitable, Callable
from urllib.parse import parse_qsl, quote, urlsplit

import anyio
import httpx
from pydantic import SecretStr

from app.gcs_contract import GcsConfiguration, GcsError, GcsObjectReceipt, GcsObjectSpec

ORIGIN = "https://storage.googleapis.com"
UPLOAD_CHUNK_BYTES = 8 * 1024**2
SOURCE_BLOCK_BYTES = 64 * 1024
JSON_BYTES = 32 * 1024
RECHECK_BYTES = 8 * 1024**2
RECHECK_SECONDS = 1
_private_io = ContextVar("gcs_private_io", default=False)


class _PrivateIoFilter(logging.Filter):
    def filter(self, record):
        # HTTPX logs URLs; HTTPcore debug traces may include response headers.
        # A resumable-session Location is a bearer capability. Keep it in memory
        # and suppress transport diagnostics only in this operation's context.
        return not _private_io.get()


_log_filter = _PrivateIoFilter()
for _name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
              "httpcore.proxy", "httpcore.socks"):
    logging.getLogger(_name).addFilter(_log_filter)


def _check(condition):
    if not condition:
        raise GcsError("GCS_VERIFICATION_FAILED")


def _selection(value):
    try:
        return GcsObjectSpec.model_validate(value.model_dump(mode="python", warnings="error"))
    except (ValueError, TypeError, AttributeError, ArithmeticError, RecursionError):
        raise GcsError("GCS_SELECTION_INVALID") from None


def _session_uri(value, configuration, name):
    """Validate before using a capability; never permit arbitrary target URLs."""
    try:
        _check(isinstance(value, str) and 1 <= len(value) <= 8192 and value.isascii()
               and not any(c.isspace() or ord(c) < 33 or ord(c) == 127 or c == "\\" for c in value))
        parsed = urlsplit(value)
        _check(parsed.scheme == "https" and parsed.netloc == "storage.googleapis.com"
               and not parsed.fragment and parsed.path == f"/upload/storage/v1/b/{configuration.bucket_name}/o")
        entries = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
        query = dict(entries)
        _check(len(entries) == len(query) and set(query) <= {"uploadType", "upload_id", "name", "ifGenerationMatch"}
               and query.get("uploadType") == "resumable"
               and re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", query.get("upload_id", "")) is not None
               and ("name" not in query or query["name"] == name)
               and ("ifGenerationMatch" not in query or query["ifGenerationMatch"] == "0"))
        return SecretStr(value)
    except (ValueError, TypeError):
        raise GcsError("GCS_VERIFICATION_FAILED") from None


def _headers(response):
    _check(response.headers.get("Content-Encoding", "identity").lower() == "identity")


def _status(response, accepted):
    if response.status_code in accepted:
        return
    # Do not parse or log Google's error payload or request/response objects.
    code = {401: "GCS_ACCESS_DENIED", 403: "GCS_ACCESS_DENIED",
            404: "GCS_OBJECT_ABSENT", 412: "GCS_OBJECT_CONFLICT"}.get(response.status_code)
    raise GcsError(code or "GCS_OUTCOME_UNCERTAIN")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError()


async def _json(response):
    _headers(response)
    _check(response.headers.get("Content-Type", "").split(";", 1)[0].lower() == "application/json")
    raw = bytearray()
    async for block in response.aiter_raw(chunk_size=4096):
        _check(len(raw) + len(block) <= JSON_BYTES)
        raw.extend(block)
    try:
        result = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
        _check(isinstance(result, dict))
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise GcsError("GCS_VERIFICATION_FAILED") from None


async def _source_blocks(source):
    try:
        async for block in source:
            yield block
    except Exception:
        raise GcsError("GCS_SOURCE_UNAVAILABLE") from None


class _OperationGuard:
    """Per-call authorization/lease gate supplied by the durable coordinator."""
    def __init__(self, callback):
        if callback is not None and not callable(callback):
            raise GcsError("GCS_OPERATION_BLOCKED")
        self.callback = callback
        self.bytes = 0
        self.checked_at = time.monotonic()

    async def require(self):
        if self.callback is not None:
            try:
                # Success must be explicit normal completion. False, coroutine
                # objects returned by a broken callback, and other values fail.
                if await self.callback() is not None:
                    raise ValueError()
            except Exception:
                raise GcsError("GCS_OPERATION_BLOCKED") from None
        self.bytes = 0
        self.checked_at = time.monotonic()

    async def progress(self, count):
        self.bytes += count
        if self.bytes >= RECHECK_BYTES or time.monotonic() - self.checked_at >= RECHECK_SECONDS:
            await self.require()


class GcsClient:
    def __init__(self, configuration: GcsConfiguration,
                 token_provider: Callable[[], Awaitable[SecretStr]]):
        try:
            self.configuration = GcsConfiguration.model_validate(
                configuration.model_dump(mode="python", warnings="error"))
        except (ValueError, TypeError, AttributeError):
            raise GcsError("GCS_CONFIGURATION_INVALID") from None
        self._token_provider = token_provider
        self._slot = BoundedSemaphore(1)

    @classmethod
    def from_settings(cls, settings):
        configuration = GcsConfiguration(enabled=settings.gcs_enabled,
            bucket_name=settings.gcs_bucket_name, staging_prefix=settings.gcs_staging_prefix,
            timeout_seconds=settings.gcs_timeout_seconds)

        async def token():
            # An explicitly supplied short-lived OAuth token. No key file reads,
            # subprocess credential discovery or ambient account fallbacks.
            return settings.gcs_access_token

        return cls(configuration, token)

    @asynccontextmanager
    async def _connection(self, guard):
        if not self.configuration.enabled:
            raise GcsError("GCS_DISABLED")
        if not self._slot.acquire(blocking=False):
            raise GcsError("GCS_BUSY")
        context = _private_io.set(True)
        client = None
        try:
            with anyio.fail_after(self.configuration.timeout_seconds):
                await guard.require()
                try:
                    token = await self._token_provider()
                    raw = token.get_secret_value()
                    if not 32 <= len(raw) <= 8192 or not raw.isascii() or any(not 33 <= ord(c) <= 126 for c in raw):
                        raise ValueError()
                except Exception:
                    raise GcsError("GCS_CREDENTIAL_UNAVAILABLE") from None
                await guard.require()
                client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5),
                    trust_env=False, follow_redirects=False, http2=False,
                    limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                    headers={"Authorization": "Bearer " + raw, "Accept-Encoding": "identity"})
                yield client
        except GcsError:
            raise
        except (httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError, RecursionError):
            raise GcsError("GCS_OUTCOME_UNCERTAIN") from None
        finally:
            try:
                if client is not None:
                    with anyio.CancelScope(shield=True):
                        await client.aclose()
            finally:
                _private_io.reset(context)
                self._slot.release()

    @asynccontextmanager
    async def _response(self, client, method, url, *, guard, **kwargs):
        await guard.require()
        response = await client.send(client.build_request(method, url, **kwargs), stream=True)
        try:
            yield response
        finally:
            with anyio.CancelScope(shield=True):
                await response.aclose()

    def _receipt(self, data, spec):
        name = spec.object_name(self.configuration)
        _check(data.get("kind") == "storage#object" and data.get("bucket") == self.configuration.bucket_name
               and data.get("name") == name and data.get("size") == str(spec.size)
               and data.get("metadata") == spec.metadata()
               and data.get("contentType") == "application/octet-stream"
               and data.get("contentEncoding", "identity") == "identity"
               and data.get("cacheControl") == "no-store")
        try:
            return GcsObjectReceipt(spec=spec, bucket_name=self.configuration.bucket_name,
                object_name=name, generation=data.get("generation"), metageneration=data.get("metageneration"))
        except ValueError:
            raise GcsError("GCS_VERIFICATION_FAILED") from None

    def _object_url(self, spec):
        return f"{ORIGIN}/storage/v1/b/{self.configuration.bucket_name}/o/{quote(spec.object_name(self.configuration), safe='')}"

    async def _metadata(self, client, spec, *, guard, conditions=None):
        async with self._response(client, "GET", self._object_url(spec),
                                  guard=guard,
                                  params={"projection": "noAcl", **(conditions or {})}) as response:
            _status(response, {200})
            return self._receipt(await _json(response), spec)

    async def _readback(self, client, spec, receipt, guard):
        conditions = {"ifGenerationMatch": receipt.generation,
                      "ifMetagenerationMatch": receipt.metageneration}
        digest = hashlib.sha256()
        size = 0
        async with self._response(client, "GET", self._object_url(spec), guard=guard, params={
                "alt": "media", "generation": receipt.generation, **conditions}) as response:
            _status(response, {200})
            _headers(response)
            _check(response.headers.get("Content-Length") == str(spec.size))
            async for block in response.aiter_raw(chunk_size=SOURCE_BLOCK_BYTES):
                size += len(block)
                _check(size <= spec.size)
                digest.update(block)
                await guard.progress(len(block))
        _check(size == spec.size and digest.hexdigest() == spec.sha256)
        # No generation selector here: the *live* object must still be the
        # verified generation with the same metadata at completion.
        latest = await self._metadata(client, spec, guard=guard, conditions=conditions)
        _check(latest == receipt)
        await guard.require()
        return receipt

    async def reconcile(self, selection: GcsObjectSpec, *,
                        operation_guard: Callable[[], Awaitable[None]] | None = None) -> GcsObjectReceipt:
        """Read-only verification after an unknown outcome; never resumes writes."""
        spec = _selection(selection)
        guard = _OperationGuard(operation_guard)
        async with self._connection(guard) as client:
            receipt = await self._metadata(client, spec, guard=guard)
            return await self._readback(client, spec, receipt, guard)

    async def upload(self, selection: GcsObjectSpec, source: AsyncIterator[bytes], *,
                     operation_guard: Callable[[], Awaitable[None]] | None = None) -> GcsObjectReceipt:
        spec = _selection(selection)
        guard = _OperationGuard(operation_guard)
        async with self._connection(guard) as client:
            name = spec.object_name(self.configuration)
            async with self._response(client, "POST",
                    f"{ORIGIN}/upload/storage/v1/b/{self.configuration.bucket_name}/o",
                    guard=guard,
                    params={"uploadType": "resumable", "ifGenerationMatch": "0"},
                    headers={"X-Upload-Content-Length": str(spec.size),
                             "X-Upload-Content-Type": "application/octet-stream"},
                    json={"name": name, "contentType": "application/octet-stream",
                          "cacheControl": "no-store", "metadata": spec.metadata()}) as response:
                _status(response, {200})
                _headers(response)
                session = _session_uri(response.headers.get("Location"), self.configuration, name)

            size = 0
            sent = 0
            digest = hashlib.sha256()
            buffer = bytearray()
            async for block in _source_blocks(source):
                if type(block) is not bytes or not 1 <= len(block) <= SOURCE_BLOCK_BYTES:
                    raise GcsError("GCS_SOURCE_CHANGED")
                size += len(block)
                if size > spec.size:
                    raise GcsError("GCS_SOURCE_CHANGED")
                digest.update(block)
                buffer.extend(block)
                await guard.progress(len(block))
                # Keep the final block until source EOF and digest validation.
                if len(buffer) >= UPLOAD_CHUNK_BYTES and sent + UPLOAD_CHUNK_BYTES < spec.size:
                    chunk = bytes(buffer[:UPLOAD_CHUNK_BYTES])
                    del buffer[:UPLOAD_CHUNK_BYTES]
                    await self._chunk(client, session, chunk, sent, spec, guard=guard, final=False)
                    sent += len(chunk)
            if size != spec.size or digest.hexdigest() != spec.sha256:
                raise GcsError("GCS_SOURCE_CHANGED")
            receipt = await self._chunk(client, session, bytes(buffer), sent, spec, guard=guard, final=True)
            return await self._readback(client, spec, receipt, guard)

    async def _chunk(self, client, session, data, start, spec, *, guard, final):
        end = start + len(data) - 1
        async with self._response(client, "PUT", session.get_secret_value(), content=data, guard=guard,
                headers={"Content-Type": "application/octet-stream",
                         "Content-Range": f"bytes {start}-{end}/{spec.size}"}) as response:
            _status(response, {200, 201} if final else {308})
            _headers(response)
            if final:
                return self._receipt(await _json(response), spec)
            # Partial acknowledgements are not guessed/replayed. An explicit
            # recovery must inspect the object; an abandoned session expires.
            _check(response.headers.get("Range") == f"bytes=0-{end}")
