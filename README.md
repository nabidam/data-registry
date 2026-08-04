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
3. **Evaluation reservation** — part of the same import: the selection pipeline holds a
   slice of every batch back as `RESERVED_EVALUATION` *before* the batch is eligible for
   training. Nothing downstream can opt out of this.
4. **Datasets** — logical definitions: which batches, which filters. Nothing is copied.
5. **Splits** — reproducible train/validation/test ratios plus a seed.
6. **Snapshots** — materialize a dataset + split into `train/validation/test.parquet` and
   `manifest.json` (with row counts and SHA-256 per file).
7. **Experiments / Models** — record training runs and checkpoints, referencing a snapshot
   and an MLflow run id.

Annotations (ignore, tags, comments, quality, review status) never modify the original data;
they only move a sample's allocation.

## Allocation

Every sample has exactly one allocation, decided during import:

| Allocation            | May appear in snapshots | May appear in evaluation sets |
| --------------------- | ----------------------- | ----------------------------- |
| `TRAINABLE`           | yes                     | no                            |
| `RESERVED_EVALUATION` | never                   | yes                           |
| `IGNORED`             | no                      | no                            |

The dataset builder is scoped to a single allocation and defaults to `TRAINABLE`, so reserved
and ignored samples are excluded with no configuration — there is no flag that lets them back in.
Reservation is permanent: `samples.reserved_at` is never cleared, so even un-ignoring a reserved
sample returns it to `RESERVED_EVALUATION`, never to the trainable pool.

### Reservation size and strategy

Reserved per import: `min(rows × EVALUATION_PERCENT / 100, EVALUATION_MAX_SAMPLES)` — e.g.
1,000,000 rows at 2% with a cap of 5,000 reserves 5,000 samples.

| Setting                  | Default     | Meaning                                    |
| ------------------------ | ----------- | ------------------------------------------ |
| `EVALUATION_PERCENT`     | `2.0`       | share of each import to reserve            |
| `EVALUATION_MAX_SAMPLES` | `5000`      | hard cap on the reserved count             |
| `EVALUATION_SELECTOR`    | `heuristic` | selection strategy                         |
| `RANDOM_SEED`            | `42`        | makes selection reproducible               |

Selection is **not** random by default. The `heuristic` selector scores each pair for benchmark
quality (sentence length, source/target length agreement, lexical richness, punctuation, noise
and URL penalties, quality hints) and then spreads the picks across language-pair/domain strata,
so the reserved pool mirrors the shape of the import. Strategies live in
`services/evaluation/selectors.py` and are registered by name; a new algorithm is one function
plus one dict entry, and every import can override the settings per request.

### Historical integrity

Snapshots are immutable and are never rewritten. If a sample is reserved *after* an existing
snapshot already exported it, the overlap is recorded in `snapshot_contaminations` and exposed
at `GET /snapshots/{id}/contamination` — that snapshot's evaluation sample is flagged as
historically contaminated instead of history being altered.

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
    evaluation/      selectors, import-time reservation, benchmark sets, contamination
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
