"""Allow historical materials without an assigned project; retain the foreign key."""
from alembic import op
import sqlalchemy as sa

revision = "20260925_0026"
down_revision = "20260919_0025"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("pbr_materials", "project_id", existing_type=sa.Uuid(), nullable=True)


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pbr_materials WHERE project_id IS NULL)")).scalar():
        raise RuntimeError("Assign projects to historical materials before downgrading; no records were changed.")
    op.alter_column("pbr_materials", "project_id", existing_type=sa.Uuid(), nullable=False)
