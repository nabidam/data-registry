"""record when a dataset reservation applied its allocations

A reservation stamps one timestamp onto every row it protects, and it only ever
promotes rows that were TRAINABLE, so that timestamp uniquely identifies the rows
that reservation changed. Storing it makes the reservation reversible without
recording millions of sample ids: the revert matches on the timestamp.

Revision ID: a1b6d40c7f38
Revises: 9e2f5c81a704
"""

import sqlalchemy as sa
from alembic import op

revision: str = "a1b6d40c7f38"
down_revision: str | None = "9e2f5c81a704"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_reservations",
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_reservations", "applied_at")
