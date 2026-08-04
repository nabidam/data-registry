"""All metadata entities.

Design rules (see AGENTS.md):
* Postgres stores metadata only; translation text lives in Parquet.
* Data is append-only: batches and snapshots are never mutated.
* Invalid samples are marked ignored via annotations, never deleted.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

sample_id_seq = Sequence("samples_id_seq")


class Source(Base, TimestampMixin):
    """Where data originated: WMT, Wikipedia, Common Crawl, Human, Synthetic..."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default="corpus")
    description: Mapped[str | None] = mapped_column(Text)

    batches: Mapped[list["Batch"]] = relationship(back_populates="source")


class Batch(Base, TimestampMixin):
    """One immutable ingestion. Its normalized rows live in a single Parquet file."""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    status: Mapped[str] = mapped_column(String(32), default="ready")  # ready | failed
    format: Mapped[str | None] = mapped_column(String(16))
    sample_count: Mapped[int] = mapped_column(BigInteger, default=0)
    parquet_uri: Mapped[str | None] = mapped_column(Text)
    raw_uri: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict | None] = mapped_column(JSONB)

    source: Mapped["Source | None"] = relationship(back_populates="batches")


class Sample(Base):
    """Immutable translation-unit metadata. Text lives in the batch Parquet file."""

    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(BigInteger, sample_id_seq, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    src_lang: Mapped[str] = mapped_column(String(16), nullable=False)
    tgt_lang: Mapped[str] = mapped_column(String(16), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(64))
    quality: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | ignored
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_samples_batch", "batch_id"),
        Index("ix_samples_langpair", "src_lang", "tgt_lang"),
        Index("ix_samples_domain", "domain"),
        Index("ix_samples_status", "status"),
    )


class Annotation(Base, TimestampMixin):
    """Per-sample mutable metadata. Original data is never touched."""

    __tablename__ = "annotations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), nullable=False)
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    quality: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str | None] = mapped_column(String(32))
    tags: Mapped[list | None] = mapped_column(JSONB)
    comment: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (Index("ix_annotations_sample", "sample_id"),)


class DatasetDefinition(Base, TimestampMixin):
    """Logical dataset: included batches + filters. Holds no physical data."""

    __tablename__ = "dataset_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    batch_ids: Mapped[list] = mapped_column(JSONB, default=list)
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)


class SplitDefinition(Base, TimestampMixin):
    """Reproducible train/validation/test recipe. Existing splits are never modified."""

    __tablename__ = "split_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset_definitions.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    ratios: Mapped[dict] = mapped_column(JSONB, default=dict)  # {train,validation,test}

    __table_args__ = (UniqueConstraint("dataset_id", "name", "version"),)


class Snapshot(Base, TimestampMixin):
    """Immutable exported dataset: parquet splits + manifest."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("dataset_definitions.id"), nullable=False)
    split_id: Mapped[int | None] = mapped_column(ForeignKey("split_definitions.id"))
    status: Mapped[str] = mapped_column(String(32), default="building")
    seed: Mapped[int] = mapped_column(Integer, default=42)
    prefix_uri: Mapped[str | None] = mapped_column(Text)
    manifest: Mapped[dict | None] = mapped_column(JSONB)
    stats: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class EvaluationSet(Base, TimestampMixin):
    """Independent benchmark collection, reusable across experiments."""

    __tablename__ = "evaluation_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="manual")  # manual|sampled|imported
    parquet_uri: Mapped[str | None] = mapped_column(Text)
    sample_count: Mapped[int] = mapped_column(BigInteger, default=0)
    spec: Mapped[dict | None] = mapped_column(JSONB)


class Experiment(Base, TimestampMixin):
    """One training run. Only references are stored, never artifacts."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("snapshots.id"))
    split_id: Mapped[int | None] = mapped_column(ForeignKey("split_definitions.id"))
    evaluation_set_ids: Mapped[list | None] = mapped_column(JSONB)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="planned")
    notes: Mapped[str | None] = mapped_column(Text)


class Model(Base, TimestampMixin):
    """Produced checkpoint plus its evaluation metrics."""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    experiment_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id"))
    checkpoint_uri: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
