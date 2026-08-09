"""API schemas. Read models mirror the entities; write models stay minimal."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.allocation import Allocation
from services.dataset_builder.filters import DatasetFilters, EvaluationSetFilters
from services.ingestion.normalize import ColumnMapping

__all__ = ["Allocation", "ColumnMapping", "DatasetFilters", "EvaluationSetFilters"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- sources ---------------------------------------------------------------
class SourceIn(BaseModel):
    name: str
    kind: str = "corpus"
    description: str | None = None


class SourceOut(ORMModel):
    id: int
    name: str
    kind: str
    description: str | None
    created_at: datetime


# --- batches ---------------------------------------------------------------
class BatchIn(BaseModel):
    name: str
    source_id: int | None = None
    notes: str | None = None


class BatchOut(ORMModel):
    id: int
    name: str
    source_id: int | None
    status: str
    format: str | None
    sample_count: int
    parquet_uri: str | None
    raw_uri: str | None
    notes: str | None
    stats: dict | None
    created_at: datetime


class BatchRejectIn(BaseModel):
    confirm_name: str
    reason: str = Field(min_length=3, max_length=2_000)


class BatchRestoreIn(BaseModel):
    confirm_name: str


class BatchPurgeIn(BaseModel):
    confirm_name: str
    reason: str = Field(min_length=10, max_length=2_000)


class BatchPurgeImpact(BaseModel):
    batch_id: int
    batch_name: str
    status: str
    sample_count: int
    can_purge: bool
    blockers: list[str]
    warnings: list[str]
    storage_prefixes: list[str]


class BatchPurgeOut(BaseModel):
    batch_id: int
    batch_name: str
    audit_id: int
    deleted_samples: int
    deleted_objects: int


# --- samples / annotations -------------------------------------------------
class SampleOut(ORMModel):
    id: int
    batch_id: int
    source_id: int | None
    src_lang: str
    tgt_lang: str
    domain: str | None
    quality: float | None
    allocation: str
    reserved_at: datetime | None
    quarantined_at: datetime | None
    created_at: datetime


class AllocationIn(BaseModel):
    """Move existing samples between allocations. Reservation is irreversible."""

    sample_ids: list[int]
    allocation: Allocation
    reason: str | None = None


class AnnotationIn(BaseModel):
    sample_id: int
    ignored: bool = False
    quality: float | None = None
    review_status: str | None = None
    tags: list[str] | None = None
    comment: str | None = None
    author: str | None = None


class AnnotationOut(ORMModel):
    id: int
    sample_id: int
    ignored: bool
    quality: float | None
    review_status: str | None
    tags: list | None
    comment: str | None
    author: str | None
    created_at: datetime


# --- dataset definitions ---------------------------------------------------
class BatchRule(BaseModel):
    """Deterministic contribution from one immutable batch."""

    batch_id: int = Field(gt=0)
    mode: Literal["all", "percent", "count"] = "all"
    value: float | int | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "BatchRule":
        if self.mode == "all":
            self.value = None
            return self
        if self.value is None:
            raise ValueError(f"{self.mode} batch rules require a value")
        if self.mode == "percent" and not 0 < float(self.value) <= 100:
            raise ValueError("batch-rule percent must be greater than 0 and at most 100")
        if self.mode == "count" and (
            isinstance(self.value, bool)
            or float(self.value) < 1
            or not float(self.value).is_integer()
        ):
            raise ValueError("batch-rule count must be a positive whole number")
        return self


class DatasetIn(BaseModel):
    name: str
    description: str | None = None
    batch_ids: list[int] = []
    batch_rules: list[BatchRule] = []
    composition_seed: int = 42
    filters: DatasetFilters = DatasetFilters()

    @model_validator(mode="after")
    def validate_batch_rules(self) -> "DatasetIn":
        ids = [rule.batch_id for rule in self.batch_rules]
        if len(ids) != len(set(ids)):
            raise ValueError("each batch may appear only once in batch_rules")
        return self


class DatasetOut(ORMModel):
    id: int
    name: str
    description: str | None
    batch_ids: list
    batch_rules: list
    composition_seed: int
    filters: dict
    created_at: datetime


class DatasetReservationIn(BaseModel):
    selector: Literal["contamination_safe", "heuristic", "random"] = "contamination_safe"
    percent: float = Field(default=1.0, ge=0, le=100)
    max_samples: int = Field(default=10_000, ge=0)
    target_count: int | None = Field(default=None, ge=0)
    seed: int = 42
    contamination_scope: Literal["registry", "composition"] = "registry"
    confirm_irreversible: Literal[True]


class DatasetReservationOut(ORMModel):
    id: int
    dataset_id: int
    status: str
    selector: str
    percent: float
    max_samples: int
    target_count: int | None
    seed: int
    contamination_scope: str
    report: dict | None
    error: str | None
    created_at: datetime


# --- splits ----------------------------------------------------------------
class SplitIn(BaseModel):
    dataset_id: int
    name: str = "default"
    version: int = 1
    seed: int = 42
    ratios: dict[str, float] = {"train": 0.9, "validation": 0.05, "test": 0.05}


class SplitOut(ORMModel):
    id: int
    dataset_id: int
    name: str
    version: int
    seed: int
    ratios: dict
    created_at: datetime


# --- snapshots -------------------------------------------------------------
class SnapshotIn(BaseModel):
    name: str
    dataset_id: int
    split_id: int | None = None
    seed: int = 42


class SnapshotOut(ORMModel):
    id: int
    name: str
    dataset_id: int
    split_id: int | None
    status: str
    seed: int
    prefix_uri: str | None
    manifest: dict | None
    stats: dict | None
    error: str | None
    created_at: datetime


class ContaminationOut(ORMModel):
    """A past snapshot that already exported samples now reserved for evaluation."""

    id: int
    snapshot_id: int
    sample_count: int
    sample_ids: list | None
    reason: str | None
    created_at: datetime


# --- evaluation sets -------------------------------------------------------
class EvaluationSetIn(BaseModel):
    name: str
    description: str | None = None
    kind: str = "sampled"
    filters: EvaluationSetFilters = EvaluationSetFilters()
    sample_ids: list[int] | None = None
    reservation_id: int | None = None
    limit: int | None = None
    seed: int = 42


class EvaluationSetOut(ORMModel):
    id: int
    name: str
    description: str | None
    kind: str
    parquet_uri: str | None
    sample_count: int
    spec: dict | None
    created_at: datetime


# --- experiments / models --------------------------------------------------
class ExperimentIn(BaseModel):
    name: str
    snapshot_id: int | None = None
    split_id: int | None = None
    evaluation_set_ids: list[int] | None = None
    mlflow_run_id: str | None = None
    status: str = "planned"
    notes: str | None = None


class ExperimentOut(ORMModel):
    id: int
    name: str
    snapshot_id: int | None
    split_id: int | None
    evaluation_set_ids: list | None
    mlflow_run_id: str | None
    status: str
    notes: str | None
    created_at: datetime


class ModelIn(BaseModel):
    name: str
    experiment_id: int | None = None
    checkpoint_uri: str | None = None
    metrics: dict[str, Any] | None = None
    notes: str | None = None


class ModelOut(ORMModel):
    id: int
    name: str
    experiment_id: int | None
    checkpoint_uri: str | None
    metrics: dict | None
    notes: str | None
    created_at: datetime


# --- misc ------------------------------------------------------------------
class Page(BaseModel):
    total: int
    items: list[Any]
