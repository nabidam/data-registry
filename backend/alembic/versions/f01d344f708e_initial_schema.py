"""initial schema

Revision ID: f01d344f708e
Revises:
Create Date: 2026-08-04 19:50:51.940721
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f01d344f708e"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sample ids are allocated in blocks from this sequence during ingestion so
    # the Parquet file and Postgres rows agree without a round trip per row.
    op.execute("CREATE SEQUENCE IF NOT EXISTS samples_id_seq AS BIGINT")

    op.create_table(
        "dataset_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("batch_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "evaluation_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("parquet_uri", sa.Text(), nullable=True),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=True),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("parquet_uri", sa.Text(), nullable=True),
        sa.Column("raw_uri", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "split_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("ratios", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "name", "version"),
    )
    op.create_table(
        "samples",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("src_lang", sa.String(length=16), nullable=False),
        sa.Column("tgt_lang", sa.String(length=16), nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=True),
        sa.Column("quality", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE samples ALTER COLUMN id SET DEFAULT nextval('samples_id_seq')")
    op.execute("ALTER SEQUENCE samples_id_seq OWNED BY samples.id")
    op.create_index("ix_samples_batch", "samples", ["batch_id"], unique=False)
    op.create_index("ix_samples_domain", "samples", ["domain"], unique=False)
    op.create_index("ix_samples_langpair", "samples", ["src_lang", "tgt_lang"], unique=False)
    op.create_index("ix_samples_status", "samples", ["status"], unique=False)
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("split_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("prefix_uri", sa.Text(), nullable=True),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dataset_definitions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["split_id"],
            ["split_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "annotations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("sample_id", sa.BigInteger(), nullable=False),
        sa.Column("ignored", sa.Boolean(), nullable=False),
        sa.Column("quality", sa.Float(), nullable=True),
        sa.Column("review_status", sa.String(length=32), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sample_id"],
            ["samples.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_annotations_sample", "annotations", ["sample_id"], unique=False)
    op.create_table(
        "experiments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("split_id", sa.Integer(), nullable=True),
        sa.Column("evaluation_set_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mlflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["split_id"],
            ["split_definitions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=True),
        sa.Column("checkpoint_uri", sa.Text(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("models")
    op.drop_table("experiments")
    op.drop_index("ix_annotations_sample", table_name="annotations")
    op.drop_table("annotations")
    op.drop_table("snapshots")
    op.drop_index("ix_samples_status", table_name="samples")
    op.drop_index("ix_samples_langpair", table_name="samples")
    op.drop_index("ix_samples_domain", table_name="samples")
    op.drop_index("ix_samples_batch", table_name="samples")
    op.drop_table("samples")
    op.drop_table("split_definitions")
    op.drop_table("batches")
    op.drop_table("sources")
    op.drop_table("evaluation_sets")
    op.drop_table("dataset_definitions")
    op.execute("DROP SEQUENCE IF EXISTS samples_id_seq")
