"""Immutable approved publication inputs and exact CSV artifacts; no execution."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260917_0015"
down_revision = "20260917_0014"
branch_labels = None
depends_on = None


def _hash(table, column):
    remainder = column
    for character in "0123456789abcdef": remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"length({column}) = 64 AND {remainder} = ''", name=f"ck_{table}_{column}")


def upgrade():
    op.create_table("publication_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("csv_sha256", sa.String(64), nullable=False),
        sa.Column("csv_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("warnings_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_publication_batches_actor_request"),
        sa.CheckConstraint("row_count BETWEEN 1 AND 100", name="ck_publication_batches_row_count"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_publication_batches_reason"),
        sa.CheckConstraint("length(csv_bytes) BETWEEN 3 AND 33554432", name="ck_publication_batches_csv_size"),
        *(_hash("publication_batches", field) for field in ("request_hash", "snapshot_hash", "csv_sha256")))
    op.create_index("ix_publication_batches_created_at", "publication_batches", ["created_at"])
    op.create_table("publication_batch_items",
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("publication_batches.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("content_context_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("technical_check_id", sa.Uuid(), nullable=False),
        sa.Column("metadata_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("technical_approval_id", sa.Uuid(), nullable=False),
        sa.Column("publication_approval_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_publication_batch_items_ordinal"),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 100", name="ck_publication_batch_items_ordinal"),
        sa.CheckConstraint("generation >= 0", name="ck_publication_batch_items_generation"),
        *(_hash("publication_batch_items", field) for field in ("revision_hash", "content_context_hash", "snapshot_hash")),
        sa.ForeignKeyConstraint(["material_id", "technical_check_id", "generation", "revision_hash"],
            ["material_technical_checks.material_id", "material_technical_checks.id", "material_technical_checks.generation", "material_technical_checks.revision_hash"],
            name="fk_publication_batch_items_check", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["material_id", "metadata_snapshot_id"], ["pbr_material_metadata_snapshots.material_id", "pbr_material_metadata_snapshots.id"],
            name="fk_publication_batch_items_metadata", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["material_id", "technical_approval_id"], ["material_approvals.material_id", "material_approvals.id"],
            name="fk_publication_batch_items_technical", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["material_id", "publication_approval_id"], ["material_approvals.material_id", "material_approvals.id"],
            name="fk_publication_batch_items_publication", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["material_id", "content_context_hash"], ["material_content_approvals.material_id", "material_content_approvals.context_hash"],
            name="fk_publication_batch_items_content", ondelete="RESTRICT"))
    for table in ("publication_batches", "publication_batch_items"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM publication_batches) OR EXISTS (SELECT 1 FROM publication_batch_items) THEN "
        "RAISE EXCEPTION 'Publication provenance exists; preserve schema and use a forward migration'; END IF; END $$")
    op.drop_table("publication_batch_items")
    op.drop_index("ix_publication_batches_created_at", table_name="publication_batches")
    op.drop_table("publication_batches")
