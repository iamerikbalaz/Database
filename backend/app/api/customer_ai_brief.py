"""Session-authenticated, byte-bounded Customer AI JSON handoff endpoints."""
from uuid import UUID

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.ai_brief import BriefSelection
from app.api.ai_brief import BriefRequestReader, _decode
from app.auth.access import AccessDependency, CATALOG_MANAGERS
from app.customer_ai_brief import CustomerApply, CustomerReview, apply_customer_result, export_customer_brief, review_customer_results


def build_customer_ai_brief_router(database):
    router = APIRouter(tags=['Customer AI JSON briefs'])
    reader = BriefRequestReader()

    def authorize(access):
        with database.session() as session: access.check(session, CATALOG_MANAGERS)

    def execute(raw, model, operation, access, customer_id=None):
        payload = _decode(raw, model)
        return operation(database, payload, access) if customer_id is None else operation(database, customer_id, payload, access)

    @router.post('/api/customer-ai/brief')
    async def brief(request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access)
        async with reader.body(request) as raw:
            return await run_in_threadpool(execute, raw, BriefSelection, export_customer_brief, access)

    @router.post('/api/customer-ai/review')
    async def review(request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access)
        async with reader.body(request) as raw:
            return await run_in_threadpool(execute, raw, CustomerReview, review_customer_results, access)

    @router.post('/api/customers/{customer_id}/ai-brief-result')
    async def apply(customer_id: UUID, request: Request, access: AccessDependency):
        await run_in_threadpool(authorize, access)
        async with reader.body(request) as raw:
            return await run_in_threadpool(execute, raw, CustomerApply, apply_customer_result, access, customer_id)

    return router
