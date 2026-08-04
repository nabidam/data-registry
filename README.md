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
| `QUARANTINED`         | no                      | no                            |
| `IGNORED`             | no                      | no                            |

The dataset builder is scoped to a single allocation and defaults to `TRAINABLE`, so reserved,
quarantined, and ignored samples are excluded with no configuration — there is no flag that lets
them back in.
Reservation and contamination quarantine are permanent: `samples.reserved_at` and
`samples.quarantined_at` are never cleared, so an un-ignored protected row cannot return to the
trainable pool.

### Reservation size and strategy

Reserved per import: `min(rows × EVALUATION_PERCENT / 100, EVALUATION_MAX_SAMPLES)` — e.g.
1,000,000 rows at 2% with a cap of 5,000 reserves 5,000 samples.

| Setting                  | Default     | Meaning                                    |
| ------------------------ | ----------- | ------------------------------------------ |
| `EVALUATION_PERCENT`     | `2.0`       | share of each import to reserve            |
| `EVALUATION_MAX_SAMPLES` | `5000`      | hard cap on the reserved count             |
| `EVALUATION_SELECTOR`    | `contamination_safe` | selection strategy              |
| `RANDOM_SEED`            | `42`        | makes selection reproducible               |

Selection is **not** random by default. `contamination_safe` ports the supplied test-set builder's
full pipeline: feature annotation (source length, math, numbers/units, acronyms, mixed script, and
rare terms); exact, MinHash, and embedding deduplication; domain × source-length quotas;
flattened or proportional domain allocation; candidate-document restriction; k-center diversity
selection; hard-phenomenon top-ups; document holdout; and a cross-document embedding purge. The
importer assigns every selected benchmark row to `dev` or `test` with a deterministic stratified
split and records its `human_verify` gold-subset flag in the immutable batch Parquet.

Its policy lives in `backend/config/evaluation_reservation.yaml`; set
`EVALUATION_RESERVATION_CONFIG` to a deployment copy. The import target still comes from
`EVALUATION_PERCENT` and `EVALUATION_MAX_SAMPLES`, rather than the standalone script's
`total_size`. `heuristic` and `random` remain available as per-import overrides.

Map **Document ID column** on Imports when the source has document/chunk IDs. The default `:`
separator turns `paper-17:004` into document `paper-17`; configure `input.id_separator` in the
policy for another format. With no mapping, each pair is treated as its own document, so only
near-duplicate protection can quarantine additional rows.

The default policy enables MinHash and LaBSE (`sentence-transformers/LaBSE`). Backend dependencies
include `numpy`, `scikit-learn`, `datasketch`, `sentence-transformers`, and CPU-compatible `torch`.
On the first eligible `contamination_safe` import, sentence-transformers downloads the configured
model into its Hugging Face cache if it is absent. Later imports reuse that cache. This fetch does
not download a Docker image. Set `embeddings.enabled: false` only when an explicit TF-IDF fallback
fits the deployment; the batch statistics identify the embedding backend used.

`RESERVED_EVALUATION` contains only the selected benchmark rows. Duplicate rows removed during
exact, MinHash, or embedding deduplication, plus rows excluded because they share a selected
document or exceed the cross-document near-duplicate threshold, are `QUARANTINED`. They cannot
enter a training snapshot or an evaluation set.

### Reservation records

Each import stores selection annotations in its immutable batch Parquet: `document_id`, feature
flags, `evaluation_split` (`dev` or `test` for reserved rows), and `human_verify`. Batch statistics
store the policy snapshot, seed, feature and quota results, each deduplication and quarantine count,
allocation counts, selected-ID hash, and SHA-256 of the batch Parquet. Use those records to compare
repeated imports or to audit an evaluation-set materialization without changing the batch.

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
  config/        deployment-owned reservation policy
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
