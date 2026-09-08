"""Add internal users and the PBR material core.

Revision ID: 20260908_0004
Revises: 20260907_0003
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260908_0004"
down_revision: str | Sequence[str] | None = "20260907_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_published_brands_next_sequence_number_range",
        "published_brands",
        type_="check",
    )
    op.create_check_constraint(
        "ck_published_brands_next_sequence_number_range",
        "published_brands",
        "next_sequence_number BETWEEN 1 AND 10000",
    )

    op.create_table(
        "internal_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('PROCESSOR', 'PRODUCTION_LEAD', 'LEADERSHIP', 'ADMIN')",
            name="ck_internal_users_role",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_internal_users_email"),
    )
    op.create_index(
        op.f("ix_internal_users_is_active"),
        "internal_users",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_internal_users_role"),
        "internal_users",
        ["role"],
        unique=False,
    )

    op.create_table(
        "pbr_materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("published_brand_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("material_name", sa.String(length=255), nullable=False),
        sa.Column("main_category_code", sa.String(length=100), nullable=False),
        sa.Column("assigned_processor_id", sa.Uuid(), nullable=False),
        sa.Column("technical_identity", sa.String(length=512), nullable=False),
        sa.Column("folder_path", sa.String(length=2048), nullable=True),
        sa.Column(
            "workflow_status",
            sa.String(length=32),
            server_default=sa.text("'IN_PROGRESS'"),
            nullable=False,
        ),
        sa.Column(
            "validation_status",
            sa.String(length=32),
            server_default=sa.text("'NOT_CHECKED'"),
            nullable=False,
        ),
        sa.Column(
            "is_published",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "publication_status",
            sa.String(length=40),
            server_default=sa.text("'NOT_PUBLISHED'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence_number BETWEEN 1 AND 9999",
            name="ck_pbr_materials_sequence_number_range",
        ),
        sa.CheckConstraint(
            "workflow_status IN ('IN_PROGRESS', 'DONE')",
            name="ck_pbr_materials_workflow_status",
        ),
        sa.CheckConstraint(
            "validation_status IN "
            "('NOT_CHECKED', 'VALID', 'WARNING', 'ERROR', 'METADATA_MISSING')",
            name="ck_pbr_materials_validation_status",
        ),
        sa.CheckConstraint(
            "publication_status IN "
            "('NOT_PUBLISHED', 'PREPARING', 'UPLOADED_WAITING_FOR_IMPORT', "
            "'WAITING_FOR_VERIFICATION', 'PUBLISHED_CURRENT', "
            "'PUBLISHED_UPDATE_REQUIRED', 'PUBLICATION_ERROR')",
            name="ck_pbr_materials_publication_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["assigned_processor_id"],
            ["internal_users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_brand_id"],
            ["published_brands.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("folder_path", name="uq_pbr_materials_folder_path"),
        sa.UniqueConstraint("technical_identity", name="uq_pbr_materials_technical_identity"),
        sa.UniqueConstraint(
            "published_brand_id",
            "sequence_number",
            name="uq_pbr_materials_brand_sequence_number",
        ),
    )
    op.create_index(
        op.f("ix_pbr_materials_assigned_processor_id"),
        "pbr_materials",
        ["assigned_processor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pbr_materials_is_published"),
        "pbr_materials",
        ["is_published"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pbr_materials_project_id"),
        "pbr_materials",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pbr_materials_publication_status"),
        "pbr_materials",
        ["publication_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pbr_materials_published_brand_id"),
        "pbr_materials",
        ["published_brand_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pbr_materials_validation_status"),
        "pbr_materials",
        ["validation_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pbr_materials_workflow_status"),
        "pbr_materials",
        ["workflow_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_pbr_materials_workflow_status"), table_name="pbr_materials")
    op.drop_index(op.f("ix_pbr_materials_validation_status"), table_name="pbr_materials")
    op.drop_index(op.f("ix_pbr_materials_published_brand_id"), table_name="pbr_materials")
    op.drop_index(op.f("ix_pbr_materials_publication_status"), table_name="pbr_materials")
    op.drop_index(op.f("ix_pbr_materials_project_id"), table_name="pbr_materials")
    op.drop_index(op.f("ix_pbr_materials_is_published"), table_name="pbr_materials")
    op.drop_index(op.f("ix_pbr_materials_assigned_processor_id"), table_name="pbr_materials")
    op.drop_table("pbr_materials")
    op.drop_index(op.f("ix_internal_users_role"), table_name="internal_users")
    op.drop_index(op.f("ix_internal_users_is_active"), table_name="internal_users")
    op.drop_table("internal_users")

    op.drop_constraint(
        "ck_published_brands_next_sequence_number_range",
        "published_brands",
        type_="check",
    )
    op.execute(
        "UPDATE published_brands SET next_sequence_number = 9999 "
        "WHERE next_sequence_number = 10000"
    )
    op.create_check_constraint(
        "ck_published_brands_next_sequence_number_range",
        "published_brands",
        "next_sequence_number BETWEEN 1 AND 9999",
    )
