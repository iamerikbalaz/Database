from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
