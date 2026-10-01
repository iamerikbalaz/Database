"""Durable PNG preview edit plans and source ownership."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261001_0042"
down_revision = "20261001_0041"
branch_labels = None
depends_on = None


def upgrade():
    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table("preview_edit_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False), sa.Column("request_payload", document, nullable=False),
        sa.Column("plan", document, nullable=False), sa.Column("items", document, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_preview_edit_actor_key"),
        sa.CheckConstraint("status IN ('RUNNING','RECOVERY_REQUIRED','COMPLETED','PARTIAL','REJECTED')", name="ck_preview_edit_status"))
    op.create_table("preview_edit_owners",
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("preview_edit_operations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("brand_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("folder_path", sa.String(2048), nullable=False))
    op.create_index("ix_preview_edit_owners_operation_id", "preview_edit_owners", ["operation_id"])
    op.create_index("ix_preview_edit_owners_brand_id", "preview_edit_owners", ["brand_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION preview_edit_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF TG_OP='DELETE' OR TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'Preview edit history must be retained'; END IF;
            IF OLD.status IN ('COMPLETED','PARTIAL','REJECTED') OR
              (to_jsonb(NEW)-ARRAY['items','status','updated_at']) IS DISTINCT FROM
              (to_jsonb(OLD)-ARRAY['items','status','updated_at']) THEN
              RAISE EXCEPTION 'Preview edit authorization is immutable';
            END IF;
            RETURN NEW;
          END $$""")
        op.execute("CREATE TRIGGER preview_edit_receipt_guard BEFORE UPDATE OR DELETE ON preview_edit_operations FOR EACH ROW EXECUTE FUNCTION preview_edit_receipt_guard()")
        op.execute("CREATE TRIGGER preview_edit_no_truncate BEFORE TRUNCATE ON preview_edit_operations FOR EACH STATEMENT EXECUTE FUNCTION preview_edit_receipt_guard()")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM preview_edit_operations)")).scalar():
        raise RuntimeError("Cannot remove preview edit history or recovery ownership.")
    op.drop_table("preview_edit_owners")
    op.drop_table("preview_edit_operations")
    if op.get_bind().dialect.name == "postgresql": op.execute("DROP FUNCTION preview_edit_receipt_guard()")
