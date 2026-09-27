"""Authenticated, versioned, exactly replayable global packaging settings."""
from datetime import date
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException
from pydantic import Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.access import ADMIN, AccessDependency
from app.db.models import PackagingSettingsRevision
from app.material_review import canonical_hash
from app.packaging_settings import current_settings, lock_settings
from app.schemas import ApiSchema


class PackagingSettingsUpdate(ApiSchema):
    idempotency_key: UUID
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    cutoff_date: date
    storage_timezone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

    @field_validator("cutoff_date")
    @classmethod
    def valid_date(cls, value):
        if not date(1980, 1, 1) <= value <= date(2100, 12, 31):
            raise ValueError("Cutoff date must be between 1980 and 2100")
        return value

    @field_validator("storage_timezone")
    @classmethod
    def valid_timezone(cls, value):
        try: ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError): raise ValueError("Unknown timezone") from None
        return value


def build_packaging_settings_router(database, runtime):
    router = APIRouter(prefix="/api/settings/packaging", tags=["settings"])

    @router.get("")
    def current(access: AccessDependency):
        with database.session() as session:
            access.check(session)
            return current_settings(session, runtime)

    @router.post("")
    def update(payload: PackagingSettingsUpdate, access: AccessDependency):
        digest = canonical_hash(payload.model_dump(mode="json"))
        with database.session() as session:
            actor = access.check(session, ADMIN)
            lock_settings(session, exclusive=True)
            actor = access.check(session, ADMIN)
            prior = session.scalar(select(PackagingSettingsRevision).where(
                PackagingSettingsRevision.actor_id == actor.id, PackagingSettingsRevision.request_key == payload.idempotency_key))
            if prior:
                if prior.request_hash != digest: raise HTTPException(409, {"code": "SETTINGS_REQUEST_CONFLICT"})
                return prior.response_snapshot
            current = current_settings(session, runtime)
            if current["version"] != payload.expected_version:
                raise HTTPException(409, {"code": "SETTINGS_VERSION_CHANGED"})
            result = {**current, "version": current["version"] + 1,
                "cutoff_date": payload.cutoff_date.isoformat(), "storage_timezone": payload.storage_timezone}
            session.add(PackagingSettingsRevision(version=result["version"], actor_id=actor.id,
                request_key=payload.idempotency_key, request_hash=digest,
                cutoff_date=payload.cutoff_date, storage_timezone=payload.storage_timezone,
                response_snapshot=result))
            try: session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(409, {"code": "SETTINGS_VERSION_CHANGED"}) from None
            return result

    return router
