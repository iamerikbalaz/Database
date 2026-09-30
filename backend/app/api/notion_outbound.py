"""Authenticated outbound queue visibility, safe retries, and folder commands."""
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.auth.access import AccessDependency, CATALOG_MANAGERS
from app.db.models import Project, PublishedBrand
from app.db.notion_sync_models import NotionSyncState, OrderFolderOperation
from app.notion_outbound import enqueue_customer_sync, enqueue_order_sync, sync_status
from app.order_folders import OrderFolderError, folder_preflight, folder_status
from app.material_review import canonical_hash


class FolderCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["CREATE", "RENAME"]
    expected_updated_at: datetime
    confirmed: Literal[True]


def build_notion_outbound_router(database, settings):
    router = APIRouter(tags=["outbound synchronization"])

    def target(session, kind, identifier):
        model = PublishedBrand if kind == "CUSTOMER" else Project
        entity = session.scalar(select(model).where(model.id == identifier).with_for_update())
        if entity is None: raise HTTPException(404, "Record not found.")
        return entity

    @router.get("/api/notion-sync/{entity_type}/{entity_id}")
    def status(entity_type: Literal["CUSTOMER", "ORDER"], entity_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            target(session, entity_type, entity_id)
            return {**sync_status(session, entity_type, entity_id), "enabled": settings.notion_outbound_enabled}

    @router.post("/api/notion-sync/{entity_type}/{entity_id}/retry")
    def retry(entity_type: Literal["CUSTOMER", "ORDER"], entity_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            entity = target(session, entity_type, entity_id)
            state = session.scalar(select(NotionSyncState).where(NotionSyncState.entity_type == entity_type,
                NotionSyncState.entity_id == entity_id).with_for_update())
            if state is not None and state.status in {"RUNNING", "RECONCILE"}:
                raise HTTPException(409, {"code": "NOTION_RECONCILIATION_REQUIRED" if state.status == "RECONCILE" else "NOTION_SYNC_BUSY"})
            if not settings.notion_outbound_enabled: raise HTTPException(503, {"code": "NOTION_OUTBOUND_DISABLED"})
            (enqueue_customer_sync if entity_type == "CUSTOMER" else enqueue_order_sync)(session, entity, access.user.id)
            session.commit()
            return sync_status(session, entity_type, entity_id)

    @router.get("/api/orders/{order_id}/folder")
    def folder_info(order_id: UUID, access: AccessDependency):
        with database.session() as session:
            access.check(session)
            order = target(session, "ORDER", order_id)
            return {**folder_status(session, order.id), "enabled": settings.order_folders_enabled,
                "folder_path": order.folder_path, "target_name": order.name}

    @router.post("/api/orders/{order_id}/folder", status_code=202)
    def folder_command(order_id: UUID, payload: FolderCommand, access: AccessDependency,
            idempotency_key: UUID = Header(alias="Idempotency-Key")):
        with database.session() as session:
            access.check(session, CATALOG_MANAGERS)
            order = target(session, "ORDER", order_id)
            if idempotency_key.int == 0: raise HTTPException(422, {"code": "ORDER_FOLDER_REQUEST_KEY_INVALID"})
            request_hash = canonical_hash({"order_id": str(order_id), "payload": payload.model_dump(mode="json")})
            previous = session.scalar(select(OrderFolderOperation).where(OrderFolderOperation.actor_id == access.user.id,
                OrderFolderOperation.request_key == idempotency_key))
            if previous:
                if previous.order_id != order_id or previous.request_hash != request_hash:
                    raise HTTPException(409, {"code": "ORDER_FOLDER_REQUEST_KEY_REUSED"})
                return {"operation_id": str(previous.id), "status": previous.status, "error_code": previous.error_code}
            if not settings.order_folders_enabled: raise HTTPException(503, {"code": "ORDER_FOLDERS_DISABLED"})
            actual = order.updated_at if order.updated_at.tzinfo else order.updated_at.replace(tzinfo=UTC)
            expected = payload.expected_updated_at if payload.expected_updated_at.tzinfo else payload.expected_updated_at.replace(tzinfo=UTC)
            if actual != expected:
                raise HTTPException(409, {"code": "ORDER_CHANGED"})
            active = session.scalar(select(OrderFolderOperation).where(OrderFolderOperation.order_id == order_id,
                OrderFolderOperation.status.in_(["PENDING", "RUNNING", "RECONCILE"])).limit(1))
            if active: raise HTTPException(409, {"code": "ORDER_FOLDER_OPERATION_PENDING"})
            if (payload.action == "CREATE" and order.folder_path) or (payload.action == "RENAME" and not order.folder_path):
                raise HTTPException(409, {"code": "ORDER_FOLDER_ACTION_INVALID"})
            try:
                _, identity = folder_preflight(settings.order_folders_root, order.name, order.folder_path)
            except (OrderFolderError, OSError) as error:
                raise HTTPException(409, {"code": getattr(error, "code", "ORDER_FOLDER_UNAVAILABLE")}) from None
            operation = OrderFolderOperation(order_id=order.id, actor_id=access.user.id, request_key=idempotency_key,
                request_hash=request_hash,
                action=payload.action, source_path=order.folder_path, target_name=order.name,
                source_identity=identity, status="PENDING")
            session.add(operation); session.commit()
            return {"operation_id": str(operation.id), "status": operation.status, "error_code": None}

    return router
