"""Versioned administrator-owned storage roots and replay receipts."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid, event, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class PathsSettingsRevision(Base):
    __tablename__ = "paths_settings_revisions"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_paths_settings_actor_request"),
        CheckConstraint("version >= 1", name="ck_paths_settings_version"),
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _immutable(_mapper, _connection, _item):
    raise ValueError("Path settings revisions are append-only.")


event.listen(PathsSettingsRevision, "before_update", _immutable)
event.listen(PathsSettingsRevision, "before_delete", _immutable)
