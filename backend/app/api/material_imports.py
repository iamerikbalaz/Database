"""Administrator-only historical source inspection and explicit import planning."""
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.auth.access import AccessDependency, ADMIN
from app.import_requests import InspectImport, inspect_source
from app.import_sources import ImportSourceError
from app.import_transport import ImportRequestReader, decode_request, source_failure


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

    @router.post("/inspect")
    async def inspection(request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access)
        async with reader.body(request) as raw:
            return await run_in_threadpool(inspect, raw, access)

    return router
