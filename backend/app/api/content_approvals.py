"""Idempotent human decisions for a displayed publication-content context."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material, _state, _request_hash, _replay, _record
from app.auth.access import AccessDependency
from app.catalog import Reason
from app.db.models import MaterialContentApproval
from app.material_identity import require_material_idle
from app.publication_content import content_review, approval_view
from app.schemas import ApiSchema
from app.worker_client import Sha256


class ContentApprovalRequest(ApiSchema):
    idempotency_key: UUID
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    expected_context_hash: Sha256
    warnings_acknowledged: Annotated[bool, Field(strict=True)] = False
    note: Reason | None = None


def build_content_approvals_router(database):
    router = APIRouter(prefix="/api/materials", tags=["content approvals"])

    @router.get("/{material_id}/content-review")
    def review(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            material = _material(session, material_id, access, lock=True)
            return content_review(session, material)

    @router.get("/{material_id}/content-approvals")
    def history(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session); _material(session, material_id, access)
            return [{**approval_view(item), "snapshot": item.snapshot} for item in session.scalars(
                select(MaterialContentApproval).where(MaterialContentApproval.material_id == material_id)
                .order_by(MaterialContentApproval.created_at.desc(), MaterialContentApproval.id.desc()).limit(100))]

    @router.post("/{material_id}/content/approve")
    def approve(material_id: UUID, payload: ContentApprovalRequest, access: AccessDependency):
        request_hash = _request_hash("CONTENT_APPROVED", material_id, payload)
        with database.session() as session:
            actor = access.check(session, PUBLICATION_APPROVERS)
            material = _material(session, material_id, access, lock=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            require_material_idle(session, material_id)
            review = content_review(session, material)
            if review["content_revision"] != payload.expected_revision or review["context_hash"] != payload.expected_context_hash:
                raise HTTPException(409, {"code": "CONTENT_CONTEXT_CHANGED"})
            if review["errors"]:
                raise HTTPException(409, {"code": "CONTENT_APPROVAL_BLOCKED", "errors": review["errors"]})
            if review["approval"]:
                raise HTTPException(409, {"code": "CONTENT_ALREADY_APPROVED"})
            if review["warnings"] and (not payload.warnings_acknowledged or not payload.note):
                raise HTTPException(422, {"code": "CONTENT_WARNINGS_REQUIRE_ACKNOWLEDGMENT_AND_NOTE"})
            state = _state(session, material_id, create=True)
            session.add(MaterialContentApproval(material_id=material_id, content_revision=payload.expected_revision,
                actor_id=actor.id, context_hash=review["context_hash"], snapshot=review["snapshot"],
                note=payload.note, warnings_acknowledged=payload.warnings_acknowledged))
            session.flush()
            return _record(session, material, state, actor.id, "CONTENT_APPROVED", payload, request_hash,
                content_review(session, material), audit={"content_revision": payload.expected_revision,
                    "context_hash": review["context_hash"], "note": payload.note,
                    "warnings_acknowledged": payload.warnings_acknowledged})

    return router
