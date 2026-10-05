"""Retained deletion receipts and exclusive ownership while source removal recovers."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint, Uuid, event, func, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
TERMINAL = {"COMPLETED", "PARTIAL", "REJECTED"}


class MaterialDeletionOperation(Base):
    __tablename__ = "material_deletion_operations"
    __table_args__ = (UniqueConstraint("actor_id", "request_key", name="uq_material_deletion_actor_key"),
        CheckConstraint("status IN ('RUNNING','RECOVERY_REQUIRED','COMPLETED','PARTIAL','REJECTED')", name="ck_material_deletion_status"),
        CheckConstraint("mode IN ('RECORD_ONLY','RECORD_AND_FILES')", name="ck_material_deletion_mode"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    proposal_hash: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(24))
    request_payload: Mapped[dict] = mapped_column(DOCUMENT)
    plan: Mapped[dict] = mapped_column(DOCUMENT)
    items: Mapped[list] = mapped_column(DOCUMENT)
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MaterialDeletionOwner(Base):
    __tablename__ = "material_deletion_owners"
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("material_deletion_operations.id", ondelete="RESTRICT"), index=True)
    brand_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"), index=True)
    folder_path: Mapped[str | None] = mapped_column(String(2048))


@event.listens_for(MaterialDeletionOperation, "before_update")
def immutable_authorization(_mapper, _connection, item):
    attrs = inspect(item).attrs
    if any(attrs[key].history.has_changes() for key in ("id", "actor_id", "request_key", "request_hash", "proposal_hash", "mode", "request_payload", "plan", "created_at")):
        raise ValueError("Material deletion authorization is immutable.")
    old = attrs.status.history.deleted
    if any(status in TERMINAL for status in old) or (not old and item.status in TERMINAL):
        raise ValueError("Completed material deletion receipts are immutable.")


@event.listens_for(MaterialDeletionOperation, "before_delete")
def retained_receipt(*_):
    raise ValueError("Material deletion receipts must be retained.")
