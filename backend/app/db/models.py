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
    Index,
    JSON,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    inspect as sa_inspect,
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
        ForeignKeyConstraint(["material_id", "technical_check_id", "generation", "revision_hash"],
                             ["material_technical_checks.material_id", "material_technical_checks.id",
                              "material_technical_checks.generation", "material_technical_checks.revision_hash"],
                             name="fk_material_review_states_technical", ondelete="RESTRICT"),
        CheckConstraint("generation >= 0", name="ck_material_review_states_generation"),
        _review_hash_constraint("revision_hash", "ck_material_review_states_revision_hash"),
        CheckConstraint("(revision_hash IS NULL AND inventory_id IS NULL) OR (revision_hash IS NOT NULL AND inventory_id IS NOT NULL)",
                        name="ck_material_review_states_current_inventory"),
    )
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), default=0, nullable=False)
    revision_hash: Mapped[str | None] = mapped_column(String(64))
    inventory_id: Mapped[UUID | None] = mapped_column(Uuid)
    technical_check_id: Mapped[UUID | None] = mapped_column(Uuid)
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


class MaterialTechnicalCheck(Base):
    __tablename__ = "material_technical_checks"
    __table_args__ = (
        UniqueConstraint("material_id", "id", "generation", "revision_hash", name="uq_material_technical_checks_revision"),
        ForeignKeyConstraint(["material_id", "inventory_id"], ["material_inventories.material_id", "material_inventories.id"],
                             name="fk_material_technical_checks_inventory", ondelete="RESTRICT"),
        CheckConstraint("generation >= 0", name="ck_material_technical_checks_generation"),
        _review_hash_constraint("revision_hash", "ck_material_technical_checks_revision_hash"),
        _review_hash_constraint("report_hash", "ck_material_technical_checks_report_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    inventory_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaterialApproval(Base):
    __tablename__ = "material_approvals"
    __table_args__ = (
        UniqueConstraint("material_id", "id", name="uq_material_approvals_material_id"),
        UniqueConstraint("material_id", "generation", "revision_hash", "kind", name="uq_material_approvals_revision_kind"),
        ForeignKeyConstraint(["material_id", "technical_check_id", "generation", "revision_hash"],
                             ["material_technical_checks.material_id", "material_technical_checks.id",
                              "material_technical_checks.generation", "material_technical_checks.revision_hash"],
                             name="fk_material_approvals_check", ondelete="RESTRICT"),
        ForeignKeyConstraint(["material_id", "technical_approval_id"], ["material_approvals.material_id", "material_approvals.id"],
                             name="fk_material_approvals_technical", ondelete="RESTRICT"),
        CheckConstraint("generation >= 0", name="ck_material_approvals_generation"),
        _review_hash_constraint("revision_hash", "ck_material_approvals_revision_hash"),
        CheckConstraint("(kind = 'TECHNICAL' AND technical_approval_id IS NULL) OR (kind = 'PUBLICATION' AND technical_approval_id IS NOT NULL)",
                        name="ck_material_approvals_kind"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    technical_check_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    technical_approval_id: Mapped[UUID | None] = mapped_column(Uuid)
    note: Mapped[str | None] = mapped_column(Text)
    warnings_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaterialFileOperation(TimestampMixin, Base):
    __tablename__ = "material_file_operations"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_material_file_operations_actor_request"),
        CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'ROLLED_BACK', 'RECOVERY_REQUIRED', 'REJECTED')", name="ck_material_file_operations_status"),
        _review_hash_constraint("request_hash", "ck_material_file_operations_request_hash"),
        _review_hash_constraint("proposal_hash", "ck_material_file_operations_proposal_hash"),
        Index("uq_material_file_operations_active", "material_id", unique=True,
              postgresql_where=text("status IN ('RUNNING', 'RECOVERY_REQUIRED')"),
              sqlite_where=text("status IN ('RUNNING', 'RECOVERY_REQUIRED')")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    source_brand_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"))
    target_brand_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    source_context: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    target_context: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    worker_plan: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))


class MaterialNumberReservation(Base):
    __tablename__ = "material_number_reservations"
    __table_args__ = (CheckConstraint("sequence_number BETWEEN 1 AND 9999", name="ck_material_number_reservations_sequence"),)
    brand_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"), primary_key=True)
    sequence_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    operation_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("material_file_operations.id", ondelete="RESTRICT"))
    actor_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaterialIdentityHistory(Base):
    __tablename__ = "material_identity_history"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    operation_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("material_file_operations.id", ondelete="RESTRICT"), unique=True)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    old_context: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    new_context: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _reject_review_history_mutation(*_: object) -> None:
    raise ImmutableAuditSnapshotError("Material inventory and audit history are append-only.")


class OnlineCategory(TimestampMixin, Base):
    __tablename__ = "online_categories"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_online_categories_version"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(765), unique=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)


class BrandCollection(TimestampMixin, Base):
    __tablename__ = "brand_collections"
    __table_args__ = (
        UniqueConstraint("brand_id", "normalized_key", name="uq_brand_collections_brand_key"),
        CheckConstraint("version >= 1", name="ck_brand_collections_version"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    brand_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"), index=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(765), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)


class CatalogAuditEvent(Base):
    __tablename__ = "catalog_audit_events"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_catalog_audit_events_actor_request"),
        _review_hash_constraint("request_hash", "ck_catalog_audit_events_request_hash"),
        CheckConstraint("resource_kind IN ('CATEGORY', 'COLLECTION')", name="ck_catalog_audit_events_kind"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    resource_id: Mapped[UUID] = mapped_column(Uuid, index=True, nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaterialContent(Base):
    __tablename__ = "material_content"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_material_content_revision"),
        CheckConstraint("credits IS NULL OR credits BETWEEN 0 AND 2147483647", name="ck_material_content_credits"),
    )
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    credits: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)


class MaterialOnlineCategory(Base):
    __tablename__ = "material_online_categories"
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True)
    category_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("online_categories.id", ondelete="RESTRICT"), primary_key=True)


class MaterialCollection(Base):
    __tablename__ = "material_collections"
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True)
    collection_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("brand_collections.id", ondelete="RESTRICT"), primary_key=True)


class MaterialContentRevision(Base):
    __tablename__ = "material_content_revisions"
    __table_args__ = (
        UniqueConstraint("material_id", "revision", name="uq_material_content_revisions_revision"),
        CheckConstraint("revision >= 1", name="ck_material_content_revisions_revision"),
        _review_hash_constraint("snapshot_hash", "ck_material_content_revisions_snapshot_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaterialContentApproval(Base):
    __tablename__ = "material_content_approvals"
    __table_args__ = (
        ForeignKeyConstraint(["material_id", "content_revision"],
            ["material_content_revisions.material_id", "material_content_revisions.revision"],
            name="fk_material_content_approvals_revision", ondelete="RESTRICT"),
        UniqueConstraint("material_id", "context_hash", name="uq_material_content_approvals_context"),
        CheckConstraint("content_revision >= 1", name="ck_material_content_approvals_revision"),
        _review_hash_constraint("context_hash", "ck_material_content_approvals_context_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    content_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    warnings_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaterialPackagingPolicy(Base):
    __tablename__ = "material_packaging_policies"
    __table_args__ = (
        UniqueConstraint("material_id", "id", name="uq_material_packaging_policies_material_id"),
        UniqueConstraint("material_id", "revision", name="uq_material_packaging_policies_revision"),
        ForeignKeyConstraint(["material_id", "previous_id"], ["material_packaging_policies.material_id", "material_packaging_policies.id"],
            name="fk_material_packaging_policies_previous", ondelete="RESTRICT"),
        ForeignKeyConstraint(["material_id", "inventory_id"], ["material_inventories.material_id", "material_inventories.id"],
            name="fk_material_packaging_policies_inventory", ondelete="RESTRICT"),
        CheckConstraint("revision >= 1", name="ck_material_packaging_policies_revision"),
        CheckConstraint("policy IN ('LEGACY_BEFORE_2026_03_04', 'CURRENT_ON_OR_AFTER_2026_03_04')", name="ck_material_packaging_policies_policy"),
        CheckConstraint("(revision = 1 AND previous_id IS NULL AND inventory_id IS NOT NULL) OR (revision > 1 AND previous_id IS NOT NULL AND inventory_id IS NULL)", name="ck_material_packaging_policies_origin"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_material_packaging_policies_reason"),
        CheckConstraint("length(storage_timezone) BETWEEN 1 AND 100", name="ck_material_packaging_policies_timezone"),
        _review_hash_constraint("evidence_hash", "ck_material_packaging_policies_evidence_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_id: Mapped[UUID | None] = mapped_column(Uuid)
    inventory_id: Mapped[UUID | None] = mapped_column(Uuid)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    policy: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PublicationBatch(Base):
    __tablename__ = "publication_batches"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_publication_batches_actor_request"),
        CheckConstraint("row_count BETWEEN 1 AND 100", name="ck_publication_batches_row_count"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_publication_batches_reason"),
        CheckConstraint("length(csv_bytes) BETWEEN 3 AND 33554432", name="ck_publication_batches_csv_size"),
        *(_review_hash_constraint(field, "ck_publication_batches_" + field) for field in ("request_hash", "snapshot_hash", "csv_sha256")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csv_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    csv_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False)
    warnings: Mapped[list] = mapped_column(_JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class PublicationBatchItem(Base):
    __tablename__ = "publication_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "ordinal", name="uq_publication_batch_items_ordinal"),
        CheckConstraint("ordinal BETWEEN 1 AND 100", name="ck_publication_batch_items_ordinal"),
        CheckConstraint("generation >= 0", name="ck_publication_batch_items_generation"),
        *(_review_hash_constraint(field, "ck_publication_batch_items_" + field) for field in ("revision_hash", "content_context_hash", "snapshot_hash")),
        ForeignKeyConstraint(["material_id", "technical_check_id", "generation", "revision_hash"],
            ["material_technical_checks.material_id", "material_technical_checks.id", "material_technical_checks.generation", "material_technical_checks.revision_hash"],
            name="fk_publication_batch_items_check", ondelete="RESTRICT"),
        ForeignKeyConstraint(["material_id", "metadata_snapshot_id"], ["pbr_material_metadata_snapshots.material_id", "pbr_material_metadata_snapshots.id"],
            name="fk_publication_batch_items_metadata", ondelete="RESTRICT"),
        ForeignKeyConstraint(["material_id", "technical_approval_id"], ["material_approvals.material_id", "material_approvals.id"],
            name="fk_publication_batch_items_technical", ondelete="RESTRICT"),
        ForeignKeyConstraint(["material_id", "publication_approval_id"], ["material_approvals.material_id", "material_approvals.id"],
            name="fk_publication_batch_items_publication", ondelete="RESTRICT"),
        ForeignKeyConstraint(["material_id", "content_context_hash"], ["material_content_approvals.material_id", "material_content_approvals.context_hash"],
            name="fk_publication_batch_items_content", ondelete="RESTRICT"),
    )
    batch_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("publication_batches.id", ondelete="RESTRICT"), primary_key=True)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    technical_check_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    metadata_snapshot_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    technical_approval_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    publication_approval_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    snapshot: Mapped[dict] = mapped_column(_JSON_DOCUMENT, nullable=False)


class MaterialPackagingExecution(Base):
    __tablename__ = "material_packaging_executions"
    __table_args__ = (
        UniqueConstraint("material_id", "id", name="uq_packaging_executions_material"),
        UniqueConstraint("actor_id", "request_key", name="uq_packaging_executions_request"),
        ForeignKeyConstraint(["batch_id", "material_id"], ["publication_batch_items.batch_id", "publication_batch_items.material_id"],
            name="fk_packaging_executions_batch_item", ondelete="RESTRICT"),
        ForeignKeyConstraint(["material_id", "policy_id"], ["material_packaging_policies.material_id", "material_packaging_policies.id"],
            name="fk_packaging_executions_policy", ondelete="RESTRICT"),
        CheckConstraint("length(folder_path) BETWEEN 1 AND 2048", name="ck_packaging_executions_folder"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_packaging_executions_reason"),
        *(_review_hash_constraint(field, "ck_packaging_executions_" + field) for field in ("request_hash", "input_hash", "worker_request_hash")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    batch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    brand_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"), index=True)
    policy_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    issuer_session_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    folder_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(_JSON_DOCUMENT, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_request: Mapped[dict] = mapped_column(_JSON_DOCUMENT, nullable=False)
    worker_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class MaterialPackagingDispatch(Base):
    __tablename__ = "material_packaging_dispatches"
    __table_args__ = (
        UniqueConstraint("execution_id", "id", name="uq_packaging_dispatches_execution"),
        UniqueConstraint("execution_id", "ordinal", name="uq_packaging_dispatches_ordinal"),
        UniqueConstraint("actor_id", "request_key", name="uq_packaging_dispatches_request"),
        CheckConstraint("ordinal >= 1", name="ck_packaging_dispatches_ordinal"),
        CheckConstraint("action IN ('EXECUTE', 'RETRY', 'RECONCILE', 'CLOSE')", name="ck_packaging_dispatches_action"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_packaging_dispatches_reason"),
        _review_hash_constraint("request_hash", "ck_packaging_dispatches_request_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    execution_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("material_packaging_executions.id", ondelete="RESTRICT"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    issuer_session_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    request_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaterialPackagingObservation(Base):
    __tablename__ = "material_packaging_observations"
    __table_args__ = (
        UniqueConstraint("execution_id", "id", name="uq_packaging_observations_execution"),
        UniqueConstraint("dispatch_id", name="uq_packaging_observations_dispatch"),
        ForeignKeyConstraint(["execution_id", "dispatch_id"], ["material_packaging_dispatches.execution_id", "material_packaging_dispatches.id"],
            name="fk_packaging_observations_dispatch", ondelete="RESTRICT"),
        CheckConstraint("outcome IN ('READY', 'RETRY_REQUIRED', 'UNCERTAIN', 'NOT_STARTED')", name="ck_packaging_observations_outcome"),
        CheckConstraint("(outcome = 'UNCERTAIN' AND failure_code IS NOT NULL AND worker_result IS NULL) OR "
            "(outcome IN ('READY', 'RETRY_REQUIRED') AND failure_code IS NULL AND worker_result IS NOT NULL) OR "
            "(outcome = 'NOT_STARTED' AND failure_code IS NULL AND worker_result IS NULL AND NOT inputs_current AND NOT actor_current)",
            name="ck_packaging_observations_result"),
        CheckConstraint("(outcome = 'READY' AND proof_sha256 IS NOT NULL) OR (outcome <> 'READY' AND proof_sha256 IS NULL)",
            name="ck_packaging_observations_proof"),
        _review_hash_constraint("proof_sha256", "ck_packaging_observations_proof_sha256"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    execution_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("material_packaging_executions.id", ondelete="RESTRICT"))
    dispatch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    worker_result: Mapped[dict | None] = mapped_column(JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    proof_sha256: Mapped[str | None] = mapped_column(String(64))
    inputs_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


PACKAGING_ACTIVE_STATUSES = ("RESERVED", "RUNNING", "RETRY_REQUIRED", "RECOVERY_REQUIRED")


class MaterialPackagingState(TimestampMixin, Base):
    __tablename__ = "material_packaging_states"
    __table_args__ = (
        ForeignKeyConstraint(["material_id", "execution_id"], ["material_packaging_executions.material_id", "material_packaging_executions.id"],
            name="fk_packaging_states_execution", ondelete="RESTRICT"),
        ForeignKeyConstraint(["execution_id", "last_dispatch_id"], ["material_packaging_dispatches.execution_id", "material_packaging_dispatches.id"],
            name="fk_packaging_states_dispatch", ondelete="RESTRICT"),
        ForeignKeyConstraint(["execution_id", "last_observation_id"], ["material_packaging_observations.execution_id", "material_packaging_observations.id"],
            name="fk_packaging_states_observation", ondelete="RESTRICT"),
        CheckConstraint("status IN ('RESERVED', 'RUNNING', 'RETRY_REQUIRED', 'RECOVERY_REQUIRED', 'PACKAGED', 'REJECTED')",
            name="ck_packaging_states_status"),
        CheckConstraint("(status = 'RESERVED' AND last_dispatch_id IS NULL AND last_observation_id IS NULL) OR "
            "(status = 'RUNNING' AND last_dispatch_id IS NOT NULL AND last_observation_id IS NULL) OR "
            "(status NOT IN ('RESERVED', 'RUNNING') AND last_dispatch_id IS NOT NULL AND last_observation_id IS NOT NULL)",
            name="ck_packaging_states_progress"),
        Index("uq_packaging_states_active", "material_id", unique=True,
            postgresql_where=text("status IN ('RESERVED', 'RUNNING', 'RETRY_REQUIRED', 'RECOVERY_REQUIRED')"),
            sqlite_where=text("status IN ('RESERVED', 'RUNNING', 'RETRY_REQUIRED', 'RECOVERY_REQUIRED')")),
    )
    execution_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    material_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    last_dispatch_id: Mapped[UUID | None] = mapped_column(Uuid)
    last_observation_id: Mapped[UUID | None] = mapped_column(Uuid)


@event.listens_for(MaterialPackagingState, "before_update")
def _protect_packaging_state_identity(_, __, item):
    if any(sa_inspect(item).attrs[field].history.has_changes() for field in ("execution_id", "material_id", "created_at")):
        raise ImmutableAuditSnapshotError("Packaging ownership identity is immutable.")


event.listen(MaterialPackagingState, "before_delete", _reject_review_history_mutation)


class PublicationStagingJob(Base):
    """Immutable internal staging reservation; cloud dispatch is a later phase."""
    __tablename__ = "publication_staging_jobs"
    __table_args__ = (
        UniqueConstraint("id", "batch_id", name="uq_staging_jobs_batch"),
        UniqueConstraint("actor_id", "request_key", name="uq_staging_jobs_request"),
        CheckConstraint("material_count BETWEEN 1 AND 100", name="ck_staging_jobs_count"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_staging_jobs_reason"),
        CheckConstraint("length(bucket_name) BETWEEN 3 AND 63", name="ck_staging_jobs_bucket"),
        CheckConstraint("length(staging_prefix) BETWEEN 1 AND 128", name="ck_staging_jobs_prefix"),
        *(_review_hash_constraint(field, "ck_staging_jobs_" + field)
          for field in ("request_hash", "plan_sha256")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("publication_batches.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    issuer_session_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    material_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket_name: Mapped[str] = mapped_column(String(63), nullable=False)
    staging_prefix: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan: Mapped[dict] = mapped_column(_JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class PublicationStagingItem(Base):
    __tablename__ = "publication_staging_items"
    __table_args__ = (
        ForeignKeyConstraint(["job_id", "batch_id"], ["publication_staging_jobs.id", "publication_staging_jobs.batch_id"],
            name="fk_staging_items_job", ondelete="RESTRICT"),
        ForeignKeyConstraint(["batch_id", "material_id"], ["publication_batch_items.batch_id", "publication_batch_items.material_id"],
            name="fk_staging_items_batch", ondelete="RESTRICT"),
        ForeignKeyConstraint(["material_id", "execution_id"], ["material_packaging_executions.material_id", "material_packaging_executions.id"],
            name="fk_staging_items_execution", ondelete="RESTRICT"),
        ForeignKeyConstraint(["execution_id", "observation_id"], ["material_packaging_observations.execution_id", "material_packaging_observations.id"],
            name="fk_staging_items_observation", ondelete="RESTRICT"),
        CheckConstraint("length(folder_path) BETWEEN 1 AND 2048", name="ck_staging_items_folder"),
        *(_review_hash_constraint(field, "ck_staging_items_" + field)
          for field in ("input_hash", "worker_request_hash", "packaging_proof_sha256")),
    )
    job_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    material_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    execution_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    observation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    brand_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("published_brands.id", ondelete="RESTRICT"), index=True)
    folder_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    packaging_proof_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class PublicationStagingClose(Base):
    __tablename__ = "publication_staging_closes"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_staging_closes_job"),
        UniqueConstraint("job_id", "id", name="uq_staging_closes_binding"),
        UniqueConstraint("actor_id", "request_key", name="uq_staging_closes_request"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_staging_closes_reason"),
        _review_hash_constraint("request_hash", "ck_staging_closes_request_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("publication_staging_jobs.id", ondelete="RESTRICT"))
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    issuer_session_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PublicationStagingOwner(TimestampMixin, Base):
    __tablename__ = "publication_staging_owners"
    __table_args__ = (
        ForeignKeyConstraint(["job_id", "material_id"], ["publication_staging_items.job_id", "publication_staging_items.material_id"],
            name="fk_staging_owners_item", ondelete="RESTRICT"),
        ForeignKeyConstraint(["job_id", "close_id"], ["publication_staging_closes.job_id", "publication_staging_closes.id"],
            name="fk_staging_owners_close", ondelete="RESTRICT"),
        CheckConstraint("(active AND close_id IS NULL) OR (NOT active AND close_id IS NOT NULL)", name="ck_staging_owners_state"),
        Index("uq_staging_owners_active", "material_id", unique=True,
            postgresql_where=text("active"), sqlite_where=text("active")),
    )
    job_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    material_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    close_id: Mapped[UUID | None] = mapped_column(Uuid)


@event.listens_for(PublicationStagingOwner, "before_update")
def _protect_staging_owner(_, __, item):
    state = sa_inspect(item)
    if (any(state.attrs[field].history.has_changes() for field in ("job_id", "material_id", "created_at"))
            or False in state.attrs.active.history.deleted
            or (item.active is False and not state.attrs.active.history.has_changes())):
        raise ImmutableAuditSnapshotError("Staging ownership identity and released state are immutable.")


event.listen(PublicationStagingOwner, "before_delete", _reject_review_history_mutation)


class MaterialImportBatch(Base):
    __tablename__ = "material_import_batches"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_material_import_batches_actor_request"),
        CheckConstraint("row_count BETWEEN 1 AND 2000", name="ck_material_import_batches_row_count"),
        CheckConstraint("source_format IN ('CSV', 'XLSX')", name="ck_material_import_batches_format"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_material_import_batches_reason"),
        _review_hash_constraint("source_sha256", "ck_material_import_batches_source_sha256"),
        _review_hash_constraint("request_hash", "ck_material_import_batches_request_hash"),
        _review_hash_constraint("preview_hash", "ck_material_import_batches_preview_hash"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    request_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_format: Mapped[str] = mapped_column(String(4), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(_JSON_DOCUMENT, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class MaterialImportRow(Base):
    __tablename__ = "material_import_rows"
    __table_args__ = (CheckConstraint("source_row BETWEEN 1 AND 4194304", name="ck_material_import_rows_source_row"),)
    batch_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("material_import_batches.id", ondelete="RESTRICT"), primary_key=True)
    source_row: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), unique=True)
    snapshot: Mapped[dict] = mapped_column(_JSON_DOCUMENT, nullable=False)


class MaterialSourceLink(Base):
    __tablename__ = "material_source_links"
    __table_args__ = (
        UniqueConstraint("material_id", "url", name="uq_material_source_links_url"),
        CheckConstraint("version >= 1", name="ck_material_source_links_version"),
        CheckConstraint("length(url) BETWEEN 1 AND 2048", name="ck_material_source_links_url"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AiServiceCredential(Base):
    __tablename__ = "ai_service_credentials"
    __table_args__ = (
        _review_hash_constraint("token_hash", "ck_ai_service_credentials_token_hash"),
        CheckConstraint("expires_at > created_at", name="ck_ai_service_credentials_expiration"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="ck_ai_service_credentials_revocation"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    # A deleted issuing session invalidates access. Do not force retention of
    # expired human session secrets merely to preserve this public audit reference.
    issuer_session_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaterialAiDraft(Base):
    __tablename__ = "material_ai_drafts"
    __table_args__ = (
        CheckConstraint("content_revision >= 0", name="ck_material_ai_drafts_revision"),
        _review_hash_constraint("context_hash", "ck_material_ai_drafts_context_hash"),
        CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_material_ai_drafts_reason"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    material_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pbr_materials.id", ondelete="RESTRICT"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("internal_users.id", ondelete="RESTRICT"))
    service_credential_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("ai_service_credentials.id", ondelete="RESTRICT"))
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    context: Mapped[dict] = mapped_column(_JSON_DOCUMENT, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(_JSON_DOCUMENT, nullable=False)
    source_link_ids: Mapped[list] = mapped_column(_JSON_DOCUMENT, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


@event.listens_for(AiServiceCredential, "before_update")
def _protect_ai_credential(_, __, item):
    attributes = sa_inspect(item).attrs
    if any(attributes[field].history.has_changes() for field in ("id", "material_id", "actor_id", "issuer_session_id", "token_hash", "created_at", "expires_at")):
        raise ValueError("AI credential scope is immutable")
    history = attributes.revoked_at.history
    if history.has_changes() and (item.revoked_at is None or any(value is not None for value in history.deleted)):
        raise ValueError("AI credential revocation is permanent")


event.listen(AiServiceCredential, "before_delete", _reject_review_history_mutation)


def _protect_source_link(_mapper, _connection, item):
    if any(sa_inspect(item).attrs[field].history.has_changes() for field in ("id", "material_id", "actor_id", "url", "created_at")):
        raise ImmutableAuditSnapshotError("Source URL identity is immutable; deactivate and create another reference.")


event.listen(MaterialSourceLink, "before_update", _protect_source_link)
event.listen(MaterialSourceLink, "before_delete", _reject_review_history_mutation)


def _protect_catalog_identity(_mapper, _connection, item):
    fields = ("id", "value", "normalized_key", "created_at") + (("brand_id",) if isinstance(item, BrandCollection) else ())
    if any(sa_inspect(item).attrs[field].history.has_changes() for field in fields):
        raise ImmutableAuditSnapshotError("Catalog identity is immutable; deactivate and create a new value.")


for _catalog_type in (OnlineCategory, BrandCollection):
    event.listen(_catalog_type, "before_update", _protect_catalog_identity)
    event.listen(_catalog_type, "before_delete", _reject_review_history_mutation)


for _review_history_type in (MaterialInventory, MaterialAuditEvent, MaterialTechnicalCheck, MaterialApproval, MaterialNumberReservation, MaterialIdentityHistory, CatalogAuditEvent, MaterialContentRevision, MaterialContentApproval, MaterialImportBatch, MaterialImportRow, MaterialAiDraft, PublicationBatch, PublicationBatchItem, MaterialPackagingPolicy, MaterialPackagingExecution, MaterialPackagingDispatch, MaterialPackagingObservation, PublicationStagingJob, PublicationStagingItem, PublicationStagingClose):
    event.listen(_review_history_type, "before_update", _reject_review_history_mutation)
    event.listen(_review_history_type, "before_delete", _reject_review_history_mutation)
