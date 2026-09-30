"""Append-only command receipts and audit for the Customers / Orders directory."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid, event, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
TARGET = "(kind='CUSTOMER' AND customer_id IS NOT NULL AND order_id IS NULL) OR (kind='ORDER' AND order_id IS NOT NULL AND customer_id IS NULL)"


class DirectoryCommand(Base):
    __tablename__ = "directory_commands"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_directory_commands_actor_key"),
        CheckConstraint(TARGET, name="ck_directory_commands_target"),
        CheckConstraint("action IN ('CREATED','UPDATED','LOGO_UPDATED')", name="ck_directory_commands_action"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(20))
    customer_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"))
    order_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(DOCUMENT)
    response_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DirectoryChangeEvent(Base):
    __tablename__ = "directory_change_events"
    __table_args__ = (
        UniqueConstraint("customer_id", "version", name="uq_directory_change_customer_version"),
        UniqueConstraint("order_id", "version", name="uq_directory_change_order_version"),
        CheckConstraint(TARGET, name="ck_directory_change_target"),
        CheckConstraint("version > 0", name="ck_directory_change_version"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(20))
    customer_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"))
    order_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(Integer)
    before_snapshot: Mapped[dict] = mapped_column(DOCUMENT)
    after_snapshot: Mapped[dict] = mapped_column(DOCUMENT)
    before_hash: Mapped[str] = mapped_column(String(64))
    after_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _immutable(mapper, connection, target):
    raise ValueError("Directory receipts and change history are immutable.")


for _model in (DirectoryCommand, DirectoryChangeEvent):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
