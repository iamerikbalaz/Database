"""Authenticated and byte-bounded JSON brief/export/review/adoption endpoints."""
import asyncio
from contextlib import asynccontextmanager
import json
from threading import BoundedSemaphore
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from app.ai_brief import BriefApply, BriefReview, BriefSelection, apply_result, export_brief, review_results
from app.auth.access import CATALOG_MANAGERS, AccessDependency
from app.import_transport import _reject_constant, _unique_object

MAX_REQUEST_BYTES = 5 * 1024**2


class BriefRequestReader:
    def __init__(self):
        self.slots = BoundedSemaphore(2)

    @asynccontextmanager
    async def body(self, request):
        if not self.slots.acquire(blocking=False): raise HTTPException(503, {'code': 'AI_BRIEF_BUSY'})
        try:
            if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise HTTPException(415, {'code': 'AI_BRIEF_JSON_REQUIRED'})
            if request.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise HTTPException(415, {'code': 'AI_BRIEF_ENCODING_UNSUPPORTED'})
            declared = request.headers.get('content-length')
            if declared is not None and (not declared.isascii() or not declared.isdigit() or len(declared) > 10
                    or not 0 < int(declared) <= MAX_REQUEST_BYTES):
                raise HTTPException(413, {'code': 'AI_BRIEF_REQUEST_TOO_LARGE'})
            data = bytearray()
            try:
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > MAX_REQUEST_BYTES:
                            raise HTTPException(413, {'code': 'AI_BRIEF_REQUEST_TOO_LARGE'})
                        data.extend(chunk)
            except TimeoutError:
                raise HTTPException(408, {'code': 'AI_BRIEF_UPLOAD_TIMEOUT'}) from None
            except ClientDisconnect:
                raise HTTPException(400, {'code': 'AI_BRIEF_UPLOAD_DISCONNECTED'}) from None
            if not data or (declared is not None and int(declared) != len(data)):
                raise HTTPException(422, {'code': 'AI_BRIEF_INVALID_JSON'})
            yield bytes(data)
        finally:
            self.slots.release()


def _decode(raw, model):
    try:
        json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        return model.model_validate_json(raw)
    except (ValueError, UnicodeError, RecursionError, ArithmeticError):
        # User-controlled property names, descriptions and source URLs stay out
        # of validation errors, exception logs and reflected response bodies.
        raise HTTPException(422, {'code': 'AI_BRIEF_INVALID_JSON'}) from None


def build_ai_brief_router(database):
    router = APIRouter(tags=['AI JSON briefs'])
    reader = BriefRequestReader()

    def authorize(access, roles):
        with database.session() as session: access.check(session, roles)

    def perform(raw, model, operation, access, roles, material_id=None):
        try: payload = _decode(raw, model)
        except HTTPException:
            authorize(access, roles)
            raise
        return operation(database, payload, access) if material_id is None else operation(database, material_id, payload, access)

    @router.post('/api/material-ai/brief')
    async def brief(request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access, CATALOG_MANAGERS)
        async with reader.body(request) as raw:
            return await run_in_threadpool(perform, raw, BriefSelection, export_brief, access, CATALOG_MANAGERS)

    @router.post('/api/material-ai/review')
    async def review(request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access, CATALOG_MANAGERS)
        async with reader.body(request) as raw:
            return await run_in_threadpool(perform, raw, BriefReview, review_results, access, CATALOG_MANAGERS)

    @router.post('/api/materials/{material_id}/ai-brief-result')
    async def apply(material_id: UUID, request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access, CATALOG_MANAGERS)
        async with reader.body(request) as raw:
            return await run_in_threadpool(perform, raw, BriefApply, apply_result, access, CATALOG_MANAGERS, material_id)

    return router
