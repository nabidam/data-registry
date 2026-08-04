# AGENT.md

# Machine Translation Dataset Registry

## Overview

Build a **lightweight, maintainable Dataset Registry** for Machine Translation (MT) research.

The goal is **NOT** to build a general-purpose data lake, data warehouse, or enterprise MLOps platform.

The goal is to build a **simple internal tool** that allows researchers to:

* Import datasets
* Organize datasets
* Track metadata
* Build reproducible datasets
* Manage train/validation/test splits
* Manage multiple evaluation sets
* Export immutable snapshots for training
* Track experiments
* Scale to ~100M translation pairs

The system should be easy to understand, easy to extend, and easy for a single developer to maintain.

---

# Development Philosophy

## Priorities

1. Simplicity
2. Clean architecture
3. Readable code
4. Fast delivery
5. Low operational complexity

Avoid overengineering.

---

## Coding Style

* Keep the project small and modular.
* Favor composition over abstraction.
* Prefer explicit code over clever code.
* Avoid unnecessary design patterns.
* Keep dependencies minimal.
* Prefer standard libraries when reasonable.
* Build features incrementally.

---

## Do NOT

* Use TDD.
* Add enterprise patterns.
* Add microservices.
* Add CQRS.
* Add Event Sourcing.
* Add Kafka.
* Add Spark.
* Add Hadoop.
* Add Kubernetes-specific logic.
* Add unnecessary caching layers.
* Add unnecessary background workers.
* Introduce premature optimization.

---

# Technology Stack

## Backend

* Python 3.13+
* FastAPI
* SQLAlchemy
* Alembic
* Pydantic

---

## Database

PostgreSQL

Used only for metadata.

---

## Object Storage

Support

* MinIO
* Amazon S3
* Google Cloud Storage

Storage backend should be abstracted behind a small interface.

---

## Data Format

Parquet

Every ingestion batch becomes immutable Parquet files.

---

## Query Engine

DuckDB

Use DuckDB directly over Parquet files.

No Spark.

---

## Frontend

* React
* TypeScript

Keep the UI simple.

---

## Experiment Tracking

MLflow

Store only references.

---

# High-Level Architecture

```text
                     React UI
                        │
                        ▼
                  FastAPI Backend
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
 PostgreSQL        Dataset Builder     Object Storage
 Metadata            DuckDB           MinIO / S3 / GCS
```

---

# Storage Layout

```text
storage/

raw/

    batch_x/

normalized/

    batch_x/

batches/

    batch_x/

        data.parquet

snapshots/

    snapshot_x/

        train.parquet
        validation.parquet
        test.parquet
        manifest.json

evaluation_sets/

exports/
```

---

# Core Principles

## Immutable Data

Samples never change.

If a sample becomes invalid:

* mark as ignored
* never delete

---

## Append Only

New data always creates a new ingestion batch.

Existing batches are immutable.

---

## No Dataset Duplication

Never store:

* jan.csv
* jan_june.csv
* jan_june_july.csv

Instead, store dataset definitions.

---

## Reproducibility

Everything must be reproducible.

Every exported dataset should be rebuildable.

---

## Metadata Driven

Actual data lives in Parquet.

Everything else lives in PostgreSQL.

---

# Core Entities

## Sample

Immutable translation unit metadata.

Fields should include things like:

* id
* language pair
* domain
* batch
* source
* quality
* status
* timestamps

Actual text should live in Parquet.

---

## Batch

Represents one ingestion.

Examples:

* January 2026
* June Crawl
* Human Review Round 2

Immutable.

---

## Source

Represents where data originated.

Examples:

* WMT
* Wikipedia
* Common Crawl
* Human
* Synthetic

---

## Dataset Definition

Logical dataset.

Contains:

* included batches
* filters
* language pairs
* domains
* quality filters

No physical data.

---

## Snapshot

Immutable exported dataset.

Contains:

* dataset definition
* split definition
* sample ids
* random seed
* statistics
* exported Parquet files

---

## Split Definition

Represents

* train
* validation
* test

Supports multiple versions.

---

## Evaluation Set

Independent benchmark collections.

Examples:

* General
* Medical
* Legal
* OCR
* Terminology
* Human Curated

Reusable across experiments.

---

## Annotation

Per-sample metadata.

Examples:

* ignored
* comments
* review status
* quality
* tags

---

## Experiment

Represents one training run.

References:

* snapshot
* split
* model
* MLflow run

---

## Model

Produced checkpoints.

Evaluation metrics.

---

# Object Flow

```text
Batch

      │

      ▼

Sample

      │

      ▼

Dataset Definition

      │

      ▼

Snapshot

      │

      ▼

Split

      │

      ▼

Experiment

      │

      ▼

Model
```

---

# Dataset Workflow

## Step 1

Import raw data.

Supported formats:

* CSV
* TSV
* JSON
* JSONL
* TMX
* XLSX
* Parquet

Normalize everything into one canonical schema.

---

## Step 2

Create an immutable ingestion batch.

Store normalized data as Parquet.

---

## Step 3

Register metadata in PostgreSQL.

---

## Step 4

Create dataset definitions.

Datasets are logical collections.

No copying.

---

## Step 5

Generate train/validation/test splits.

Splits are reproducible using a random seed.

---

## Step 6

Build immutable snapshots.

Output:

* train.parquet
* validation.parquet
* test.parquet
* manifest.json

---

## Step 7

Train models.

Track experiments.

---

# Dataset Builder

Must support filtering by:

* language pair
* source
* domain
* tags
* quality
* batch
* ignored status

Should also support combining multiple batches.

---

# Splits

Support:

* multiple split versions
* reproducible random seed
* configurable ratios

Never modify existing splits.

---

# Evaluation Sets

Must support:

* manual creation
* sampled creation
* imported lists
* reusable benchmark collections

Independent from train/test splits.

---

# Annotation System

Allow users to:

* ignore sample
* add tags
* add comments
* assign quality
* review status

Never modify original data.

---

# Snapshot Export

Every snapshot must produce:

* train.parquet
* validation.parquet
* test.parquet
* manifest.json

Snapshots are immutable.

---

# Search

Support searching by:

* text
* sample id
* language pair
* domain
* source
* tags
* batch

---

# Statistics

Show statistics by:

* language pair
* domain
* source
* batch
* dataset
* snapshot
* evaluation set

---

# UI Pages

* Dashboard
* Samples
* Batches
* Sources
* Datasets
* Snapshots
* Splits
* Evaluation Sets
* Annotations
* Experiments
* Models
* Statistics
* Imports
* Exports
* Settings

---

# UI Principles

* Clean
* Minimal
* Fast
* Functional
* No unnecessary animations
* Responsive
* Research-oriented

---

# API Design

Prefer REST.

Simple endpoints.

Predictable resource names.

Avoid unnecessary nesting.

---

# Project Structure

```text
backend/

    api/

    core/

    db/

    models/

    schemas/

    services/

        ingestion/

        dataset_builder/

        snapshots/

        evaluation/

        experiments/

    storage/

    utils/

frontend/

    components/

    pages/

    hooks/

    services/

storage/

docs/
```

---

# Future Extensibility

Design so new features can be added without major refactoring.

Possible future additions:

* More importers
* More evaluation metrics
* More annotation types
* More storage backends
* Additional language pairs
* API integrations
* Active learning workflows

Do not implement these now.

---

# Success Criteria

The finished system should:

* Be understandable by a new developer in under one hour.
* Be maintainable by a single engineer.
* Support at least 100 million translation pairs.
* Keep all datasets reproducible.
* Never duplicate datasets unnecessarily.
* Separate metadata from data storage.
* Be simple to deploy.
* Be simple to extend.
* Favor correctness, clarity, and maintainability over unnecessary sophistication.
