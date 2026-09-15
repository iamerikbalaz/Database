"""Persist source inventories, invalidation generations and material audit events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260915_0007"
down_revision = "20260914_0006"
branch_labels = None
depends_on = None


def _hash(column, name):
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"{column} IS NULL OR (length({column}) = 64 AND {remainder} = '')", name=name)


def upgrade():
    op.create_table("material_inventories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("revision_hash", sa.String(64), nullable=False),
        sa.Column("source_inventory", postgresql.JSONB(), nullable=False),
        sa.Column("material_context", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("material_id", "id", name="uq_material_inventories_material_id"),
        sa.CheckConstraint("generation >= 0", name="ck_material_inventories_generation"),
        _hash("revision_hash", "ck_material_inventories_revision_hash"),
    )
    op.create_index("ix_material_inventories_material_id", "material_inventories", ["material_id"])
    op.create_table("material_review_states",
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("revision_hash", sa.String(64)),
        sa.Column("inventory_id", sa.Uuid()),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(100)),
        sa.ForeignKeyConstraint(["material_id", "inventory_id"], ["material_inventories.material_id", "material_inventories.id"],
                                name="fk_material_review_states_inventory", ondelete="RESTRICT"),
        sa.CheckConstraint("generation >= 0", name="ck_material_review_states_generation"),
        _hash("revision_hash", "ck_material_review_states_revision_hash"),
        sa.CheckConstraint("(revision_hash IS NULL AND inventory_id IS NULL) OR (revision_hash IS NOT NULL AND inventory_id IS NOT NULL)",
                           name="ck_material_review_states_current_inventory"),
    )
    op.create_table("material_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("revision_hash", sa.String(64)),
        sa.Column("request_key", sa.Uuid()),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_material_audit_events_actor_request"),
        sa.CheckConstraint("generation >= 0", name="ck_material_audit_events_generation"),
        _hash("revision_hash", "ck_material_audit_events_revision_hash"),
        _hash("request_hash", "ck_material_audit_events_request_hash"),
    )
    op.create_index("ix_material_audit_events_material_id", "material_audit_events", ["material_id"])
    op.execute("""CREATE FUNCTION material_review_history_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Material review history is append-only'; END; $$""")
    for table in ("material_inventories", "material_audit_events"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    # Explicit operator downgrade removes new review/audit data; source files,
    # credentials, material records and existing metadata snapshots stay intact.
    op.drop_table("material_review_states")
    op.drop_table("material_audit_events")
    op.drop_table("material_inventories")
    op.execute("DROP FUNCTION material_review_history_reject_mutation()")
