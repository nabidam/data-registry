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

from core.allocation import Allocation
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
    """One immutable ingestion. Its normalized rows live in Parquet shards."""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    status: Mapped[str] = mapped_column(
        String(32), default="ready"
    )  # queued | importing | ready | failed
    format: Mapped[str | None] = mapped_column(String(16))
    sample_count: Mapped[int] = mapped_column(BigInteger, default=0)
    parquet_uri: Mapped[str | None] = mapped_column(Text)
    raw_uri: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict | None] = mapped_column(JSONB)

    source: Mapped["Source | None"] = relationship(back_populates="batches")


class BatchPurgeAudit(Base, TimestampMixin):
    """Minimal administrative record that a batch was intentionally purged."""

    __tablename__ = "batch_purge_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_batch_purge_audits_batch_id", "batch_id"),)


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
    # TRAINABLE | RESERVED_EVALUATION | QUARANTINED | IGNORED — see core/allocation.py.
    allocation: Mapped[str] = mapped_column(String(24), default=Allocation.TRAINABLE)
    # Set the first time a sample is reserved and never cleared: reservation is
    # permanent, so a sample can never drift back into the trainable pool.
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set for rows excluded by an import-time or dataset-level contamination pass. Like
    # reservation, quarantine is permanent so an annotation cannot leak the
    # row back into a later training snapshot.
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_samples_batch", "batch_id"),
        Index("ix_samples_langpair", "src_lang", "tgt_lang"),
        Index("ix_samples_domain", "domain"),
        Index("ix_samples_allocation", "allocation"),
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
    """Logical dataset: batch composition plus filters. Holds no physical data."""

    __tablename__ = "dataset_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    batch_ids: Mapped[list] = mapped_column(JSONB, default=list)
    # Optional additive workflow for deterministic per-batch composition. Empty
    # keeps the original batch_ids behavior unchanged.
    batch_rules: Mapped[list] = mapped_column(JSONB, default=list)
    composition_seed: Mapped[int] = mapped_column(Integer, default=42)
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)


class DatasetReservation(Base, TimestampMixin):
    """One auditable reservation run over a logical dataset composition."""

    __tablename__ = "dataset_reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_definitions.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="queued")
    selector: Mapped[str] = mapped_column(String(32), default="contamination_safe")
    percent: Mapped[float] = mapped_column(Float, default=1.0)
    max_samples: Mapped[int] = mapped_column(Integer, default=10_000)
    target_count: Mapped[int | None] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    # Two orthogonal axes. contamination_scope decides which corpus is scanned;
    # comparison_scope decides which rows within it may be compared with each other.
    contamination_scope: Mapped[str] = mapped_column(String(32), default="registry")
    comparison_scope: Mapped[str] = mapped_column(
        String(32), default="pair", server_default="pair", nullable=False
    )
    report: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_dataset_reservations_dataset", "dataset_id"),)


class DatasetReservationSample(Base):
    """Generated evaluation metadata for samples selected by a reservation run."""

    __tablename__ = "dataset_reservation_samples"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reservation_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_reservations.id"), nullable=False
    )
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), nullable=False)
    evaluation_split: Mapped[str] = mapped_column(String(16))
    human_verify: Mapped[bool] = mapped_column(Boolean, default=False)
    annotations: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("reservation_id", "sample_id"),
        Index("ix_dataset_reservation_samples_reservation", "reservation_id"),
        Index("ix_dataset_reservation_samples_sample", "sample_id"),
    )


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


class SnapshotContamination(Base, TimestampMixin):
    """Overlap between an existing snapshot and samples reserved after the fact.

    Snapshots are never rewritten; this table is the record that a past export
    contains data now held back for evaluation.
    """

    __tablename__ = "snapshot_contaminations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"), nullable=False)
    sample_count: Mapped[int] = mapped_column(BigInteger, default=0)
    sample_ids: Mapped[list | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (Index("ix_contaminations_snapshot", "snapshot_id"),)


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
