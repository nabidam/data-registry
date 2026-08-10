"""dataset reservation comparison scope

Adds the second contamination axis. ``contamination_scope`` already decides which
corpus a reservation scans; ``comparison_scope`` decides which rows inside it may
be compared with each other. Existing rows default to ``pair``, which is the safe
reading: a past reservation's report describes what it did, and nothing re-runs.

Revision ID: 9e2f5c81a704
Revises: 7d3c1a9b5e20
"""

import sqlalchemy as sa
from alembic import op

revision: str = "9e2f5c81a704"
down_revision: str | None = "7d3c1a9b5e20"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_reservations",
        sa.Column(
            "comparison_scope",
            sa.String(length=32),
            nullable=False,
            server_default="pair",
        ),
    )


def downgrade() -> None:
    op.drop_column("dataset_reservations", "comparison_scope")
