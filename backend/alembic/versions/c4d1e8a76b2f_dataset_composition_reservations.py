"""add dataset composition rules and reservation provenance

Revision ID: c4d1e8a76b2f
Revises: 8ab84e4c9191
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d1e8a76b2f"
down_revision: str | None = "8ab84e4c9191"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_definitions",
        sa.Column(
            "batch_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "dataset_definitions",
        sa.Column("composition_seed", sa.Integer(), nullable=False, server_default="42"),
    )
    op.create_table(
        "dataset_reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("selector", sa.String(length=32), nullable=False),
        sa.Column("percent", sa.Float(), nullable=False),
        sa.Column("max_samples", sa.Integer(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("contamination_scope", sa.String(length=32), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["dataset_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dataset_reservations_dataset", "dataset_reservations", ["dataset_id"]
    )
    op.create_table(
        "dataset_reservation_samples",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("reservation_id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.BigInteger(), nullable=False),
        sa.Column("evaluation_split", sa.String(length=16), nullable=False),
        sa.Column("human_verify", sa.Boolean(), nullable=False),
        sa.Column("annotations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["reservation_id"], ["dataset_reservations.id"]),
        sa.ForeignKeyConstraint(["sample_id"], ["samples.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reservation_id", "sample_id"),
    )
    op.create_index(
        "ix_dataset_reservation_samples_reservation",
        "dataset_reservation_samples",
        ["reservation_id"],
    )
    op.create_index(
        "ix_dataset_reservation_samples_sample",
        "dataset_reservation_samples",
        ["sample_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dataset_reservation_samples_sample",
        table_name="dataset_reservation_samples",
    )
    op.drop_index(
        "ix_dataset_reservation_samples_reservation",
        table_name="dataset_reservation_samples",
    )
    op.drop_table("dataset_reservation_samples")
    op.drop_index("ix_dataset_reservations_dataset", table_name="dataset_reservations")
    op.drop_table("dataset_reservations")
    op.drop_column("dataset_definitions", "composition_seed")
    op.drop_column("dataset_definitions", "batch_rules")
