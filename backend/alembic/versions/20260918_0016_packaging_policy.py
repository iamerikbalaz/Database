"""Append-only first ZIP policy and administrator override decisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260918_0016"
down_revision = "20260917_0015"
branch_labels = None
depends_on = None


def upgrade():
    table = "material_packaging_policies"
    remainder = "evidence_hash"
    for char in "0123456789abcdef": remainder = f"replace({remainder}, '{char}', '')"
    op.create_table(table,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_id", sa.Uuid()),
        sa.Column("inventory_id", sa.Uuid()),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("policy", sa.String(64), nullable=False),
        sa.Column("storage_timezone", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("material_id", "id", name="uq_material_packaging_policies_material_id"),
        sa.UniqueConstraint("material_id", "revision", name="uq_material_packaging_policies_revision"),
        sa.ForeignKeyConstraint(["material_id", "previous_id"], [table + ".material_id", table + ".id"],
            name="fk_material_packaging_policies_previous", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["material_id", "inventory_id"], ["material_inventories.material_id", "material_inventories.id"],
            name="fk_material_packaging_policies_inventory", ondelete="RESTRICT"),
        sa.CheckConstraint("revision >= 1", name="ck_material_packaging_policies_revision"),
        sa.CheckConstraint("policy IN ('LEGACY_BEFORE_2026_03_04', 'CURRENT_ON_OR_AFTER_2026_03_04')", name="ck_material_packaging_policies_policy"),
        sa.CheckConstraint("(revision = 1 AND previous_id IS NULL AND inventory_id IS NOT NULL) OR (revision > 1 AND previous_id IS NOT NULL AND inventory_id IS NULL)",
            name="ck_material_packaging_policies_origin"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 2000", name="ck_material_packaging_policies_reason"),
        sa.CheckConstraint("length(storage_timezone) BETWEEN 1 AND 100", name="ck_material_packaging_policies_timezone"),
        sa.CheckConstraint(f"length(evidence_hash) = 64 AND {remainder} = ''", name="ck_material_packaging_policies_evidence_hash"))
    op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("""CREATE FUNCTION material_packaging_policy_chain() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE prior material_packaging_policies%ROWTYPE;
    BEGIN
        PERFORM 1 FROM pbr_materials WHERE id = NEW.material_id FOR UPDATE;
        SELECT * INTO prior FROM material_packaging_policies WHERE material_id = NEW.material_id ORDER BY revision DESC LIMIT 1;
        IF (prior.id IS NULL AND (NEW.revision <> 1 OR NEW.previous_id IS NOT NULL))
            OR (prior.id IS NOT NULL AND (NEW.revision <> prior.revision + 1 OR NEW.previous_id IS DISTINCT FROM prior.id
                OR NEW.policy = prior.policy OR NEW.storage_timezone <> prior.storage_timezone)) THEN
            RAISE EXCEPTION 'Packaging policy must extend the current decision';
        END IF;
        RETURN NEW;
    END; $$""")
    op.execute(f"CREATE TRIGGER {table}_chain BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION material_packaging_policy_chain()")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM material_packaging_policies) THEN "
        "RAISE EXCEPTION 'Packaging policy provenance exists; preserve schema and use a forward migration'; END IF; END $$")
    op.drop_table("material_packaging_policies")
    op.execute("DROP FUNCTION material_packaging_policy_chain()")
