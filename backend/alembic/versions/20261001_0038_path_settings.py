"""Versioned application storage locations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261001_0038"
down_revision = "20261001_0037"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("paths_settings_revisions",
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_snapshot", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_paths_settings_actor_request"),
        sa.CheckConstraint("version >= 1", name="ck_paths_settings_version"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION paths_settings_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'Path settings revisions are append-only'; END $$""")
        op.execute("CREATE TRIGGER paths_settings_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON paths_settings_revisions FOR EACH STATEMENT EXECUTE FUNCTION paths_settings_immutable()")


def downgrade():
    op.drop_table("paths_settings_revisions")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION paths_settings_immutable()")
