"""Durable authorization and recovery for explicit source metadata edits."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260927_0032"
down_revision = "20260927_0031"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("material_metadata_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("brand_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("folder_path", sa.String(2048), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_metadata_operation_actor_request"),
        sa.CheckConstraint("status IN ('RUNNING','COMPLETED','REJECTED')", name="ck_metadata_operation_status"))
    op.create_index("ix_material_metadata_operations_material_id", "material_metadata_operations", ["material_id"])
    op.execute("""CREATE FUNCTION metadata_operation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'Metadata operation receipts cannot be deleted'; END IF;
          IF OLD.status <> 'RUNNING' OR
             (to_jsonb(NEW) - ARRAY['status','result','updated_at']) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['status','result','updated_at']) THEN
            RAISE EXCEPTION 'Metadata operation authorization and terminal receipts are immutable';
          END IF;
          RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER metadata_operation_protect BEFORE UPDATE OR DELETE ON material_metadata_operations "
               "FOR EACH ROW EXECUTE FUNCTION metadata_operation_guard()")
    op.execute("CREATE TRIGGER metadata_operation_no_truncate BEFORE TRUNCATE ON material_metadata_operations "
               "FOR EACH STATEMENT EXECUTE FUNCTION metadata_operation_guard()")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM material_metadata_operations)")).scalar():
        raise RuntimeError("Metadata operation receipts exist; use a forward migration.")
    op.drop_table("material_metadata_operations")
    op.execute("DROP FUNCTION metadata_operation_guard()")
