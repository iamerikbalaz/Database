"""A separate, server-produced automatic file check result."""
from alembic import op
import sqlalchemy as sa

revision = "20260928_0033"
down_revision = "20260927_0032"
branch_labels = None
depends_on = None

_OLD_MATERIAL_KEYS = "'publication_status','checked_status','note','created_at','updated_at']"
_INTERMEDIATE_MATERIAL_KEYS = ("'publication_status','checked_status','note','automatic_file_check_status',"
    "'automatic_file_checked_at','automatic_file_check_report','automatic_file_check_profile',"
    "'automatic_file_check_complete','created_at','updated_at']")
_NEW_MATERIAL_KEYS = ("'publication_status','checked_status','note','automatic_file_check_status',"
    "'automatic_file_checked_at','automatic_file_check_profile',"
    "'automatic_file_check_complete','created_at','updated_at']")
_OLD_MATERIAL_RESPONSE = "SELECT to_jsonb(m) INTO actual FROM pbr_materials m WHERE id=NEW.material_id;"
_NEW_MATERIAL_RESPONSE = "SELECT to_jsonb(m) - 'automatic_file_check_report' INTO actual FROM pbr_materials m WHERE id=NEW.material_id;"
_OLD_RESPONSE_COMPARISON = "OR NEW.response_snapshot - ARRAY['created_at','updated_at'] IS DISTINCT FROM actual - ARRAY['created_at','updated_at'] THEN"
_NEW_RESPONSE_COMPARISON = """OR (NEW.response_snapshot->>'automatic_file_checked_at')::timestamptz IS DISTINCT FROM (actual->>'automatic_file_checked_at')::timestamptz
            OR NEW.response_snapshot - ARRAY['created_at','updated_at','automatic_file_checked_at'] IS DISTINCT FROM actual - ARRAY['created_at','updated_at','automatic_file_checked_at'] THEN"""


def update_resource_command_guard(connection, *, upgrading=True):
    """Adapt new receipts only; existing immutable receipt JSON stays untouched.

    An explicit connection also supports the forward-only correction of a local
    instance upgraded before this pre-release guard fix. Reapplying is a no-op.
    """
    definition = connection.execute(sa.text("SELECT pg_get_functiondef('resource_command_guard()'::regprocedure)")).scalar_one()
    original = definition
    variants = (_OLD_MATERIAL_KEYS, _INTERMEDIATE_MATERIAL_KEYS, _NEW_MATERIAL_KEYS)
    present = [item for item in variants if item in definition]
    if len(present) != 1 or definition.count(present[0]) != 1:
        raise RuntimeError("Unexpected resource command guard definition; no changes were applied.")
    definition = definition.replace(present[0], _NEW_MATERIAL_KEYS if upgrading else _OLD_MATERIAL_KEYS, 1)
    for before, after in ((_OLD_MATERIAL_RESPONSE, _NEW_MATERIAL_RESPONSE),
                          (_OLD_RESPONSE_COMPARISON, _NEW_RESPONSE_COMPARISON)):
        old, new = (before, after) if upgrading else (after, before)
        if definition.count(old) == 0 and definition.count(new) == 1:
            continue
        if definition.count(old) != 1 or new in definition:
            raise RuntimeError("Unexpected resource command guard definition; no changes were applied.")
        definition = definition.replace(old, new, 1)
    if definition != original:
        connection.execute(sa.text(definition))


def upgrade():
    op.add_column("pbr_materials", sa.Column("automatic_file_check_status", sa.String(16),
        nullable=False, server_default=sa.text("'NOT_CHECKED'")))
    op.add_column("pbr_materials", sa.Column("automatic_file_checked_at", sa.DateTime(timezone=True)))
    op.add_column("pbr_materials", sa.Column("automatic_file_check_report", sa.Text()))
    op.add_column("pbr_materials", sa.Column("automatic_file_check_profile", sa.String(32)))
    op.add_column("pbr_materials", sa.Column("automatic_file_check_complete", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_check_constraint("ck_material_automatic_file_check_status", "pbr_materials",
        "automatic_file_check_status IN ('NOT_CHECKED', 'OK', 'ISSUES')")
    update_resource_command_guard(op.get_bind())


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM pbr_materials WHERE automatic_file_checked_at IS NOT NULL)")).scalar():
        raise RuntimeError("Automatic file check results exist; use a forward migration.")
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM resource_commands WHERE kind='MATERIAL' AND response_snapshot ? 'automatic_file_check_status')")).scalar():
        raise RuntimeError("Automatic file check receipt history exists; use a forward migration.")
    update_resource_command_guard(op.get_bind(), upgrading=False)
    op.drop_constraint("ck_material_automatic_file_check_status", "pbr_materials", type_="check")
    for column in ("automatic_file_check_complete", "automatic_file_check_profile", "automatic_file_check_report", "automatic_file_checked_at", "automatic_file_check_status"):
        op.drop_column("pbr_materials", column)
