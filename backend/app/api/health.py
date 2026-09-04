from typing import Literal, Protocol

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app import __version__


class HealthDatabase(Protocol):
    def ping(self) -> bool: ...


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["connected", "unavailable"]
    service: str = "backend"
    version: str = __version__


def build_health_router(database: HealthDatabase) -> APIRouter:
    router = APIRouter(tags=["system"])

    @router.get("/health", response_model=HealthResponse)
    def health(response: Response) -> HealthResponse:
        if database.ping():
            return HealthResponse(status="ok", database="connected")

        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="unavailable")

    return router
