"""API schemas. Read models mirror the entities; write models stay minimal."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from services.dataset_builder.filters import DatasetFilters
from services.ingestion.normalize import ColumnMapping

__all__ = ["ColumnMapping", "DatasetFilters"]


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


# --- samples / annotations -------------------------------------------------
class SampleOut(ORMModel):
    id: int
    batch_id: int
    source_id: int | None
    src_lang: str
    tgt_lang: str
    domain: str | None
    quality: float | None
    status: str
    created_at: datetime


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
class DatasetIn(BaseModel):
    name: str
    description: str | None = None
    batch_ids: list[int] = []
    filters: DatasetFilters = DatasetFilters()


class DatasetOut(ORMModel):
    id: int
    name: str
    description: str | None
    batch_ids: list
    filters: dict
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


# --- evaluation sets -------------------------------------------------------
class EvaluationSetIn(BaseModel):
    name: str
    description: str | None = None
    kind: str = "sampled"
    filters: DatasetFilters = DatasetFilters()
    sample_ids: list[int] | None = None
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
