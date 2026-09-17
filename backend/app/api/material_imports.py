"""Administrator-only historical source inspection and explicit import planning."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased
from starlette.concurrency import run_in_threadpool

from app.auth.access import AccessDependency, ADMIN
from app.db.models import MaterialImportBatch
from app.import_requests import ConfirmImport, InspectImport, PlanImport, inspect_source, prepare_rows
from app.import_sources import ImportSourceError
from app.import_transport import ImportRequestReader, decode_request, source_failure
from app.material_imports import batch_result, batch_summary, build_preview, confirm_import


def build_material_imports_router(database):
    router = APIRouter(prefix="/api/material-imports", tags=["historical imports"])
    reader = ImportRequestReader()

    def authorize(access):
        with database.session() as session:
            access.check(session, ADMIN)

    def inspect(raw, access):
        result = None
        error = None
        try:
            result = inspect_source(decode_request(raw, InspectImport))
        except ImportSourceError as exc:
            error = source_failure(exc)
        except HTTPException as exc:
            error = exc
        # Disabling/demoting the actor during parsing must prevent disclosure,
        # including diagnostic details and samples from a rejected workbook.
        authorize(access)
        if error is not None:
            raise error
        return result

    def plan(raw, access, *, confirm=False):
        try:
            payload = decode_request(raw, ConfirmImport if confirm else PlanImport)
            table = payload.source.table()
            rows, findings = prepare_rows(table, payload.columns, payload.links)
        except (ImportSourceError, HTTPException) as exc:
            authorize(access)
            raise source_failure(exc) if isinstance(exc, ImportSourceError) else exc
        with database.session() as session:
            access.check(session, ADMIN, exclusive=True)
            if confirm:
                return confirm_import(session, access.user.id, payload, table, rows, findings)
            return build_preview(session, payload, table, rows, findings)

    @router.post("/inspect")
    async def inspection(request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access)
        async with reader.body(request) as raw:
            return await run_in_threadpool(inspect, raw, access)

    @router.post("/preview")
    async def planning(request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access)
        async with reader.body(request) as raw:
            return await run_in_threadpool(plan, raw, access)

    @router.post("/confirm")
    async def confirmation(request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access)
        async with reader.body(request) as raw:
            return await run_in_threadpool(plan, raw, access, confirm=True)

    @router.get("")
    def batches(access: AccessDependency, limit: Annotated[int, Query(ge=1, le=50)] = 20, after: UUID | None = None):
        with database.session() as session:
            access.check(session, ADMIN)
            query = select(MaterialImportBatch)
            if after is not None:
                previous = session.get(MaterialImportBatch, after)
                if previous is None:
                    raise HTTPException(404, {"code": "IMPORT_BATCH_NOT_FOUND"})
                anchor = aliased(MaterialImportBatch)
                # Compare native stored timestamps. SQLite's CURRENT_TIMESTAMP
                # text omits fractions while rebound datetime parameters do not.
                created = select(anchor.created_at).where(anchor.id == after).scalar_subquery()
                query = query.where(or_(MaterialImportBatch.created_at < created,
                    and_(MaterialImportBatch.created_at == created, MaterialImportBatch.id < previous.id)))
            page = session.scalars(query.order_by(MaterialImportBatch.created_at.desc(), MaterialImportBatch.id.desc()).limit(limit + 1)).all()
            return {"items": [batch_summary(batch) for batch in page[:limit]],
                    "next_after": str(page[limit - 1].id) if len(page) > limit else None}

    @router.get("/{batch_id}")
    def batch(batch_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, ADMIN)
            record = session.get(MaterialImportBatch, batch_id)
            if record is None:
                raise HTTPException(404, {"code": "IMPORT_BATCH_NOT_FOUND"})
            return batch_result(session, record)

    return router
