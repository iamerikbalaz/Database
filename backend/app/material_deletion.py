"""Strict review/confirm contract for permanent removal of material records."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from app.schemas import ApiSchema, Sha256


class MaterialDeletionSelection(ApiSchema):
    id: UUID
    expected_updated_at: datetime


class MaterialDeletionPlanRequest(ApiSchema):
    materials: list[MaterialDeletionSelection] = Field(min_length=1, max_length=100)
    mode: Literal["RECORD_ONLY", "RECORD_AND_FILES"]

    @model_validator(mode="after")
    def unique_materials(self):
        if len({item.id for item in self.materials}) != len(self.materials): raise ValueError("Select each material once.")
        return self


class MaterialDeletionApplyRequest(MaterialDeletionPlanRequest):
    idempotency_key: UUID
    expected_proposal_hash: Sha256
    confirmed: Literal[True]

    @model_validator(mode="after")
    def nonzero_key(self):
        if not self.idempotency_key.int: raise ValueError("Use a nonzero request key.")
        return self


class MaterialDeletionResume(ApiSchema):
    confirmed: Literal[True]
