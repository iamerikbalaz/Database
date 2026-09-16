"""Durable filesystem coordination, burned brand numbers and identity history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260916_0009"
down_revision = "20260915_0008"
branch_labels = None
depends_on = None


def _hash(column, name):
    remainder = column
    for character in "0123456789abcdef": remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"{column} IS NULL OR (length({column}) = 64 AND {remainder} = '')", name=name)


def upgrade():
    op.create_table("material_file_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_brand_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_brand_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("source_context", postgresql.JSONB(), nullable=False),
        sa.Column("target_context", postgresql.JSONB(), nullable=False),
        sa.Column("worker_plan", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_material_file_operations_actor_request"),
        sa.CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'ROLLED_BACK', 'RECOVERY_REQUIRED', 'REJECTED')", name="ck_material_file_operations_status"),
        _hash("request_hash", "ck_material_file_operations_request_hash"),
        _hash("proposal_hash", "ck_material_file_operations_proposal_hash"),
    )
    op.create_index("ix_material_file_operations_material_id", "material_file_operations", ["material_id"])
    op.create_index("uq_material_file_operations_active", "material_file_operations", ["material_id"], unique=True,
                    postgresql_where=sa.text("status IN ('RUNNING', 'RECOVERY_REQUIRED')"))
    op.create_table("material_number_reservations",
        sa.Column("brand_id", sa.Uuid(), sa.ForeignKey("published_brands.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("sequence_number", sa.Integer(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("material_file_operations.id", ondelete="RESTRICT")),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sequence_number BETWEEN 1 AND 9999", name="ck_material_number_reservations_sequence"),
    )
    op.create_index("ix_material_number_reservations_material_id", "material_number_reservations", ["material_id"])
    op.execute("""INSERT INTO material_number_reservations (brand_id, sequence_number, material_id, created_at)
                  SELECT published_brand_id, sequence_number, id, created_at FROM pbr_materials""")
    op.create_table("material_identity_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("pbr_materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("material_file_operations.id", ondelete="RESTRICT"), unique=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT")),
        sa.Column("old_context", postgresql.JSONB(), nullable=False),
        sa.Column("new_context", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_material_identity_history_material_id", "material_identity_history", ["material_id"])
    for table in ("material_number_reservations", "material_identity_history"):
        op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")
    op.execute("""CREATE FUNCTION material_file_operation_protect_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status NOT IN ('RUNNING', 'RECOVERY_REQUIRED') OR
             (to_jsonb(NEW) - ARRAY['status','result','updated_at']) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['status','result','updated_at']) THEN
            RAISE EXCEPTION 'Identity operation authorization and terminal outcome are immutable';
          END IF;
          RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER material_file_operation_protect_update BEFORE UPDATE ON material_file_operations "
               "FOR EACH ROW EXECUTE FUNCTION material_file_operation_protect_update()")
    op.execute("CREATE TRIGGER material_file_operation_no_delete BEFORE DELETE OR TRUNCATE ON material_file_operations "
               "FOR EACH STATEMENT EXECUTE FUNCTION material_review_history_reject_mutation()")


def downgrade():
    # An active journal must be reconciled before removing its database lock.
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM material_file_operations WHERE status IN ('RUNNING', 'RECOVERY_REQUIRED'))
                  THEN RAISE EXCEPTION 'Reconcile active filesystem operations before downgrade'; END IF; END $$""")
    op.drop_table("material_identity_history")
    op.drop_table("material_number_reservations")
    op.drop_table("material_file_operations")
    op.execute("DROP FUNCTION material_file_operation_protect_update()")
