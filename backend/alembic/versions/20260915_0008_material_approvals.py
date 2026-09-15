"""Immutable technical checks and revision-bound technical/publication approvals."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260915_0008"
down_revision = "20260915_0007"
branch_labels = None
depends_on = None


def _hash(column, name):
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"{column} IS NULL OR (length({column}) = 64 AND {remainder} = '')", name=name)


def upgrade():
    op.create_table("material_technical_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("inventory_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("material_id", "id", "generation", "revision_hash", name="uq_material_technical_checks_revision"),
        sa.ForeignKeyConstraint(["material_id", "inventory_id"], ["material_inventories.material_id", "material_inventories.id"],
                                name="fk_material_technical_checks_inventory", ondelete="RESTRICT"),
        sa.CheckConstraint("generation >= 0", name="ck_material_technical_checks_generation"),
        _hash("revision_hash", "ck_material_technical_checks_revision_hash"),
        _hash("report_hash", "ck_material_technical_checks_report_hash"),
    )
    op.create_index("ix_material_technical_checks_material_id", "material_technical_checks", ["material_id"])
    op.add_column("material_review_states", sa.Column("technical_check_id", sa.Uuid()))
    op.create_foreign_key("fk_material_review_states_technical", "material_review_states", "material_technical_checks",
        ["material_id", "technical_check_id", "generation", "revision_hash"], ["material_id", "id", "generation", "revision_hash"], ondelete="RESTRICT")
    op.create_table("material_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("technical_check_id", sa.Uuid(), nullable=False),
        sa.Column("technical_approval_id", sa.Uuid()),
        sa.Column("note", sa.Text()),
        sa.Column("warnings_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("material_id", "id", name="uq_material_approvals_material_id"),
        sa.UniqueConstraint("material_id", "generation", "revision_hash", "kind", name="uq_material_approvals_revision_kind"),
        sa.ForeignKeyConstraint(["material_id", "technical_check_id", "generation", "revision_hash"],
            ["material_technical_checks.material_id", "material_technical_checks.id", "material_technical_checks.generation", "material_technical_checks.revision_hash"],
            name="fk_material_approvals_check", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["material_id", "technical_approval_id"], ["material_approvals.material_id", "material_approvals.id"],
            name="fk_material_approvals_technical", ondelete="RESTRICT"),
        sa.CheckConstraint("generation >= 0", name="ck_material_approvals_generation"),
        _hash("revision_hash", "ck_material_approvals_revision_hash"),
        sa.CheckConstraint("(kind = 'TECHNICAL' AND technical_approval_id IS NULL) OR (kind = 'PUBLICATION' AND technical_approval_id IS NOT NULL)",
                           name="ck_material_approvals_kind"),
    )
    op.create_index("ix_material_approvals_material_id", "material_approvals", ["material_id"])
    for table in ("material_technical_checks", "material_approvals"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    op.drop_table("material_approvals")
    op.drop_constraint("fk_material_review_states_technical", "material_review_states", type_="foreignkey")
    op.drop_column("material_review_states", "technical_check_id")
    op.drop_table("material_technical_checks")
