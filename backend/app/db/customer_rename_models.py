"""Durable customer rename authorization; filesystem receipts stay in identity jobs."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, Uuid, event, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CustomerRenameOperation(Base):
    __tablename__ = "customer_rename_operations"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_customer_rename_actor_key"),
        CheckConstraint("status IN ('RUNNING','RECOVERY_REQUIRED','COMPLETED','PARTIAL')", name="ck_customer_rename_status"),
        Index("uq_customer_rename_active", "customer_id", unique=True,
              postgresql_where=text("status IN ('RUNNING','RECOVERY_REQUIRED')"),
              sqlite_where=text("status IN ('RUNNING','RECOVERY_REQUIRED')")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    customer_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"))
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    proposal_hash: Mapped[str] = mapped_column(String(64))
    authorization: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


@event.listens_for(CustomerRenameOperation, "before_delete")
def reject_delete(*_):
    raise ValueError("Customer rename authorization must be retained")


@event.listens_for(CustomerRenameOperation, "before_update")
def protect_authorization(mapper, connection, target):
    from sqlalchemy import inspect
    state = inspect(target)
    if any(state.attrs[key].history.has_changes() for key in (
            "id", "customer_id", "actor_id", "request_key", "request_hash", "proposal_hash", "authorization", "created_at")):
        raise ValueError("Customer rename authorization is immutable")
    previous = state.attrs.status.history.deleted
    old_status = previous[0] if previous else target.status
    if old_status in {"COMPLETED", "PARTIAL"}:
        raise ValueError("Customer rename terminal outcome is immutable")
