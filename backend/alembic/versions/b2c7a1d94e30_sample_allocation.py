"""sample allocation + snapshot contamination

Replaces samples.status (active|ignored) with samples.allocation
(TRAINABLE|RESERVED_EVALUATION|IGNORED) and adds the table recording
historically contaminated snapshots.

Revision ID: b2c7a1d94e30
Revises: f01d344f708e
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2c7a1d94e30"
down_revision: str | None = "f01d344f708e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("samples", sa.Column("allocation", sa.String(length=24), nullable=True))
    op.add_column("samples", sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True))
    # Existing data predates reservation: everything active becomes trainable.
    op.execute(
        "UPDATE samples SET allocation = "
        "CASE WHEN status = 'ignored' THEN 'IGNORED' ELSE 'TRAINABLE' END"
    )
    op.alter_column("samples", "allocation", nullable=False)
    op.drop_index("ix_samples_status", table_name="samples")
    op.drop_column("samples", "status")
    op.create_index("ix_samples_allocation", "samples", ["allocation"], unique=False)

    op.create_table(
        "snapshot_contaminations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("sample_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_contaminations_snapshot", "snapshot_contaminations", ["snapshot_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_contaminations_snapshot", table_name="snapshot_contaminations")
    op.drop_table("snapshot_contaminations")
    op.add_column("samples", sa.Column("status", sa.String(length=16), nullable=True))
    op.execute(
        "UPDATE samples SET status = "
        "CASE WHEN allocation = 'IGNORED' THEN 'ignored' ELSE 'active' END"
    )
    op.alter_column("samples", "status", nullable=False)
    op.drop_index("ix_samples_allocation", table_name="samples")
    op.drop_column("samples", "allocation")
    op.drop_column("samples", "reserved_at")
    op.create_index("ix_samples_status", "samples", ["status"], unique=False)
