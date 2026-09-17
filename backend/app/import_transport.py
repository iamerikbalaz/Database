"""Authenticate first, then accept a bounded JSON envelope without body logging."""
import asyncio
from contextlib import asynccontextmanager
import json
from threading import BoundedSemaphore

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from app.import_sources import ImportSourceError

MAX_REQUEST_BYTES = 6 * 1024**2
UPLOAD_TIMEOUT_SECONDS = 10


def failure(code, status=422):
    return HTTPException(status, {"code": code})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("Non-finite JSON value")


def decode_request(raw, model):
    try:
        # Pydantic deliberately accepts UUID strings in strict JSON mode. The
        # independent syntax pass additionally rejects duplicate properties and
        # nonstandard NaN/Infinity, without echoing user-controlled locations.
        json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        return model.model_validate_json(raw)
    except (ValueError, ValidationError, UnicodeError, RecursionError):
        raise failure("IMPORT_REQUEST_INVALID") from None


def source_failure(exc: ImportSourceError):
    return HTTPException(422, {"code": exc.code, "row": exc.row, "column": exc.column})


class ImportRequestReader:
    def __init__(self):
        # Per API process. Hold the slot through parsing, so slow uploads and
        # expensive archives cannot accumulate an unbounded worker queue.
        self.slots = BoundedSemaphore(2)

    @asynccontextmanager
    async def body(self, request):
        if not self.slots.acquire(blocking=False):
            raise failure("IMPORT_BUSY", 503)
        try:
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise failure("IMPORT_JSON_REQUIRED", 415)
            if request.headers.get("content-encoding", "identity").lower() != "identity":
                raise failure("IMPORT_CONTENT_ENCODING", 415)
            declared = request.headers.get("content-length")
            if declared is not None:
                if not declared.isascii() or not declared.isdigit() or len(declared) > 10:
                    raise failure("IMPORT_REQUEST_SIZE", 413)
                if not 0 < int(declared) <= MAX_REQUEST_BYTES:
                    raise failure("IMPORT_REQUEST_SIZE", 413)
            data = bytearray()
            try:
                async with asyncio.timeout(UPLOAD_TIMEOUT_SECONDS):
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
                            raise failure("IMPORT_REQUEST_SIZE", 413)
                        data.extend(chunk)
            except TimeoutError:
                raise failure("IMPORT_UPLOAD_TIMEOUT", 408) from None
            except ClientDisconnect:
                raise failure("IMPORT_UPLOAD_DISCONNECTED", 400) from None
            if not data or (declared is not None and len(data) != int(declared)):
                raise failure("IMPORT_REQUEST_SIZE", 413)
            yield bytes(data)
        finally:
            self.slots.release()
