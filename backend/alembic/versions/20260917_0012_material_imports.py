"""Immutable historical import batches and exact created-row provenance."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260917_0012"
down_revision = "20260916_0011"
branch_labels = None
depends_on = None


def _hash(field):
    remainder = field
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"length({field}) = 64 AND {remainder} = ''", name=f"ck_material_import_batches_{field}")


def upgrade():
    op.create_table("material_import_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("source_format", sa.String(4), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_material_import_batches_actor_request"),
        sa.CheckConstraint("row_count BETWEEN 1 AND 2000", name="ck_material_import_batches_row_count"),
        sa.CheckConstraint("source_format IN ('CSV', 'XLSX')", name="ck_material_import_batches_format"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_material_import_batches_reason"),
        _hash("source_sha256"), _hash("request_hash"), _hash("preview_hash"))
    op.create_index("ix_material_import_batches_created_at", "material_import_batches", ["created_at"])
    op.create_table("material_import_rows",
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("material_import_batches.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("source_row", sa.Integer(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("source_row BETWEEN 1 AND 4194304", name="ck_material_import_rows_source_row"))
    for table in ("material_import_batches", "material_import_rows"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE "
                   f"ON {table} FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    # Once an import exists its provenance is part of the database record. A
    # routine rollback must not silently discard that permanent audit history.
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM material_import_batches) THEN "
               "RAISE EXCEPTION 'Import provenance exists; preserve schema and use a forward migration'; "
               "END IF; END $$")
    op.drop_table("material_import_rows")
    op.drop_index("ix_material_import_batches_created_at", table_name="material_import_batches")
    op.drop_table("material_import_batches")
