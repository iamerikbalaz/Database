"""Durable one-way Notion synchronization and order folder operations."""
from alembic import op
import sqlalchemy as sa

revision = "20260930_0035"
down_revision = "20260930_0034"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("notion_sync_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("page_id", sa.String(36), unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("synced_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_id", name="uq_notion_sync_entity"))
    op.create_table("order_folder_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("internal_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("source_path", sa.String(2048)),
        sa.Column("target_name", sa.String(255), nullable=False),
        sa.Column("source_identity", sa.String(100)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("result_path", sa.String(2048)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_order_folder_request"))
    op.create_index("ix_order_folder_operations_order_id", "order_folder_operations", ["order_id"])


def downgrade():
    populated = op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM notion_sync_states) "
        "OR EXISTS(SELECT 1 FROM order_folder_operations)")).scalar_one()
    if populated:
        raise RuntimeError("Outbound queues contain durable work or history; use a forward migration")
    op.drop_table("order_folder_operations")
    op.drop_table("notion_sync_states")
