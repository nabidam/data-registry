"""persist import-time contamination quarantine

Revision ID: 8ab84e4c9191
Revises: b2c7a1d94e30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8ab84e4c9191"
down_revision: str | None = "b2c7a1d94e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "samples", sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("samples", "quarantined_at")
