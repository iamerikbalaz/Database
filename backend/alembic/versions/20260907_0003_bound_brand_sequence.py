"""Bound the published brand sequence number.

Revision ID: 20260907_0003
Revises: 20260904_0002
Create Date: 2026-09-07
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260907_0003"
down_revision: str | Sequence[str] | None = "20260904_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_published_brands_next_sequence_number_positive",
        "published_brands",
        type_="check",
    )
    op.create_check_constraint(
        "ck_published_brands_next_sequence_number_range",
        "published_brands",
        "next_sequence_number BETWEEN 1 AND 9999",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_published_brands_next_sequence_number_range",
        "published_brands",
        type_="check",
    )
    op.create_check_constraint(
        "ck_published_brands_next_sequence_number_positive",
        "published_brands",
        "next_sequence_number >= 1",
    )
