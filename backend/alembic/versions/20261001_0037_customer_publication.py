"""Customer publication state, CSV country and clearer cooperation statuses."""
from alembic import op
import sqlalchemy as sa

revision = "20261001_0037"
down_revision = "20260930_0036"
branch_labels = None
depends_on = None


def _legacy_guard(upgrade):
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    definition = bind.execute(sa.text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)")).scalar_one()
    old = "SELECT to_jsonb(b)"
    new = "SELECT (to_jsonb(b) - ARRAY['is_published','country'])"
    source, target = (old, new) if upgrade else (new, old)
    if definition.count(source) != 1:
        raise RuntimeError("Unexpected brand receipt guard; no changes applied")
    bind.execute(sa.text(definition.replace(source, target, 1)))


def upgrade():
    op.add_column("published_brands", sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("published_brands", sa.Column("country", sa.String(255)))
    op.drop_constraint("ck_customer_status", "published_brands", type_="check")
    op.execute("UPDATE published_brands SET customer_status=CASE customer_status WHEN 'Active' THEN 'Active cooperation' WHEN 'test' THEN 'Test sample' WHEN 'In library' THEN 'In library (not verified)' ELSE customer_status END")
    op.alter_column("published_brands", "customer_status", server_default="Active cooperation")
    op.create_check_constraint("ck_customer_status", "published_brands", "customer_status IN ('In library (not verified)','Test sample','Active cooperation')")
    _legacy_guard(True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM published_brands WHERE is_published OR country IS NOT NULL")).scalar_one():
        raise RuntimeError("Customer publication data must be preserved before downgrade")
    _legacy_guard(False)
    op.drop_constraint("ck_customer_status", "published_brands", type_="check")
    op.execute("UPDATE published_brands SET customer_status=CASE customer_status WHEN 'Active cooperation' THEN 'Active' WHEN 'Test sample' THEN 'test' WHEN 'In library (not verified)' THEN 'In library' ELSE customer_status END")
    op.alter_column("published_brands", "customer_status", server_default="Active")
    op.create_check_constraint("ck_customer_status", "published_brands", "customer_status IN ('In library','test','Active')")
    op.drop_column("published_brands", "country")
    op.drop_column("published_brands", "is_published")
