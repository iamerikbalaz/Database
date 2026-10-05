"""Durable material creation plans and atomic bulk content receipts."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint, Uuid, event, func, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class MaterialCreationBatch(Base):
    __tablename__ = "material_creation_batches"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_material_creation_actor_key"),
        CheckConstraint("status IN ('PENDING','PARTIAL','COMPLETED')", name="ck_material_creation_status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    customer_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"), nullable=True)
    request_key: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict] = mapped_column(DOCUMENT)
    source_context: Mapped[dict] = mapped_column(DOCUMENT)
    items: Mapped[list] = mapped_column(DOCUMENT)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MaterialContentBatch(Base):
    __tablename__ = "material_content_batches"
    __table_args__ = (UniqueConstraint("actor_id", "request_key", name="uq_material_content_actor_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@event.listens_for(MaterialCreationBatch, "before_update")
def protect_creation(_mapper, _connection, item):
    attrs = inspect(item).attrs
    if any(attrs[key].history.has_changes() for key in (
            "id", "actor_id", "customer_id", "request_key", "request_hash", "request_payload", "source_context", "created_at")):
        raise ValueError("Material creation authorization is immutable.")
    if "COMPLETED" in attrs.status.history.deleted or (not attrs.status.history.deleted and item.status == "COMPLETED"):
        raise ValueError("Completed material creation receipts are immutable.")


def reject_change(*_):
    raise ValueError("Material operation receipts must be retained unchanged.")


event.listen(MaterialCreationBatch, "before_delete", reject_change)
event.listen(MaterialContentBatch, "before_update", reject_change)
event.listen(MaterialContentBatch, "before_delete", reject_change)
