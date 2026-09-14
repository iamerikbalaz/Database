"""Add credentials, server sessions, and database-backed login rate limits.

Revision ID: 20260914_0006
Revises: 20260909_0005
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260914_0006"
down_revision: str | Sequence[str] | None = "20260909_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["internal_users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(token_hash) = 64", name="ck_auth_sessions_token_hash"
        ),
        sa.CheckConstraint(
            "length(csrf_token) >= 43", name="ck_auth_sessions_csrf_token"
        ),
        sa.CheckConstraint(
            "absolute_expires_at > created_at",
            name="ck_auth_sessions_absolute_expiration",
        ),
        sa.CheckConstraint(
            "idle_expires_at > created_at AND idle_expires_at <= absolute_expires_at",
            name="ck_auth_sessions_idle_expiration",
        ),
        sa.CheckConstraint(
            "last_seen_at >= created_at", name="ck_auth_sessions_last_seen"
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_auth_sessions_revoked_at",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["internal_users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index(
        op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_auth_sessions_idle_expires_at"),
        "auth_sessions",
        ["idle_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_sessions_absolute_expires_at"),
        "auth_sessions",
        ["absolute_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_sessions_revoked_at"),
        "auth_sessions",
        ["revoked_at"],
        unique=False,
    )

    op.create_table(
        "auth_login_rate_limits",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_bucket", sa.BigInteger(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(key_hash) = 64", name="ck_auth_login_rate_limits_key_hash"
        ),
        sa.CheckConstraint(
            "attempt_count > 0", name="ck_auth_login_rate_limits_attempt_count"
        ),
        sa.PrimaryKeyConstraint("key_hash", "window_bucket"),
    )
    op.create_index(
        op.f("ix_auth_login_rate_limits_window_bucket"),
        "auth_login_rate_limits",
        ["window_bucket"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_auth_login_rate_limits_window_bucket"),
        table_name="auth_login_rate_limits",
    )
    op.drop_table("auth_login_rate_limits")
    op.drop_index(op.f("ix_auth_sessions_revoked_at"), table_name="auth_sessions")
    op.drop_index(
        op.f("ix_auth_sessions_absolute_expires_at"), table_name="auth_sessions"
    )
    op.drop_index(
        op.f("ix_auth_sessions_idle_expires_at"), table_name="auth_sessions"
    )
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("user_credentials")
