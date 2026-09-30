"""Keep historical source manufacturer identities and durable rename batches."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260930_0036"
down_revision = "20260930_0035"
branch_labels = None
depends_on = None


def _guard(upgrade):
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    definition = bind.execute(sa.text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)")).scalar_one()
    old = "SELECT to_jsonb(m)"
    new = "SELECT to_jsonb(m) - 'source_brand_name'"
    source, target = (old, new) if upgrade else (new, old)
    if definition.count(source) != 1:
        raise RuntimeError("Unexpected material receipt guard; no changes applied")
    bind.execute(sa.text(definition.replace(source, target, 1)))


def upgrade():
    op.add_column("pbr_materials", sa.Column("source_brand_name", sa.String(255)))
    op.execute("UPDATE pbr_materials SET source_brand_name=(SELECT name FROM published_brands WHERE published_brands.id=pbr_materials.published_brand_id)")
    op.create_table("customer_rename_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("authorization", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_customer_rename_actor_key"),
        sa.CheckConstraint("status IN ('RUNNING','RECOVERY_REQUIRED','COMPLETED','PARTIAL')", name="ck_customer_rename_status"))
    condition = sa.text("status IN ('RUNNING','RECOVERY_REQUIRED')")
    op.create_index("uq_customer_rename_active", "customer_rename_operations", ["customer_id"], unique=True,
                    postgresql_where=condition, sqlite_where=condition)
    _guard(True)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION customer_rename_protect_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.status NOT IN ('RUNNING','RECOVERY_REQUIRED') OR
              (to_jsonb(NEW)-ARRAY['status','result','updated_at']) IS DISTINCT FROM
              (to_jsonb(OLD)-ARRAY['status','result','updated_at']) THEN
              RAISE EXCEPTION 'Customer rename authorization and terminal outcome are immutable';
            END IF; RETURN NEW; END; $$""")
        op.execute("CREATE TRIGGER customer_rename_protect_update BEFORE UPDATE ON customer_rename_operations FOR EACH ROW EXECUTE FUNCTION customer_rename_protect_update()")
        op.execute("CREATE TRIGGER customer_rename_no_delete BEFORE DELETE OR TRUNCATE ON customer_rename_operations FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS(SELECT 1 FROM customer_rename_operations)")).scalar_one():
        raise RuntimeError("Customer rename history exists; use a forward migration")
    if bind.execute(sa.text("""SELECT EXISTS(SELECT 1 FROM pbr_materials m JOIN published_brands b ON b.id=m.published_brand_id
        WHERE m.source_brand_name IS NOT NULL AND m.source_brand_name != b.name)""")).scalar_one():
        raise RuntimeError("Historical source manufacturer identities exist; use a forward migration")
    _guard(False)
    op.drop_table("customer_rename_operations")
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION customer_rename_protect_update()")
    op.drop_column("pbr_materials", "source_brand_name")
