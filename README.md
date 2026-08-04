# MT Dataset Registry

A lightweight dataset registry for Machine Translation research: import raw corpora,
organize them into immutable batches, define logical datasets, and export reproducible
snapshots for training.

Metadata lives in PostgreSQL. Translation text lives in Parquet on object storage.
DuckDB queries the Parquet files directly — no Spark, no data lake, no workers.

## Quick start

```bash
docker compose up --build
```

| Service        | URL                    |
| -------------- | ---------------------- |
| UI             | http://localhost:5173  |
| API + OpenAPI  | http://localhost:8001/docs |
| MinIO console  | http://localhost:9001 (minioadmin / minioadmin) |
| Postgres       | localhost:5433 (mtreg / mtreg) |

Migrations run automatically when the backend container boots.

## Workflow

1. **Sources** — register where data comes from (WMT, Wikipedia, Human, …).
2. **Imports** — upload CSV/TSV/JSON/JSONL/TMX/XLSX/Parquet. Each import is normalized
   into one canonical schema and stored as an immutable batch (`batches/batch_N/data.parquet`),
   with per-sample metadata copied into Postgres.
3. **Datasets** — logical definitions: which batches, which filters. Nothing is copied.
4. **Splits** — reproducible train/validation/test ratios plus a seed.
5. **Snapshots** — materialize a dataset + split into `train/validation/test.parquet` and
   `manifest.json` (with row counts and SHA-256 per file).
6. **Experiments / Models** — record training runs and checkpoints, referencing a snapshot
   and an MLflow run id.

Annotations (ignore, tags, comments, quality, review status) never modify the original data;
ignored samples are filtered out at build time.

## Reproducibility

Splits are assigned by hashing `sample_id + seed` into a uniform bucket, so the same dataset
definition and seed always produce the same split — without shuffling or storing per-sample
split assignments. The manifest records the definition, the filters, the seed, the counts,
and a checksum for every exported file.

## Storage backends

Set `STORAGE_BACKEND`:

- `local` — filesystem under `STORAGE_ROOT`
- `s3` — Amazon S3 or MinIO (`S3_ENDPOINT_URL`)
- `gcs` — Google Cloud Storage through its S3 interoperability endpoint (HMAC keys)

All three sit behind `storage/base.py::Storage`; adding a backend means implementing five methods.

## Layout

```
backend/
  api/          REST routes (one module per resource)
  core/         config, logging, errors
  db/           engine, session, declarative base
  models/       SQLAlchemy entities
  schemas/      Pydantic request/response models
  services/
    ingestion/       readers + normalization + batch creation
    dataset_builder/ DuckDB query construction over Parquet
    snapshots/       immutable exports + manifest
    evaluation/      benchmark sets
  storage/      object storage interface + backends
  utils/        DuckDB helpers
frontend/       Vite + React + TypeScript + Tailwind
```

## Local development (without Docker)

```bash
# backend
cd backend
uv sync
DATABASE_URL=postgresql+asyncpg://mtreg:mtreg@localhost:5433/mtreg \
S3_ENDPOINT_URL=http://localhost:9000 \
uv run alembic upgrade head
uv run uvicorn main:app --reload

# frontend
cd frontend
yarn install
yarn dev
```

## Scale notes

Sample metadata is inserted with `COPY` in 200k-row chunks and ids are allocated in blocks
from a Postgres sequence, so ingestion stays fast at ~100M rows. Everything heavier than a
metadata lookup (search, previews, statistics on a dataset, snapshot builds) runs in DuckDB
directly against Parquet.
