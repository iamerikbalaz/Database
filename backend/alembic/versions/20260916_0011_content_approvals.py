"""Append-only approval of exact publication content and review context."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260916_0011"
down_revision = "20260916_0010"
branch_labels = None
depends_on = None


def upgrade():
    remainder = "context_hash"
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    op.create_table("material_content_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("warnings_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["material_id", "content_revision"],
            ["material_content_revisions.material_id", "material_content_revisions.revision"],
            name="fk_material_content_approvals_revision", ondelete="RESTRICT"),
        sa.UniqueConstraint("material_id", "context_hash", name="uq_material_content_approvals_context"),
        sa.CheckConstraint("content_revision >= 1", name="ck_material_content_approvals_revision"),
        sa.CheckConstraint(f"length(context_hash) = 64 AND {remainder} = ''", name="ck_material_content_approvals_context_hash"))
    op.create_index("ix_material_content_approvals_material_id", "material_content_approvals", ["material_id"])
    op.execute("CREATE TRIGGER material_content_approvals_append_only BEFORE UPDATE OR DELETE OR TRUNCATE "
               "ON material_content_approvals FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.drop_table("material_content_approvals")
