"""add minimal batch purge audit records

Revision ID: 7d3c1a9b5e20
Revises: c4d1e8a76b2f
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7d3c1a9b5e20"
down_revision: str | None = "c4d1e8a76b2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "batch_purge_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("batch_name", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_batch_purge_audits_batch_id",
        "batch_purge_audits",
        ["batch_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_batch_purge_audits_batch_id", table_name="batch_purge_audits")
    op.drop_table("batch_purge_audits")
