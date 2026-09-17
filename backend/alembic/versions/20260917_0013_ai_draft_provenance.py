"""Explicit source URL approvals and immutable AI draft provenance."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260917_0013"
down_revision = "20260917_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("material_source_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("material_id", "url", name="uq_material_source_links_url"),
        sa.CheckConstraint("version >= 1", name="ck_material_source_links_version"),
        sa.CheckConstraint("length(url) BETWEEN 1 AND 2048", name="ck_material_source_links_url"))
    op.create_index("ix_material_source_links_material_id", "material_source_links", ["material_id"])
    op.execute("""CREATE FUNCTION material_source_link_protect_identity() RETURNS trigger AS $$ BEGIN
        IF ROW(NEW.id, NEW.material_id, NEW.actor_id, NEW.url, NEW.created_at)
           IS DISTINCT FROM ROW(OLD.id, OLD.material_id, OLD.actor_id, OLD.url, OLD.created_at) THEN
           RAISE EXCEPTION 'Source URL identity is immutable'; END IF;
        RETURN NEW; END; $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER material_source_links_identity BEFORE UPDATE ON material_source_links "
               "FOR EACH ROW EXECUTE FUNCTION material_source_link_protect_identity()")
    op.execute("CREATE TRIGGER material_source_links_no_delete BEFORE DELETE OR TRUNCATE ON material_source_links "
               "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    remainder = "context_hash"
    for character in "0123456789abcdef": remainder = f"replace({remainder}, '{character}', '')"
    op.create_table("material_ai_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("source_link_ids", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("content_revision >= 0", name="ck_material_ai_drafts_revision"),
        sa.CheckConstraint(f"context_hash IS NULL OR (length(context_hash) = 64 AND {remainder} = '')", name="ck_material_ai_drafts_context_hash"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_material_ai_drafts_reason"))
    op.create_index("ix_material_ai_drafts_material_id", "material_ai_drafts", ["material_id"])
    op.execute("CREATE TRIGGER material_ai_drafts_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON material_ai_drafts "
               "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM material_ai_drafts) OR EXISTS (SELECT 1 FROM material_source_links) THEN "
               "RAISE EXCEPTION 'Content provenance exists; preserve schema and use a forward migration'; END IF; END $$")
    op.drop_index("ix_material_ai_drafts_material_id", table_name="material_ai_drafts")
    op.drop_table("material_ai_drafts")
    op.drop_index("ix_material_source_links_material_id", table_name="material_source_links")
    op.drop_table("material_source_links")
    op.execute("DROP FUNCTION material_source_link_protect_identity()")
