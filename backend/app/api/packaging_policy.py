"""Authenticated immutable policy selection and reviewed administrator overrides."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.api.material_review import _material, _record, _replay, _request_hash, _state
from app.auth.access import ADMIN, AccessDependency
from app.catalog import Reason
from app.db.models import MaterialPackagingPolicy
from app.material_review import canonical_hash, invalidate_review
from app.packaging_policy import current_policy, initial_evidence, override_preview, policy_view
from app.publication_csv import Digest
from app.schemas import ApiSchema, MaterialZipPolicy


class PolicySelection(ApiSchema):
    idempotency_key: UUID
    expected_generation: Annotated[int, Field(ge=0, strict=True)]
    expected_revision_hash: Digest
    expected_inventory_id: UUID
    reason: Reason


class PolicyOverridePreview(ApiSchema):
    expected_policy_id: UUID
    policy: MaterialZipPolicy


class PolicyOverride(PolicyOverridePreview):
    idempotency_key: UUID
    expected_preview_hash: Digest
    reason: Reason


def build_packaging_policy_router(database, settings):
    router = APIRouter(prefix="/api/materials", tags=["packaging policy"])

    @router.get("/{material_id}/packaging-policy")
    def detail(material_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            _material(session, material_id, access)
            return {"current": policy_view(current_policy(session, material_id))}

    @router.get("/{material_id}/packaging-policy/history")
    def history(material_id: UUID, access: AccessDependency,
        before: Annotated[int | None, Query(ge=1)] = None, limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            access.check(session)
            _material(session, material_id, access)
            query = select(MaterialPackagingPolicy).where(MaterialPackagingPolicy.material_id == material_id)
            if before is not None: query = query.where(MaterialPackagingPolicy.revision < before)
            rows = list(session.scalars(query.order_by(MaterialPackagingPolicy.revision.desc()).limit(limit + 1)))
            return {"items": [policy_view(row) for row in rows[:limit]],
                "next_before": rows[limit - 1].revision if len(rows) > limit else None}

    @router.post("/{material_id}/packaging-policy/select")
    def select_policy(material_id: UUID, payload: PolicySelection, access: AccessDependency):
        request_hash = _request_hash("PACKAGING_POLICY_SELECT", material_id, payload)
        with database.session() as session:
            actor = access.check(session, PUBLICATION_APPROVERS)
            material = _material(session, material_id, access, lock=True, mutating=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            state = _state(session, material_id)
            current = current_policy(session, material_id)
            event = "PACKAGING_POLICY_REUSED"
            if current is None:
                policy, evidence = initial_evidence(session, material, state, payload, settings.zip_policy_timezone)
                current = MaterialPackagingPolicy(material_id=material_id, actor_id=actor.id, revision=1,
                    inventory_id=state.inventory_id, policy=policy, storage_timezone=settings.zip_policy_timezone,
                    reason=payload.reason, evidence=evidence, evidence_hash=canonical_hash(evidence))
                session.add(current)
                try: session.flush()
                except IntegrityError:
                    session.rollback()
                    raise HTTPException(409, {"code": "PACKAGING_POLICY_CONCURRENT_CONFLICT"}) from None
                event = "PACKAGING_POLICY_SELECTED"
            state = state or _state(session, material_id, create=True)
            body = {"current": policy_view(current)}
            return _record(session, material, state, actor.id, event, payload, request_hash, body,
                audit={"decision_id": str(current.id), "policy": current.policy, "revision": current.revision, "reason": payload.reason})

    @router.post("/{material_id}/packaging-policy/override-preview")
    def preview(material_id: UUID, payload: PolicyOverridePreview, access: AccessDependency):
        with database.session() as session:
            access.check(session, ADMIN)
            material = _material(session, material_id, access, lock=True, mutating=True)
            return override_preview(material, _state(session, material_id), current_policy(session, material_id),
                payload.policy.value, payload.expected_policy_id)

    @router.post("/{material_id}/packaging-policy/override")
    def override(material_id: UUID, payload: PolicyOverride, access: AccessDependency):
        request_hash = _request_hash("PACKAGING_POLICY_OVERRIDE", material_id, payload)
        with database.session() as session:
            actor = access.check(session, ADMIN)
            material = _material(session, material_id, access, lock=True, mutating=True)
            replay = _replay(session, actor.id, material_id, payload, request_hash)
            if replay is not None: return replay
            state = _state(session, material_id)
            current = current_policy(session, material_id)
            impact = override_preview(material, state, current, payload.policy.value, payload.expected_policy_id)
            if impact["preview_hash"] != payload.expected_preview_hash:
                raise HTTPException(409, {"code": "PACKAGING_POLICY_PREVIEW_CHANGED"})
            evidence = {"schema_version": 1, "preview": impact}
            decision = MaterialPackagingPolicy(material_id=material_id, actor_id=actor.id, revision=current.revision + 1,
                previous_id=current.id, policy=payload.policy.value, storage_timezone=current.storage_timezone,
                reason=payload.reason, evidence=evidence, evidence_hash=canonical_hash(evidence))
            session.add(decision)
            try: session.flush()
            except IntegrityError:
                session.rollback()
                raise HTTPException(409, {"code": "PACKAGING_POLICY_CONCURRENT_CONFLICT"}) from None
            state = state or _state(session, material_id, create=True)
            invalidate_review(session, material, actor.id, "PACKAGING_POLICY_CHANGED", record_event=False)
            body = {"current": policy_view(decision)}
            return _record(session, material, state, actor.id, "PACKAGING_POLICY_OVERRIDDEN", payload, request_hash, body,
                audit={"previous_id": str(current.id), "decision_id": str(decision.id), "policy": decision.policy,
                    "revision": decision.revision, "reason": payload.reason, "preview_hash": impact["preview_hash"]})

    return router
