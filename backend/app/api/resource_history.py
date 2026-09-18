"""Explicitly paged administrator history of ordinary resource create/edit calls."""
from datetime import UTC
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.auth.access import ADMIN, AccessDependency
from app.db.models import ResourceChangeEvent
from app.material_review import canonical_hash
from app.resource_history import KINDS, valid_snapshot


def event_view(item, kind, identifier):
    if (item.kind != kind or getattr(item, KINDS[kind][1]) != identifier
            or not valid_snapshot(item.after_snapshot, kind, identifier)
            or (item.action == "CREATED" and item.before_snapshot != {})
            or (item.action != "CREATED" and not valid_snapshot(item.before_snapshot, kind, identifier))
            or canonical_hash(item.before_snapshot) != item.before_hash or canonical_hash(item.after_snapshot) != item.after_hash):
        raise HTTPException(503, {"code": "RESOURCE_HISTORY_INVALID"})
    timestamp = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=UTC)
    return {"id": str(item.id), "resource_kind": kind, "resource_id": str(identifier), "actor_id": str(item.actor_id),
        "version": item.version, "action": item.action, "before": item.before_snapshot, "after": item.after_snapshot,
        "before_sha256": item.before_hash, "after_sha256": item.after_hash, "created_at": timestamp.isoformat()}


def build_resource_history_router(database):
    router = APIRouter(tags=["resource history"])

    def make_read(kind):
        model, column, _, _ = KINDS[kind]

        def history(resource_id: UUID, access: AccessDependency, after: UUID | None = None):
            with database.session() as session:
                access.check(session, ADMIN)
                if session.get(model, resource_id) is None: raise HTTPException(404, "Resource not found.")
                target = getattr(ResourceChangeEvent, column)
                query = select(ResourceChangeEvent).where(target == resource_id)
                if after is not None:
                    cursor = session.scalar(select(ResourceChangeEvent).where(ResourceChangeEvent.id == after, target == resource_id))
                    if cursor is None: raise HTTPException(409, {"code": "RESOURCE_HISTORY_CURSOR_INVALID"})
                    query = query.where(ResourceChangeEvent.version < cursor.version)
                records = list(session.scalars(query.order_by(ResourceChangeEvent.version.desc()).limit(21)))
                return {"resource_kind": kind, "resource_id": str(resource_id),
                    "items": [event_view(item, kind, resource_id) for item in records[:20]],
                    "next_cursor": str(records[19].id) if len(records) > 20 else None}
        return history

    for kind, (_, _, segment, _) in KINDS.items():
        router.add_api_route(f"/api/{segment}/{{resource_id}}/history", make_read(kind), methods=["GET"], name=f"{kind.lower()}_history")
    return router
