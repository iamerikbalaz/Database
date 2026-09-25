"""Human Checked and Note; retain technical states and immutable history."""
from alembic import op
import sqlalchemy as sa
from uuid import NAMESPACE_URL, uuid5
from app.texture_categories import CATEGORIES
from app.catalog import value_key

revision = "20260925_0027"
down_revision = "20260925_0026"
branch_labels = None
depends_on = None


def update_guard(name, old, new):
    definition = op.get_bind().execute(sa.text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": name + "()"}).scalar_one()
    if definition.count(old) != 1:
        raise RuntimeError("Unexpected resource guard definition; migration did not proceed.")
    op.execute(definition.replace(old, new, 1))


def upgrade():
    op.add_column("pbr_materials", sa.Column("checked_status", sa.String(16), nullable=False, server_default="no"))
    op.add_column("pbr_materials", sa.Column("note", sa.Text(), nullable=True))
    op.create_check_constraint("ck_pbr_materials_checked_status", "pbr_materials", "checked_status IN ('no', 'OK', 'Correction')")
    op.create_index("ix_pbr_materials_checked_status", "pbr_materials", ["checked_status"])
    update_guard("resource_change_guard", "'publication_status',publication_status)",
                 "'publication_status',publication_status,'checked_status',checked_status,'note',note)")
    update_guard("resource_command_guard", "'publication_status','created_at','updated_at']",
                 "'publication_status','checked_status','note','created_at','updated_at']")
    # Preserve existing values, IDs, activity and every assignment. Names are
    # full paths because e.g. Tiles occurs under several different groups.
    catalog = sa.table("online_categories", sa.column("id", sa.Uuid()), sa.column("value", sa.String()),
        sa.column("normalized_key", sa.String()), sa.column("version", sa.Integer()), sa.column("is_active", sa.Boolean()))
    connection = op.get_bind()
    for category in CATEGORIES:
        key = value_key(category["value"])
        if connection.execute(sa.select(catalog.c.id).where(catalog.c.normalized_key == key)).first() is None:
            connection.execute(catalog.insert().values(id=uuid5(NAMESPACE_URL, "reawote:categories:2026:" + category["code"]),
                value=category["value"], normalized_key=key, version=1, is_active=True))


def downgrade():
    # Added vocabulary is retained: it may already be referenced in content or history.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pbr_materials WHERE checked_status != 'no' OR note IS NOT NULL)")).scalar():
        raise RuntimeError("Export human checks and notes before downgrading; no data was removed.")
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM resource_change_events WHERE kind='MATERIAL' AND after_snapshot ? 'checked_status') OR EXISTS (SELECT 1 FROM resource_commands WHERE kind='MATERIAL' AND response_snapshot ? 'checked_status')")).scalar():
        raise RuntimeError("Material tracking history exists; use a forward migration.")
    update_guard("resource_change_guard", "'publication_status',publication_status,'checked_status',checked_status,'note',note)",
                 "'publication_status',publication_status)")
    update_guard("resource_command_guard", "'publication_status','checked_status','note','created_at','updated_at']",
                 "'publication_status','created_at','updated_at']")
    op.drop_index("ix_pbr_materials_checked_status", "pbr_materials")
    op.drop_constraint("ck_pbr_materials_checked_status", "pbr_materials", type_="check")
    op.drop_column("pbr_materials", "note")
    op.drop_column("pbr_materials", "checked_status")
