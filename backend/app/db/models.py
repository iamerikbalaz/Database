from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base


class ProjectStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class InternalUserRole(StrEnum):
    PROCESSOR = "PROCESSOR"
    PRODUCTION_LEAD = "PRODUCTION_LEAD"
    LEADERSHIP = "LEADERSHIP"
    ADMIN = "ADMIN"


class MaterialWorkflowStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class MaterialValidationStatus(StrEnum):
    NOT_CHECKED = "NOT_CHECKED"
    VALID = "VALID"
    WARNING = "WARNING"
    ERROR = "ERROR"
    METADATA_MISSING = "METADATA_MISSING"


class MaterialMetadataStatus(StrEnum):
    NOT_SCANNED = "NOT_SCANNED"
    MISSING = "MISSING"
    VALID = "VALID"
    WARNING = "WARNING"
    INVALID = "INVALID"


class MaterialPublicationStatus(StrEnum):
    NOT_PUBLISHED = "NOT_PUBLISHED"
    PREPARING = "PREPARING"
    UPLOADED_WAITING_FOR_IMPORT = "UPLOADED_WAITING_FOR_IMPORT"
    WAITING_FOR_VERIFICATION = "WAITING_FOR_VERIFICATION"
    PUBLISHED_CURRENT = "PUBLISHED_CURRENT"
    PUBLISHED_UPDATE_REQUIRED = "PUBLISHED_UPDATE_REQUIRED"
    PUBLICATION_ERROR = "PUBLICATION_ERROR"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


_JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _strip_characters(expression: str, characters: str) -> str:
    for character in characters:
        expression = f"replace({expression}, '{character}', '')"
    return expression


_SHA256_REMAINDER = _strip_characters("source_sha256", "0123456789abcdef")
_HEX_REMAINDER = _strip_characters("substr(hex_color, 2)", "0123456789ABCDEF")
_RESOLUTION_REMAINDER = _strip_characters(
    "substr(master_resolution, 1, length(master_resolution) - 1)",
    "0123456789",
)

_MATERIAL_METADATA_WARNING_FIELDS = frozenset({"code", "message", "path"})


def _validate_material_metadata_warnings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Material metadata warnings must be a list.")
    for index, warning in enumerate(value):
        if not isinstance(warning, dict):
            raise ValueError(f"Material metadata warning {index} must be an object.")
        if not warning.keys() <= _MATERIAL_METADATA_WARNING_FIELDS:
            raise ValueError(f"Material metadata warning {index} has unknown fields.")

        code = warning.get("code")
        if not isinstance(code, str) or not code.strip() or len(code) > 100:
            raise ValueError(
                f"Material metadata warning {index} code must be non-empty text "
                "with at most 100 characters."
            )
        message = warning.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(
                f"Material metadata warning {index} message must be non-empty text."
            )
        path = warning.get("path")
        if path is not None and (
            not isinstance(path, str) or not path.strip() or len(path) > 2048
        ):
            raise ValueError(
                f"Material metadata warning {index} path must be null or non-empty text "
                "with at most 2048 characters."
            )
    return value


def _material_metadata_constraints(table_name: str) -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint(
            "status IN ('NOT_SCANNED', 'MISSING', 'VALID', 'WARNING', 'INVALID')",
            name=f"ck_{table_name}_status",
        ),
        CheckConstraint(
            "source_filename IS NULL OR "
            "(length(source_filename) BETWEEN 1 AND 255 "
            "AND replace(source_filename, '/', '') = source_filename "
            "AND replace(source_filename, '\\', '') = source_filename)",
            name=f"ck_{table_name}_source_filename",
        ),
        CheckConstraint(
            "source_sha256 IS NULL OR "
            f"(length(source_sha256) = 64 AND source_sha256 = lower(source_sha256) "
            f"AND {_SHA256_REMAINDER} = '')",
            name=f"ck_{table_name}_source_sha256",
        ),
        CheckConstraint(
            "hex_color IS NULL OR "
            f"(length(hex_color) = 7 AND substr(hex_color, 1, 1) = '#' "
            f"AND hex_color = upper(hex_color) AND {_HEX_REMAINDER} = '')",
            name=f"ck_{table_name}_hex_color",
        ),
        CheckConstraint(
            "width_cm IS NULL OR width_cm > 0",
            name=f"ck_{table_name}_width_cm_positive",
        ),
        CheckConstraint(
            "height_cm IS NULL OR height_cm > 0",
            name=f"ck_{table_name}_height_cm_positive",
        ),
        CheckConstraint(
            "master_resolution IS NULL OR "
            "(length(master_resolution) BETWEEN 2 AND 16 "
            "AND substr(master_resolution, length(master_resolution), 1) = 'K' "
            "AND substr(master_resolution, 1, 1) IN "
            "('1', '2', '3', '4', '5', '6', '7', '8', '9') "
            f"AND {_RESOLUTION_REMAINDER} = '')",
            name=f"ck_{table_name}_master_resolution",
        ),
        CheckConstraint(
            "pbr_material_metadata_warnings_are_valid(warnings)",
            name=f"ck_{table_name}_warnings",
        ).ddl_if(dialect="postgresql"),
    )


class MaterialMetadataFieldsMixin:
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MaterialMetadataStatus.NOT_SCANNED.value,
        server_default=text("'NOT_SCANNED'"),
    )
    source_filename: Mapped[str | None] = mapped_column(String(255))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    source_content: Mapped[str | None] = mapped_column(Text)
    hex_color: Mapped[str | None] = mapped_column(String(7))
    width_cm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    master_resolution: Mapped[str | None] = mapped_column(String(16))
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_DOCUMENT,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @validates("warnings")
    def _validate_warnings(self, _: str, value: object) -> list[dict[str, Any]]:
        return _validate_material_metadata_warnings(value)


class InternalUser(TimestampMixin, Base):
    __tablename__ = "internal_users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('PROCESSOR', 'PRODUCTION_LEAD', 'LEADERSHIP', 'ADMIN')",
            name="ck_internal_users_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        index=True,
    )

    assigned_materials: Mapped[list["PBRMaterial"]] = relationship(
        back_populates="assigned_processor",
        passive_deletes=True,
    )
    credential: Mapped["UserCredential | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    auth_sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserCredential(Base):
    __tablename__ = "user_credentials"

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("internal_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[InternalUser] = relationship(back_populates="credential")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("length(token_hash) = 64", name="ck_auth_sessions_token_hash"),
        CheckConstraint(
            "length(csrf_token) >= 43",
            name="ck_auth_sessions_csrf_token",
        ),
        CheckConstraint(
            "absolute_expires_at > created_at",
            name="ck_auth_sessions_absolute_expiration",
        ),
        CheckConstraint(
            "idle_expires_at > created_at AND idle_expires_at <= absolute_expires_at",
            name="ck_auth_sessions_idle_expiration",
        ),
        CheckConstraint(
            "last_seen_at >= created_at",
            name="ck_auth_sessions_last_seen",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_auth_sessions_revoked_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("internal_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    user: Mapped[InternalUser] = relationship(back_populates="auth_sessions")


class AuthLoginRateLimit(Base):
    __tablename__ = "auth_login_rate_limits"
    __table_args__ = (
        CheckConstraint("length(key_hash) = 64", name="ck_auth_login_rate_limits_key_hash"),
        CheckConstraint("attempt_count > 0", name="ck_auth_login_rate_limits_attempt_count"),
    )

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_bucket: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(2048))
    vat_id: Mapped[str | None] = mapped_column(String(100))
    notion_page_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    published_brands: Mapped[list["PublishedBrand"]] = relationship(back_populates="company")
    projects: Mapped[list["Project"]] = relationship(back_populates="company")


class PublishedBrand(TimestampMixin, Base):
    __tablename__ = "published_brands"
    __table_args__ = (
        CheckConstraint(
            "next_sequence_number BETWEEN 1 AND 10000",
            name="ck_published_brands_next_sequence_number_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    folder_prefix: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    brand_identifier: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    next_sequence_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    company: Mapped[Company] = relationship(back_populates="published_brands")
    materials: Mapped[list["PBRMaterial"]] = relationship(back_populates="published_brand")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('NOT_STARTED', 'IN_PROGRESS', 'DONE')",
            name="ck_projects_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    project_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ProjectStatus.NOT_STARTED.value,
        server_default=text("'NOT_STARTED'"),
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company] = relationship(back_populates="projects")
    materials: Mapped[list["PBRMaterial"]] = relationship(back_populates="project")


class PBRMaterial(TimestampMixin, Base):
    __tablename__ = "pbr_materials"
    __table_args__ = (
        CheckConstraint(
            "sequence_number BETWEEN 1 AND 9999",
            name="ck_pbr_materials_sequence_number_range",
        ),
        CheckConstraint(
            "workflow_status IN ('IN_PROGRESS', 'DONE')",
            name="ck_pbr_materials_workflow_status",
        ),
        CheckConstraint(
            "validation_status IN "
            "('NOT_CHECKED', 'VALID', 'WARNING', 'ERROR', 'METADATA_MISSING')",
            name="ck_pbr_materials_validation_status",
        ),
        CheckConstraint(
            "publication_status IN "
            "('NOT_PUBLISHED', 'PREPARING', 'UPLOADED_WAITING_FOR_IMPORT', "
            "'WAITING_FOR_VERIFICATION', 'PUBLISHED_CURRENT', "
            "'PUBLISHED_UPDATE_REQUIRED', 'PUBLICATION_ERROR')",
            name="ck_pbr_materials_publication_status",
        ),
        UniqueConstraint(
            "published_brand_id",
            "sequence_number",
            name="uq_pbr_materials_brand_sequence_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    published_brand_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("published_brands.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    material_name: Mapped[str] = mapped_column(String(255), nullable=False)
    main_category_code: Mapped[str] = mapped_column(String(100), nullable=False)
    assigned_processor_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("internal_users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    technical_identity: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    folder_path: Mapped[str | None] = mapped_column(String(2048), unique=True)
    workflow_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MaterialWorkflowStatus.IN_PROGRESS.value,
        server_default=text("'IN_PROGRESS'"),
        index=True,
    )
    validation_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MaterialValidationStatus.NOT_CHECKED.value,
        server_default=text("'NOT_CHECKED'"),
        index=True,
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )
    publication_status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=MaterialPublicationStatus.NOT_PUBLISHED.value,
        server_default=text("'NOT_PUBLISHED'"),
        index=True,
    )

    project: Mapped[Project] = relationship(back_populates="materials")
    published_brand: Mapped[PublishedBrand] = relationship(back_populates="materials")
    assigned_processor: Mapped[InternalUser] = relationship(
        back_populates="assigned_materials"
    )
    metadata_state: Mapped["PBRMaterialMetadata"] = relationship(
        back_populates="material",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
        foreign_keys="PBRMaterialMetadata.material_id",
    )
    metadata_snapshots: Mapped[list["PBRMaterialMetadataSnapshot"]] = relationship(
        back_populates="material",
        passive_deletes=True,
        order_by="PBRMaterialMetadataSnapshot.sequence_number",
    )


class PBRMaterialMetadataSnapshot(MaterialMetadataFieldsMixin, Base):
    __tablename__ = "pbr_material_metadata_snapshots"
    __table_args__ = (
        *_material_metadata_constraints("pbr_material_metadata_snapshots"),
        CheckConstraint(
            "sequence_number > 0",
            name="ck_pbr_material_metadata_snapshots_sequence_number_positive",
        ),
        UniqueConstraint(
            "material_id",
            "sequence_number",
            name="uq_pbr_material_metadata_snapshots_material_sequence",
        ),
        UniqueConstraint(
            "material_id",
            "id",
            name="uq_pbr_material_metadata_snapshots_material_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("pbr_materials.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    material: Mapped[PBRMaterial] = relationship(back_populates="metadata_snapshots")


class ImmutableAuditSnapshotError(RuntimeError):
    """Raised when application code tries to mutate an audit snapshot."""


@event.listens_for(PBRMaterialMetadataSnapshot, "before_update")
def _reject_snapshot_update(*_: object) -> None:
    raise ImmutableAuditSnapshotError(
        "PBR material metadata snapshots are append-only and cannot be updated."
    )


@event.listens_for(PBRMaterialMetadataSnapshot, "before_delete")
def _reject_snapshot_delete(*_: object) -> None:
    raise ImmutableAuditSnapshotError(
        "PBR material metadata snapshots are append-only and cannot be deleted."
    )


class PBRMaterialMetadata(MaterialMetadataFieldsMixin, Base):
    __tablename__ = "pbr_material_metadata"
    __table_args__ = (
        *_material_metadata_constraints("pbr_material_metadata"),
        ForeignKeyConstraint(
            ["material_id", "current_snapshot_id"],
            [
                "pbr_material_metadata_snapshots.material_id",
                "pbr_material_metadata_snapshots.id",
            ],
            name="fk_pbr_material_metadata_current_snapshot",
            ondelete="RESTRICT",
        ),
    )

    material_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("pbr_materials.id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    material: Mapped[PBRMaterial] = relationship(
        back_populates="metadata_state",
        foreign_keys=[material_id],
    )


def _review_hash_constraint(column: str, name: str) -> CheckConstraint:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return CheckConstraint(f"{column} IS NULL OR (length({column}) = 64 AND {remainder} = '')", name=name)


class MaterialInventory(Base):
    __tablename__ = "material_inventories"
    __table_args__ = (
        UniqueConstraint("material_id", "id", name="uq_material_inventories_material_id"),
        CheckConstraint("generation >= 0", name="ck_material_inventories_generation"),
        _review_hash_constraint("revision_hash", "ck_material_inventories_revision_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_inventory: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    material_context: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaterialReviewState(Base):
    __tablename__ = "material_review_states"
    __table_args__ = (
        ForeignKeyConstraint(["material_id", "inventory_id"], ["material_inventories.material_id", "material_inventories.id"],
                             name="fk_material_review_states_inventory", ondelete="RESTRICT"),
        CheckConstraint("generation >= 0", name="ck_material_review_states_generation"),
        _review_hash_constraint("revision_hash", "ck_material_review_states_revision_hash"),
        CheckConstraint("(revision_hash IS NULL AND inventory_id IS NULL) OR (revision_hash IS NOT NULL AND inventory_id IS NOT NULL)",
                        name="ck_material_review_states_current_inventory"),
    )
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), default=0, nullable=False)
    revision_hash: Mapped[str | None] = mapped_column(String(64))
    inventory_id: Mapped[UUID | None] = mapped_column(Uuid)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))


class MaterialAuditEvent(Base):
    __tablename__ = "material_audit_events"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_material_audit_events_actor_request"),
        CheckConstraint("generation >= 0", name="ck_material_audit_events_generation"),
        _review_hash_constraint("revision_hash", "ck_material_audit_events_revision_hash"),
        _review_hash_constraint("request_hash", "ck_material_audit_events_request_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_hash: Mapped[str | None] = mapped_column(String(64))
    request_key: Mapped[UUID | None] = mapped_column(Uuid)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _reject_review_history_mutation(*_: object) -> None:
    raise ImmutableAuditSnapshotError("Material inventory and audit history are append-only.")


for _review_history_type in (MaterialInventory, MaterialAuditEvent):
    event.listen(_review_history_type, "before_update", _reject_review_history_mutation)
    event.listen(_review_history_type, "before_delete", _reject_review_history_mutation)
