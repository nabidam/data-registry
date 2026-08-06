# ADR 002: use one durable PostgreSQL-backed import worker

## Decision

CPU-, memory-, disk-, and database-intensive ingestion runs in a dedicated worker process rather
than a FastAPI background task. Every upload persists its complete job specification and progress
on the batch before returning `202 Accepted`. The API never normalizes, embeds, or publishes an
import inside a request process.

PostgreSQL is the queue. The worker holds a session-level advisory lock, claims only `queued` jobs,
and runs one import at a time. An interrupted `importing` job is requeued only after a replacement
worker owns that singleton lock. Progress includes attempt ID, heartbeat, phase, message, processed
rows, and published shards.
CPU-heavy selection additionally reports its current substage, processed/total items, and elapsed
stage time. LaBSE inference is chunked so heartbeats and progress continue during model work.

Every attempt writes Parquet to its own object-storage prefix. The batch's `parquet_uri` is updated
in the same final metadata transaction that marks the batch `ready`. Failed-attempt objects are
therefore orphaned but never visible through the registry and can be cleaned up separately.

The API container alone runs Alembic. Compose waits for the API healthcheck before starting the
worker, gives the worker a dedicated temporary-work volume, and applies separate CPU and memory
limits.

## Rationale

FastAPI background tasks share the API process and disappear on restart, which is unsafe for
multi-gigabyte imports and model inference. A dedicated worker is a necessary process-isolation
seam, not a general background-processing platform.

Celery and Redis are deliberately excluded. One sequential importer does not need another
datastore or a distributed scheduling system. They may be reconsidered if the registry later needs
multiple independent job classes, priorities, scheduled execution, or safe concurrent workers.

## Consequences

Imports remain non-blocking for API traffic and restartable from the immutable raw object. A retry
repeats normalization and may leave an unreferenced attempt prefix, but it cannot mix partial
Parquet output with a successful batch. Operators monitor the Imports page or worker logs and must
provision enough worker memory and work-volume capacity for LaBSE and temporary Parquet files.
