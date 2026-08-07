# MT Dataset Registry

A lightweight dataset registry for Machine Translation research: import raw corpora,
organize them into immutable batches, define logical datasets, and export reproducible
snapshots for training.

Metadata lives in PostgreSQL. Translation text lives in Parquet on object storage.
DuckDB queries the Parquet files directly — no Spark or data lake. Heavy imports
run in one small restartable worker so the API process remains responsive.

## Quick start

The default Compose file expects an externally managed PostgreSQL database. Create `.env` from
the example and set `DATABASE_URL` to a URI reachable from the backend container:

```bash
cp .env.example .env
# Edit DATABASE_URL in .env
docker compose up --build
```

For a self-contained local stack, including PostgreSQL, use the full Compose file instead:

```bash
docker compose -f docker-compose.full.yml up --build
```

This local Compose build deliberately omits the optional evaluation group, so the host does not
need LaBSE, PyTorch, or MinHash installed. Use `heuristic` or `random` reservations locally. To
build the production backend image with the complete evaluation pipeline, run:

```bash
docker build --build-arg INSTALL_EVALUATION=true -t mtdataregistry-backend:full ./backend
```

The full image runs `contamination_safe`; its first eligible import may download the LaBSE model
into the host Hugging Face cache, mounted at `/root/.cache/huggingface` in the backend container.
Later container runs reuse it. Override the host location with `HOST_HF_CACHE_DIR` when needed.
Both image variants install strictly from the committed `backend/uv.lock` file.
Compose runs the code and dependency environment baked into each image; it does
not bind-mount the source tree over `/app`. Rebuild after changing source code.

For either Compose setup, set `INSTALL_EVALUATION=true` in `.env` and rebuild with
`docker compose up --build` to include the optional evaluation dependencies.

| Service        | URL                    |
| -------------- | ---------------------- |
| UI             | http://localhost:5173  |
| API + OpenAPI  | http://localhost:8001/docs |
| MinIO console  | http://localhost:9001 (minioadmin / minioadmin) |
| Postgres (full Compose only) | localhost:5433 (mtreg / mtreg) |

Migrations run automatically when the backend container boots. The worker waits
for the backend healthcheck and never runs Alembic itself.

## Workflow

1. **Sources** — register where data comes from (WMT, Wikipedia, Human, …).
2. **Imports** — upload CSV/TSV/JSON/JSONL/TMX/XLSX/Parquet. The browser sends large files to
   MinIO in bounded 16 MB multipart requests, then the API queues the import immediately for the
   dedicated worker. Large CSV/TSV files are normalized in bounded frames instead of being loaded
   into RAM as one DataFrame. Each immutable batch is stored as canonical Parquet shards
   (`batches/batch_N/attempt_ID/part-*.parquet`) with per-sample metadata copied into Postgres. Set
   `IMPORT_UPLOAD_PART_SIZE_MB` (minimum 5), `IMPORT_BATCH_ROWS`, and `PARQUET_SHARD_SIZE_MB`
   (default 256) to suit the deployment's proxy, memory, and object-size limits.
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

Selection is **not** random by default. `contamination_safe` adapts the supplied test-set builder's
pipeline stages: feature annotation (source length, math, numbers/units, acronyms, mixed script, and
rare terms); exact, MinHash, and embedding deduplication; domain × source-length quotas;
flattened or proportional domain allocation; candidate-document restriction; k-center diversity
selection; hard-phenomenon top-ups; document holdout; and a cross-document embedding purge. The
importer assigns every selected benchmark row to `dev` or `test` with a deterministic stratified
split and records its `human_verify` gold-subset flag in the immutable batch Parquet.

For CSV/TSV files above `IMPORT_STREAM_THRESHOLD_MB` (default 512), expensive evaluation selection
runs over a deterministic bounded reservoir (`EVALUATION_CANDIDATE_LIMIT`, default 50,000). The
actual expensive selector input is at most the requested evaluation size multiplied by
`EVALUATION_CANDIDATE_MULTIPLIER` (default 10), capped by that reservoir. Selecting 1,000 rows
therefore runs LaBSE over 10,000 candidates rather than all 50,000.
After selection, the worker streams over **every row in the complete corpus** and quarantines rows
from selected documents and exact evaluation duplicates. A row becomes a semantic-verification
candidate when its source or target shares at least two normalized three-token shingles with the
corresponding side of the reserved evaluation pool. Candidate source text is then verified with
LaBSE cosine similarity before quarantine. This catches a shared four-token passage or separated
phrase overlap that the previous five-token gate missed, without admitting every row containing one
generic trigram. LaBSE—not lexical overlap—makes the quarantine decision. A semantic rewrite
without enough shared phrasing can still evade this scalable prefilter; use a stricter offline audit
when that residual risk is unacceptable. Deployments can set
`contamination.semantic_prefilter_min_shared_shingles: 1` for maximum lexical recall at the cost of
more LaBSE work, especially for short or repetitive text.

Its policy lives in `backend/config/evaluation_reservation.yaml`; set
`EVALUATION_RESERVATION_CONFIG` to a deployment copy. The import target still comes from
`EVALUATION_PERCENT` and `EVALUATION_MAX_SAMPLES`, rather than the standalone script's
`total_size`. `heuristic` and `random` remain available as per-import overrides.

Map **Document ID column** on Imports when the source has document/chunk IDs. The default `:`
separator turns `paper-17:004` into document `paper-17`; configure `input.id_separator` in the
policy for another format. With no mapping, each pair is treated as its own document, so only
near-duplicate protection can quarantine additional rows.

The default policy enables MinHash and LaBSE (`sentence-transformers/LaBSE`). Its heavy runtime
dependencies (`numpy`, `scikit-learn`, `datasketch`, `sentence-transformers`, and CPU-compatible
`torch`) are isolated in the backend `evaluation` dependency group. The ordinary API can run
without them; install the group with `cd backend && uv sync --group evaluation` before using
`contamination_safe`, or build the production image with `INSTALL_EVALUATION=true`.
On the first eligible `contamination_safe` import, sentence-transformers downloads the configured
model into its Hugging Face cache if it is absent. Later imports reuse that cache. This fetch does
not download a Docker image. Set `embeddings.enabled: false` only when an explicit TF-IDF fallback
fits the deployment; the batch statistics identify the embedding backend used.

`RESERVED_EVALUATION` contains only the selected benchmark rows. Duplicate rows removed during
exact, MinHash, or embedding deduplication, plus rows excluded because they share a selected
document or exceed the cross-document near-duplicate threshold, are `QUARANTINED`. They cannot
enter a training snapshot or an evaluation set.

### Import worker operations

All import entry points finish by writing a durable `queued` job to PostgreSQL. A single dedicated
worker claims jobs and records its attempt, phase, heartbeat, row count, shard count, and latest
message in `batches.stats`. The Imports page polls only while a job is active and shows the same
progress that appears in worker logs.

The worker uses a PostgreSQL advisory lock, so accidentally starting a second worker does not run
the same import twice. After a worker restart, an interrupted job is requeued and writes to a new
attempt-specific object-storage prefix. A batch points to that prefix only after its PostgreSQL
metadata transaction commits, so partial files from a failed attempt are never queried.

Follow production progress with:

```bash
docker compose -f docker-compose.full.yml logs -f worker
```

Expected phases are `queued`, `claimed`, `downloading`, `normalizing`,
`selecting_evaluation`, `preparing_contamination_scan`, `scanning_contamination`,
`scanning_and_publishing`, and `complete`. Selection reports its current substage, including
annotation, exact and MinHash deduplication, chunked LaBSE inference, embedding deduplication,
document filtering, diversity selection, and contamination checks. Logs and UI include processed
items, totals, and elapsed stage time, so a slow model load or CPU inference is distinguishable
from a dead worker.
Failures retain the exception type and message on the batch and in the worker log. The Imports page
offers **Retry** for failed jobs; it reuses the raw object and creates a new isolated attempt.
An `importing` row whose heartbeat is older than one minute is highlighted as stale in the UI.

Compose limits the worker separately with `IMPORT_WORKER_CPUS` (default `2.0`) and
`IMPORT_WORKER_MEMORY_LIMIT` (default `8g`). Temporary raw and Parquet files use the dedicated
`importwork` volume. Size that volume for the raw upload plus normalized and final Parquet staging;
large contamination-safe imports trade elapsed time for bounded memory and API responsiveness.
LaBSE uses CPU unless the container can see a supported GPU. On CPU, model inference is normally
the longest selection stage. Candidate embedding dedup uses deterministic random-hyperplane LSH
instead of an all-pairs nearest-neighbor matrix, and k-center selection uses a deterministic
128-dimensional projection rather than repeatedly scanning all 768 LaBSE dimensions.

PostgreSQL is intentionally the job queue. Celery and Redis are not required for the registry's
single sequential import workload; revisit that decision only if the system needs multiple job
classes, priorities, scheduling, or concurrent workers.

### Reservation records

Each import stores `document_id`, `evaluation_split` (`dev` or `test` for reserved rows), and
`human_verify` in its immutable batch Parquet. Contamination-safe feature annotations are populated
for rows evaluated by the selector; they are null outside the bounded candidate pool rather than
being reported as false. Batch statistics store the policy snapshot, seed, feature and quota
results, each deduplication and quarantine count, full-corpus scan outcomes, allocation counts,
selected-ID hash, and SHA-256 for every Parquet shard.

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
DATABASE_URL=postgresql+psycopg://mtreg:mtreg@localhost:5433/mtreg \
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
