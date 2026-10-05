"""Names-only drafts preserve missing identity facts as NULL."""
from alembic import op
import sqlalchemy as sa

revision = "20261005_0043"
down_revision = "20261001_0042"
branch_labels = None
depends_on = None

CONSTRAINT = "(is_draft AND technical_identity IS NULL AND sequence_number IS NULL AND folder_path IS NULL AND NOT is_published AND workflow_status = 'IN_PROGRESS' AND (published_brand_id IS NULL OR main_category_code IS NULL)) OR (NOT is_draft AND published_brand_id IS NOT NULL AND main_category_code IS NOT NULL AND sequence_number IS NOT NULL AND technical_identity IS NOT NULL)"
FIELDS = {"published_brand_id": sa.Uuid(), "sequence_number": sa.Integer(), "main_category_code": sa.String(100), "assigned_processor_id": sa.Uuid(), "technical_identity": sa.String(512)}


def _receipt_guard(upgrading):
    """Validate the new public draft fact without rewriting historical receipts."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    definition = bind.execute(sa.text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)")).scalar_one()
    before = "'automatic_file_check_complete','created_at','updated_at']"
    after = "'automatic_file_check_complete','is_draft','created_at','updated_at']"
    source, target = (before, after) if upgrading else (after, before)
    if definition.count(source) != 1 or target in definition:
        raise RuntimeError("Unexpected material draft receipt guard; no changes applied")
    bind.execute(sa.text(definition.replace(source, target, 1)))


def upgrade():
    op.add_column("pbr_materials", sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.false()))
    for name, kind in FIELDS.items():
        op.alter_column("pbr_materials", name, existing_type=kind, nullable=True)
    op.create_check_constraint("ck_pbr_materials_draft_identity", "pbr_materials", CONSTRAINT)
    op.alter_column("material_creation_batches", "customer_id", existing_type=sa.Uuid(), nullable=True)
    _receipt_guard(True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM pbr_materials WHERE is_draft OR assigned_processor_id IS NULL) OR EXISTS(SELECT 1 FROM material_creation_batches WHERE customer_id IS NULL)")).scalar():
        raise RuntimeError("Cannot remove draft support while incomplete material records or their receipts exist.")
    if op.get_bind().dialect.name == "postgresql" and op.get_bind().execute(sa.text(
            "SELECT EXISTS(SELECT 1 FROM resource_commands WHERE kind='MATERIAL' AND response_snapshot ? 'is_draft')")).scalar():
        raise RuntimeError("Cannot remove draft support while draft-aware material receipts exist.")
    op.alter_column("material_creation_batches", "customer_id", existing_type=sa.Uuid(), nullable=False)
    _receipt_guard(False)
    op.drop_constraint("ck_pbr_materials_draft_identity", "pbr_materials", type_="check")
    for name, kind in FIELDS.items():
        op.alter_column("pbr_materials", name, existing_type=kind, nullable=False)
    op.drop_column("pbr_materials", "is_draft")
