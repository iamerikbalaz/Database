"""Prepare exact approved CSV snapshots; no packaging, upload or publication."""
import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import Field
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.material_approvals import PUBLICATION_APPROVERS
from app.auth.access import AccessDependency
from app.auth.service import _aware
from app.catalog import Reason
from app.db.models import MaterialAuditEvent, PublicationBatch, PublicationBatchItem
from app.material_review import canonical_hash
from app.publication_csv import Digest, render_publication_csv
from app.publication_preflight import PublicationSelection, prepare_publication


class PublicationCreate(PublicationSelection):
    idempotency_key: UUID
    expected_preview_hash: Digest
    reason: Reason
    warnings_acknowledged: Annotated[bool, Field(strict=True)] = False


def _summary(batch):
    return {"id": str(batch.id), "actor_id": str(batch.actor_id), "status": "PREPARED", "row_count": batch.row_count,
        "snapshot_hash": batch.snapshot_hash, "csv_sha256": batch.csv_sha256,
        "created_at": _aware(batch.created_at).isoformat()}


def _view(session, batch):
    items = list(session.scalars(select(PublicationBatchItem).where(PublicationBatchItem.batch_id == batch.id)
        .order_by(PublicationBatchItem.ordinal)))
    return {**_summary(batch), "reason": batch.reason, "warnings_acknowledged": batch.warnings_acknowledged,
        "warnings": batch.warnings, "items": [{"material_id": str(item.material_id), "ordinal": item.ordinal,
            "snapshot_hash": item.snapshot_hash, "row": item.snapshot["csv_row"],
            "technical_approval_id": str(item.technical_approval_id), "publication_approval_id": str(item.publication_approval_id),
            "content_approval_id": item.snapshot["content_approval_id"], "metadata_snapshot_id": str(item.metadata_snapshot_id)} for item in items]}


def _batch(session, batch_id):
    batch = session.get(PublicationBatch, batch_id)
    if batch is None: raise HTTPException(404, "Publication batch not found.")
    return batch


def build_publication_batches_router(database):
    router = APIRouter(prefix="/api/publication-batches", tags=["publication preparation"])

    @router.post("")
    def create(payload: PublicationCreate, access: AccessDependency):
        request_hash = canonical_hash({"operation": "PUBLICATION_BATCH_PREPARED", "payload": payload.model_dump(mode="json")})
        with database.session() as session:
            actor = access.check(session, PUBLICATION_APPROVERS)
            prior = session.scalar(select(PublicationBatch).where(PublicationBatch.actor_id == actor.id,
                PublicationBatch.request_key == payload.idempotency_key))
            if prior:
                if prior.request_hash != request_hash: raise HTTPException(409, {"code": "PUBLICATION_REQUEST_CONFLICT"})
                return JSONResponse(status_code=201, content=_view(session, prior))
            prepared = prepare_publication(session, payload, access)
            if prepared.preview["preview_hash"] != payload.expected_preview_hash:
                raise HTTPException(409, {"code": "PUBLICATION_PREVIEW_CHANGED"})
            if not prepared.preview["can_prepare"]:
                raise HTTPException(409, {"code": "PUBLICATION_INPUTS_BLOCKED", "preview": prepared.preview})
            artifact = render_publication_csv(prepared.rows)
            warnings = [warning for item in prepared.preview["items"] for warning in item["warnings"]]
            if warnings and not payload.warnings_acknowledged:
                raise HTTPException(422, {"code": "PUBLICATION_WARNINGS_REQUIRE_ACKNOWLEDGMENT"})
            access.check(session, PUBLICATION_APPROVERS)
            batch = PublicationBatch(actor_id=actor.id, request_key=payload.idempotency_key, request_hash=request_hash,
                snapshot_hash=prepared.preview["preview_hash"], csv_sha256=artifact.sha256, csv_bytes=artifact.data,
                row_count=len(artifact.rows), reason=payload.reason, warnings_acknowledged=payload.warnings_acknowledged, warnings=warnings)
            session.add(batch); session.flush()
            for ordinal, digest in enumerate(artifact.rows, 1):
                snapshot = prepared.snapshots[digest.material_id]
                item = PublicationBatchItem(batch_id=batch.id, material_id=digest.material_id, ordinal=ordinal,
                    generation=snapshot["generation"], revision_hash=digest.revision_hash, content_context_hash=digest.content_context_hash,
                    snapshot_hash=canonical_hash(snapshot), snapshot=snapshot,
                    **{field: UUID(snapshot[field]) for field in ("technical_check_id", "metadata_snapshot_id",
                        "technical_approval_id", "publication_approval_id")})
                session.add(item)
                session.add(MaterialAuditEvent(material_id=item.material_id, actor_id=actor.id,
                    event_type="PUBLICATION_BATCH_PREPARED", generation=item.generation, revision_hash=item.revision_hash,
                    result={"audit": {"batch_id": str(batch.id), "snapshot_hash": item.snapshot_hash, "csv_sha256": batch.csv_sha256,
                        "reason": payload.reason, "warnings_acknowledged": payload.warnings_acknowledged}}))
            try: session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(409, {"code": "PUBLICATION_CONCURRENT_CONFLICT"}) from None
            return JSONResponse(status_code=201, content=_view(session, batch))

    @router.get("")
    def history(access: AccessDependency, after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS)
            query = select(PublicationBatch).order_by(PublicationBatch.created_at.desc(), PublicationBatch.id.desc())
            if after:
                cursor = _batch(session, after)
                stamp = select(PublicationBatch.created_at).where(PublicationBatch.id == after).scalar_subquery()
                query = query.where(or_(PublicationBatch.created_at < stamp, and_(PublicationBatch.created_at == stamp, PublicationBatch.id < cursor.id)))
            rows = list(session.scalars(query.limit(limit + 1)))
            return {"items": [_summary(row) for row in rows[:limit]], "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None}

    @router.get("/{batch_id}")
    def detail(batch_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS)
            return _view(session, _batch(session, batch_id))

    @router.get("/{batch_id}/csv")
    def download(batch_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, PUBLICATION_APPROVERS)
            batch = _batch(session, batch_id)
            if hashlib.sha256(batch.csv_bytes).hexdigest() != batch.csv_sha256:
                raise HTTPException(500, {"code": "PUBLICATION_ARTIFACT_INTEGRITY_ERROR"})
            return Response(content=batch.csv_bytes, media_type="text/csv", headers={
                "Content-Disposition": f'attachment; filename="publication-{batch.id}.csv"',
                "X-Content-SHA256": batch.csv_sha256, "X-Content-Type-Options": "nosniff"})

    return router
