"""Durable material creation and additive content batches."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261001_0039"
down_revision = "20261001_0038"
branch_labels = None
depends_on = None


def upgrade():
    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table("material_creation_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_payload", document, nullable=False), sa.Column("source_context", document, nullable=False),
        sa.Column("items", document, nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_material_creation_actor_key"),
        sa.CheckConstraint("status IN ('PENDING','PARTIAL','COMPLETED')", name="ck_material_creation_status"))
    op.create_table("material_content_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_snapshot", document, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_material_content_actor_key"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION material_creation_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Material creation receipts must be retained'; END IF;
          IF OLD.status='COMPLETED' OR
             (to_jsonb(NEW)-ARRAY['items','status','updated_at']) IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['items','status','updated_at']) THEN
            RAISE EXCEPTION 'Material creation authorization is immutable';
          END IF;
          RETURN NEW;
        END $$""")
        op.execute("CREATE TRIGGER material_creation_receipt_guard BEFORE UPDATE OR DELETE ON material_creation_batches FOR EACH ROW EXECUTE FUNCTION material_creation_receipt_guard()")
        op.execute("""CREATE FUNCTION material_content_batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'Material content batch receipts are immutable'; END $$""")
        op.execute("CREATE TRIGGER material_content_batch_guard BEFORE UPDATE OR DELETE ON material_content_batches FOR EACH ROW EXECUTE FUNCTION material_content_batch_guard()")


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS(SELECT 1 FROM material_creation_batches) OR EXISTS(SELECT 1 FROM material_content_batches)")).scalar():
        raise RuntimeError("Cannot remove material creation or content receipt history.")
    op.drop_table("material_content_batches")
    op.drop_table("material_creation_batches")
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION material_content_batch_guard()")
        op.execute("DROP FUNCTION material_creation_receipt_guard()")
