"""Atomic receipts for keyed ordinary writes; no replay grants access by itself."""
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.auth.access import ADMIN, CATALOG_MANAGERS, MATERIAL_EDITORS
from app.db.models import Company, PublishedBrand, Project, InternalUser, PBRMaterial, ResourceCommand
from app.material_review import canonical_hash
from app.schemas import CompanyRead, PublishedBrandRead, ProjectRead, InternalUserRead, PBRMaterialRead

CommandKey = Annotated[UUID | None, Header(alias="Idempotency-Key")]


async def command_input(request: Request):
    # FastAPI independently validates the same parsed JSON against the route's
    # strict schema. Hash the submitted values, before trimming/default/coercion,
    # so the browser can bind read recovery without duplicating Pydantic rules.
    try:
        return await request.json()
    except ValueError:
        # Non-JSON content types reach dependencies before body validation.
        # Preserve a bounded client error, without exposing the submitted body.
        raise HTTPException(422, "A valid JSON request body is required.") from None


CommandInput = Annotated[dict, Depends(command_input)]
KINDS = {
    "COMPANY": (Company, CompanyRead, "company_id"),
    "BRAND": (PublishedBrand, PublishedBrandRead, "brand_id"),
    "PROJECT": (Project, ProjectRead, "project_id"),
    "USER": (InternalUser, InternalUserRead, "user_id"),
    "MATERIAL": (PBRMaterial, PBRMaterialRead, "material_id"),
}
PRIVILEGES = {"ADMIN": ADMIN, "CATALOG": CATALOG_MANAGERS, "MATERIAL_NAME": MATERIAL_EDITORS}


def command_for_actor(session, actor_id, key):
    return session.scalar(select(ResourceCommand).where(ResourceCommand.actor_id == actor_id, ResourceCommand.request_key == key))


def authorize_receipt(session, access, receipt):
    if access.user.role not in PRIVILEGES[receipt.privilege]:
        raise HTTPException(403, "Forbidden.")
    model, _, field = KINDS[receipt.kind]
    target = session.scalar(select(model).where(model.id == getattr(receipt, field)).with_for_update(read=True))
    if target is None:
        raise HTTPException(404, "Record not found.")
    if receipt.kind == "MATERIAL": access.require_material(target)


def saved_response(receipt):
    try:
        schema = KINDS[receipt.kind][1]
        result = schema.model_validate(receipt.response_snapshot).model_dump(mode="json", exclude_unset=True)
        if result != receipt.response_snapshot or result["id"] != str(getattr(receipt, KINDS[receipt.kind][2])) or canonical_hash(result) != receipt.response_hash:
            raise ValueError("Invalid command receipt")
        return result
    except (KeyError, ValueError, TypeError):
        raise HTTPException(503, {"code": "RESOURCE_COMMAND_UNAVAILABLE"}) from None


def receipt_view(receipt):
    return {"id": str(receipt.id), "actor_id": str(receipt.actor_id), "request_key": str(receipt.request_key), "kind": receipt.kind,
        "action": receipt.action, "resource_id": str(getattr(receipt, KINDS[receipt.kind][2])),
        "request_hash": receipt.request_hash, "response_hash": receipt.response_hash,
        "response": saved_response(receipt), "created_at": receipt.created_at}


class ResourceWrite:
    def __init__(self, access, kind, action, payload, key=None, target=None, *, raw_payload=None):
        if key is not None and key.int == 0:
            raise HTTPException(422, {"code": "RESOURCE_COMMAND_KEY_INVALID"})
        self.access, self.kind, self.action, self.key, self.target = access, kind, action, key, target
        self.payload = payload.model_dump(mode="json", exclude_unset=action == "UPDATED")
        self.request_hash = canonical_hash({"schema_version": 1, "kind": kind, "action": action,
            "target_id": str(target) if target is not None else None, "payload": raw_payload if raw_payload is not None else self.payload})
        self.privilege = ("ADMIN" if kind == "USER" else "MATERIAL_NAME" if kind == "MATERIAL" and
            action == "UPDATED" and set(self.payload) <= {"material_name", "note", "workflow_status", "expected_updated_at"} else "CATALOG")

    def replay(self, session):
        # The caller already holds the access gate, actor credential and session
        # locks. The credential lock serializes this actor across sessions/keys.
        if self.key is None: return None
        receipt = command_for_actor(session, self.access.user.id, self.key)
        if receipt is None: return None
        authorize_receipt(session, self.access, receipt)
        if receipt.kind != self.kind or receipt.action != self.action or receipt.request_hash != self.request_hash:
            raise HTTPException(409, {"code": "RESOURCE_COMMAND_KEY_REUSED"})
        return JSONResponse(saved_response(receipt), status_code=201 if self.action == "CREATED" else 200,
            headers={"Idempotency-Replayed": "true"})

    def record(self, session, item):
        if self.key is None: return None
        session.flush()
        snapshot = KINDS[self.kind][1].model_validate(item).model_dump(mode="json")
        receipt = ResourceCommand(actor_id=self.access.user.id, request_key=self.key, kind=self.kind, action=self.action,
            privilege=self.privilege, **{KINDS[self.kind][2]: item.id}, request_hash=self.request_hash,
            response_snapshot=snapshot, response_hash=canonical_hash(snapshot))
        session.add(receipt); session.flush()
        return JSONResponse(snapshot, status_code=201 if self.action == "CREATED" else 200,
            headers={"Idempotency-Replayed": "false"})
