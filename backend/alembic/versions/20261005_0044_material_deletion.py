"""Retained material tombstones and recoverable admin deletion ownership."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261005_0044"
down_revision = "20261005_0043"
branch_labels = None
depends_on = None


def _guard(upgrading):
    bind = op.get_bind()
    if bind.dialect.name != "postgresql": return
    definition = bind.execute(sa.text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)")).scalar_one()
    previous = "SELECT to_jsonb(m) - 'source_brand_name'"
    current = previous + " - 'deleted_at'"
    source, target = (previous, current) if upgrading else (current, previous)
    if definition.count(source) != 1:
        raise RuntimeError("Unexpected material receipt guard; no changes applied")
    bind.execute(sa.text(definition.replace(source, target, 1)))


def upgrade():
    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column("pbr_materials",sa.Column("deleted_at",sa.DateTime(timezone=True),nullable=True))
    _guard(True)
    op.create_index("ix_pbr_materials_deleted_at","pbr_materials",["deleted_at"])
    op.create_table("material_deletion_operations",
        sa.Column("id",sa.Uuid(),primary_key=True),
        sa.Column("actor_id",sa.Uuid(),sa.ForeignKey("internal_users.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("request_key",sa.Uuid(),nullable=False),sa.Column("request_hash",sa.String(64),nullable=False),
        sa.Column("proposal_hash",sa.String(64),nullable=False),sa.Column("mode",sa.String(24),nullable=False),
        sa.Column("request_payload",document,nullable=False),sa.Column("plan",document,nullable=False),sa.Column("items",document,nullable=False),
        sa.Column("status",sa.String(24),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint("actor_id","request_key",name="uq_material_deletion_actor_key"),
        sa.CheckConstraint("status IN ('RUNNING','RECOVERY_REQUIRED','COMPLETED','PARTIAL','REJECTED')",name="ck_material_deletion_status"),
        sa.CheckConstraint("mode IN ('RECORD_ONLY','RECORD_AND_FILES')",name="ck_material_deletion_mode"))
    op.create_table("material_deletion_owners",
        sa.Column("material_id",sa.Uuid(),sa.ForeignKey("pbr_materials.id",ondelete="RESTRICT"),primary_key=True),
        sa.Column("operation_id",sa.Uuid(),sa.ForeignKey("material_deletion_operations.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("brand_id",sa.Uuid(),sa.ForeignKey("published_brands.id",ondelete="RESTRICT"),nullable=True),
        sa.Column("folder_path",sa.String(2048),nullable=True))
    op.create_index("ix_material_deletion_owners_operation_id","material_deletion_owners",["operation_id"])
    op.create_index("ix_material_deletion_owners_brand_id","material_deletion_owners",["brand_id"])
    if op.get_bind().dialect.name=="postgresql":
        op.execute("""CREATE FUNCTION material_deletion_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF TG_OP='DELETE' OR TG_OP='TRUNCATE' THEN RAISE EXCEPTION 'Material deletion history must be retained'; END IF;
            IF OLD.status IN ('COMPLETED','PARTIAL','REJECTED') OR
              (to_jsonb(NEW)-ARRAY['items','status','updated_at']) IS DISTINCT FROM
              (to_jsonb(OLD)-ARRAY['items','status','updated_at']) THEN
              RAISE EXCEPTION 'Material deletion authorization is immutable';
            END IF;
            RETURN NEW;
          END $$""")
        op.execute("CREATE TRIGGER material_deletion_receipt_guard BEFORE UPDATE OR DELETE ON material_deletion_operations FOR EACH ROW EXECUTE FUNCTION material_deletion_receipt_guard()")
        op.execute("CREATE TRIGGER material_deletion_no_truncate BEFORE TRUNCATE ON material_deletion_operations FOR EACH STATEMENT EXECUTE FUNCTION material_deletion_receipt_guard()")
        op.execute("""CREATE FUNCTION material_tombstone_guard() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF OLD.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'Deleted material audit records are immutable'; END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
          END $$""")
        op.execute("CREATE TRIGGER material_tombstone_guard BEFORE UPDATE OR DELETE ON pbr_materials FOR EACH ROW EXECUTE FUNCTION material_tombstone_guard()")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM material_deletion_operations) OR EXISTS(SELECT 1 FROM pbr_materials WHERE deleted_at IS NOT NULL)")).scalar():
        raise RuntimeError("Cannot remove deletion history, tombstones or recovery ownership.")
    _guard(False)
    if op.get_bind().dialect.name=="postgresql":
        op.execute("DROP TRIGGER material_tombstone_guard ON pbr_materials")
        op.execute("DROP FUNCTION material_tombstone_guard()")
    op.drop_table("material_deletion_owners"); op.drop_table("material_deletion_operations")
    if op.get_bind().dialect.name=="postgresql": op.execute("DROP FUNCTION material_deletion_receipt_guard()")
    op.drop_index("ix_pbr_materials_deleted_at",table_name="pbr_materials")
    op.drop_column("pbr_materials","deleted_at")
