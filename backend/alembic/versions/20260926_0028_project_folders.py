"""Project NAS folder references, preserving immutable legacy receipts."""
from alembic import op
import sqlalchemy as sa

revision = "20260926_0028"
down_revision = "20260925_0027"
branch_labels = None
depends_on = None


def _update_guard(name, old, new):
    definition = op.get_bind().execute(sa.text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"), {"name": name + "()"}).scalar_one()
    if definition.count(old) != 1:
        raise RuntimeError("Unexpected resource guard definition; migration did not proceed.")
    op.execute(definition.replace(old, new, 1))


def upgrade():
    op.add_column("projects", sa.Column("folder_path", sa.String(2048), nullable=True))
    _update_guard("resource_change_guard", "'due_date',due_date,'notes',notes)", "'due_date',due_date,'notes',notes,'folder_path',folder_path)")
    _update_guard("resource_command_guard", "'status','due_date','notes','created_at','updated_at']", "'status','due_date','notes','folder_path','created_at','updated_at']")


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM projects WHERE folder_path IS NOT NULL)")).scalar():
        raise RuntimeError("Project folder references exist; use a forward migration.")
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM resource_change_events WHERE kind='PROJECT' AND after_snapshot ? 'folder_path') OR EXISTS (SELECT 1 FROM resource_commands WHERE kind='PROJECT' AND response_snapshot ? 'folder_path')")).scalar():
        raise RuntimeError("Project folder history exists; use a forward migration.")
    _update_guard("resource_change_guard", "'due_date',due_date,'notes',notes,'folder_path',folder_path)", "'due_date',due_date,'notes',notes)")
    _update_guard("resource_command_guard", "'status','due_date','notes','folder_path','created_at','updated_at']", "'status','due_date','notes','created_at','updated_at']")
    op.drop_column("projects", "folder_path")
