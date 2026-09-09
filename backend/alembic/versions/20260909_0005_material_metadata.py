"""Add current material metadata and immutable audit snapshots.

Revision ID: 20260909_0005
Revises: 20260908_0004
Create Date: 2026-09-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260909_0005"
down_revision: str | Sequence[str] | None = "20260908_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _strip_characters(expression: str, characters: str) -> str:
    for character in characters:
        expression = f"replace({expression}, '{character}', '')"
    return expression


SHA256_REMAINDER = _strip_characters("source_sha256", "0123456789abcdef")
HEX_REMAINDER = _strip_characters("substr(hex_color, 2)", "0123456789ABCDEF")
RESOLUTION_REMAINDER = _strip_characters(
    "substr(master_resolution, 1, length(master_resolution) - 1)",
    "0123456789",
)


def _metadata_columns() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'NOT_SCANNED'"),
            nullable=False,
        ),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_content", sa.Text(), nullable=True),
        sa.Column("hex_color", sa.String(length=7), nullable=True),
        sa.Column("width_cm", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("height_cm", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("master_resolution", sa.String(length=16), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _metadata_constraints(table_name: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            "status IN ('NOT_SCANNED', 'MISSING', 'VALID', 'WARNING', 'INVALID')",
            name=f"ck_{table_name}_status",
        ),
        sa.CheckConstraint(
            "source_filename IS NULL OR "
            "(length(source_filename) BETWEEN 1 AND 255 "
            "AND replace(source_filename, '/', '') = source_filename "
            "AND replace(source_filename, '\\', '') = source_filename)",
            name=f"ck_{table_name}_source_filename",
        ),
        sa.CheckConstraint(
            "source_sha256 IS NULL OR "
            f"(length(source_sha256) = 64 AND source_sha256 = lower(source_sha256) "
            f"AND {SHA256_REMAINDER} = '')",
            name=f"ck_{table_name}_source_sha256",
        ),
        sa.CheckConstraint(
            "hex_color IS NULL OR "
            f"(length(hex_color) = 7 AND substr(hex_color, 1, 1) = '#' "
            f"AND hex_color = upper(hex_color) AND {HEX_REMAINDER} = '')",
            name=f"ck_{table_name}_hex_color",
        ),
        sa.CheckConstraint(
            "width_cm IS NULL OR width_cm > 0",
            name=f"ck_{table_name}_width_cm_positive",
        ),
        sa.CheckConstraint(
            "height_cm IS NULL OR height_cm > 0",
            name=f"ck_{table_name}_height_cm_positive",
        ),
        sa.CheckConstraint(
            "master_resolution IS NULL OR "
            "(length(master_resolution) BETWEEN 2 AND 16 "
            "AND substr(master_resolution, length(master_resolution), 1) = 'K' "
            "AND substr(master_resolution, 1, 1) IN "
            "('1', '2', '3', '4', '5', '6', '7', '8', '9') "
            f"AND {RESOLUTION_REMAINDER} = '')",
            name=f"ck_{table_name}_master_resolution",
        ),
        sa.CheckConstraint(
            "pbr_material_metadata_warnings_are_valid(warnings)",
            name=f"ck_{table_name}_warnings",
        ),
    ]


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION pbr_material_metadata_warnings_are_valid(candidate jsonb)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        SET search_path = pg_catalog
        AS $function$
            SELECT jsonb_typeof(candidate) = 'array'
                AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        CASE
                            WHEN jsonb_typeof(candidate) = 'array' THEN candidate
                            ELSE '[]'::jsonb
                        END
                    ) AS entry(value)
                    WHERE jsonb_typeof(value) IS DISTINCT FROM 'object'
                        OR EXISTS (
                            SELECT 1
                            FROM jsonb_object_keys(
                                CASE
                                    WHEN jsonb_typeof(value) = 'object' THEN value
                                    ELSE '{}'::jsonb
                                END
                            ) AS property(name)
                            WHERE name NOT IN ('code', 'message', 'path')
                        )
                        OR jsonb_typeof(value -> 'code') IS DISTINCT FROM 'string'
                        OR length(value ->> 'code') NOT BETWEEN 1 AND 100
                        OR (value ->> 'code') !~ '[^[:space:]]'
                        OR jsonb_typeof(value -> 'message') IS DISTINCT FROM 'string'
                        OR (value ->> 'message') !~ '[^[:space:]]'
                        OR (
                            value ? 'path'
                            AND value -> 'path' <> 'null'::jsonb
                            AND (
                                jsonb_typeof(value -> 'path') IS DISTINCT FROM 'string'
                                OR length(value ->> 'path') NOT BETWEEN 1 AND 2048
                                OR (value ->> 'path') !~ '[^[:space:]]'
                            )
                        )
                )
        $function$
        """
    )

    op.create_table(
        "pbr_material_metadata_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        *_metadata_columns(),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *_metadata_constraints("pbr_material_metadata_snapshots"),
        sa.CheckConstraint(
            "sequence_number > 0",
            name="ck_pbr_material_metadata_snapshots_sequence_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["pbr_materials.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "material_id",
            "sequence_number",
            name="uq_pbr_material_metadata_snapshots_material_sequence",
        ),
        sa.UniqueConstraint(
            "material_id",
            "id",
            name="uq_pbr_material_metadata_snapshots_material_id",
        ),
    )
    op.create_index(
        op.f("ix_pbr_material_metadata_snapshots_material_id"),
        "pbr_material_metadata_snapshots",
        ["material_id"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION pbr_material_metadata_snapshots_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog
        AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = format(
                    'pbr_material_metadata_snapshots is append-only; %s is forbidden',
                    TG_OP
                );
            RETURN NULL;
        END
        $function$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_pbr_material_metadata_snapshots_reject_update_delete
        BEFORE UPDATE OR DELETE ON pbr_material_metadata_snapshots
        FOR EACH ROW
        EXECUTE FUNCTION pbr_material_metadata_snapshots_reject_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_pbr_material_metadata_snapshots_reject_truncate
        BEFORE TRUNCATE ON pbr_material_metadata_snapshots
        FOR EACH STATEMENT
        EXECUTE FUNCTION pbr_material_metadata_snapshots_reject_mutation()
        """
    )

    op.create_table(
        "pbr_material_metadata",
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("current_snapshot_id", sa.Uuid(), nullable=True),
        *_metadata_columns(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *_metadata_constraints("pbr_material_metadata"),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["pbr_materials.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id", "current_snapshot_id"],
            [
                "pbr_material_metadata_snapshots.material_id",
                "pbr_material_metadata_snapshots.id",
            ],
            name="fk_pbr_material_metadata_current_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("material_id"),
    )

    op.execute(
        "INSERT INTO pbr_material_metadata (material_id) "
        "SELECT id FROM pbr_materials"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_pbr_material_metadata_snapshots_reject_truncate "
        "ON pbr_material_metadata_snapshots"
    )
    op.execute(
        "DROP TRIGGER trg_pbr_material_metadata_snapshots_reject_update_delete "
        "ON pbr_material_metadata_snapshots"
    )
    op.execute("DROP FUNCTION pbr_material_metadata_snapshots_reject_mutation()")
    op.drop_constraint(
        "ck_pbr_material_metadata_warnings",
        "pbr_material_metadata",
        type_="check",
    )
    op.drop_constraint(
        "ck_pbr_material_metadata_snapshots_warnings",
        "pbr_material_metadata_snapshots",
        type_="check",
    )
    op.execute("DROP FUNCTION pbr_material_metadata_warnings_are_valid(jsonb)")
    op.drop_table("pbr_material_metadata")
    op.drop_index(
        op.f("ix_pbr_material_metadata_snapshots_material_id"),
        table_name="pbr_material_metadata_snapshots",
    )
    op.drop_table("pbr_material_metadata_snapshots")
