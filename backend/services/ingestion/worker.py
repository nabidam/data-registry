"""Durable, singleton import worker backed by PostgreSQL.

The worker deliberately handles one import at a time. Imports are CPU, memory,
disk, and database intensive; parallel execution on one registry host makes the
API less reliable without improving useful throughput.
"""

import asyncio
import logging
import shutil
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select, text

from core.config import settings
from core.logging import setup_logging
from db.session import SessionLocal, engine
from models import Batch
from schemas import ColumnMapping
from services.evaluation.reservation import ReservationPolicy
from services.ingestion.service import batch_keys, ingest_stored_file
from storage import get_storage

log = logging.getLogger(__name__)
POLL_SECONDS = 2
RETRY_SECONDS = 10
HEARTBEAT_SECONDS = 15
WORKER_LOCK_ID = 1_294_671_823
ACTIVE_STATUSES = {"queued", "importing"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _filename(batch: Batch) -> str:
    if not batch.raw_uri:
        raise ValueError("import batch has no raw object")
    name = Path(urlparse(batch.raw_uri).path).name
    if not name or name in {".", ".."}:
        raise ValueError("import batch has no raw filename")
    return name


def _cleanup_stale_work() -> tuple[int, int]:
    """Remove attempt-local files left by an unclean worker shutdown."""
    root = Path(settings.work_dir)
    removed_files = 0
    removed_bytes = 0
    uploads = root / "uploads"
    if uploads.is_dir():
        for path in uploads.glob("batch_*"):
            if path.is_file():
                removed_files += 1
                removed_bytes += path.stat().st_size
                path.unlink(missing_ok=True)
    for path in root.glob("batch_*"):
        if path.is_dir():
            removed_files += sum(1 for child in path.rglob("*") if child.is_file())
            removed_bytes += sum(
                child.stat().st_size for child in path.rglob("*") if child.is_file()
            )
            shutil.rmtree(path)
    return removed_files, removed_bytes


async def _recover_interrupted_jobs() -> int:
    """Requeue jobs left importing after the previous singleton exited."""
    recovered = 0
    async with SessionLocal() as session:
        rows = await session.execute(
            select(Batch).where(Batch.status == "importing").with_for_update(skip_locked=True)
        )
        for batch in rows.scalars():
            stats = dict(batch.stats or {})
            if not isinstance(stats.get("job"), dict):
                batch.status = "failed"
                stats["error"] = (
                    "import was interrupted before its resumable job was recorded; "
                    "re-import the file"
                )
                stats["finished_at"] = _utc_now()
            else:
                batch.status = "queued"
                progress = dict(stats.get("progress") or {})
                progress.update(
                    phase="recovered",
                    message="Previous worker stopped; import queued for a clean retry",
                    updated_at=_utc_now(),
                )
                stats["progress"] = progress
                recovered += 1
            batch.stats = stats
        await session.commit()
    return recovered


async def _claim_batch() -> tuple[int, str, dict, str] | None:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Batch)
            .where(Batch.status == "queued")
            .order_by(Batch.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        batch = result.scalar_one_or_none()
        if batch is None:
            return None

        stats = dict(batch.stats or {})
        job = stats.get("job")
        if not isinstance(job, dict):
            batch.status = "failed"
            batch.stats = {
                **stats,
                "error": "queued import has no persisted job specification; re-import the file",
                "finished_at": _utc_now(),
            }
            await session.commit()
            log.error(
                "batch %s cannot be resumed because its job specification is missing", batch.id
            )
            return None

        filename = _filename(batch)
        attempt_id = uuid4().hex
        attempt = int(stats.get("attempt", 0)) + 1
        now = _utc_now()
        batch.status = "importing"
        batch.stats = {
            **stats,
            "attempt": attempt,
            "attempt_id": attempt_id,
            "started_at": stats.get("started_at") or now,
            "heartbeat_at": now,
            "progress": {
                "phase": "claimed",
                "message": f"Worker claimed import attempt {attempt}",
                "rows_processed": 0,
                "shards_processed": 0,
                "updated_at": now,
            },
        }
        await session.commit()
        log.info(
            "claimed import batch_id=%s name=%r attempt=%s attempt_id=%s file=%r",
            batch.id,
            batch.name,
            attempt,
            attempt_id,
            filename,
        )
        return batch.id, filename, job, attempt_id


async def _update_progress(
    batch_id: int,
    attempt_id: str,
    *,
    phase: str | None = None,
    message: str | None = None,
    rows_processed: int | None = None,
    shards_processed: int | None = None,
    shards_total: int | None = None,
    stage: str | None = None,
    items_processed: int | None = None,
    items_total: int | None = None,
    stage_elapsed_seconds: float | None = None,
) -> None:
    """Persist progress without sharing the ingestion transaction."""
    async with SessionLocal() as session:
        batch = await session.get(Batch, batch_id)
        if batch is None or batch.status not in ACTIVE_STATUSES:
            return
        stats = dict(batch.stats or {})
        if stats.get("attempt_id") != attempt_id:
            return
        now = _utc_now()
        progress = dict(stats.get("progress") or {})
        if phase is not None and phase != progress.get("phase") and stage is None:
            for key in (
                "stage",
                "stage_started_at",
                "items_processed",
                "items_total",
                "stage_elapsed_seconds",
            ):
                progress.pop(key, None)
        if stage is not None and stage != progress.get("stage"):
            for key in ("items_processed", "items_total", "stage_elapsed_seconds"):
                progress.pop(key, None)
            progress["stage_started_at"] = now
        updates = {
            "phase": phase,
            "message": message,
            "rows_processed": rows_processed,
            "shards_processed": shards_processed,
            "shards_total": shards_total,
            "stage": stage,
            "items_processed": items_processed,
            "items_total": items_total,
            "stage_elapsed_seconds": (
                round(stage_elapsed_seconds, 1) if stage_elapsed_seconds is not None else None
            ),
        }
        progress.update({key: value for key, value in updates.items() if value is not None})
        if stage_elapsed_seconds is None and progress.get("stage_started_at"):
            try:
                stage_started = datetime.fromisoformat(str(progress["stage_started_at"]))
                progress["stage_elapsed_seconds"] = round(
                    (datetime.now(UTC) - stage_started).total_seconds(), 1
                )
            except ValueError:
                pass
        progress["updated_at"] = now
        stats["heartbeat_at"] = now
        stats["progress"] = progress
        batch.stats = stats
        await session.commit()

    log.info(
        (
            "import progress batch_id=%s attempt_id=%s phase=%s stage=%s "
            "items=%s/%s stage_seconds=%s rows=%s shards=%s/%s message=%s"
        ),
        batch_id,
        attempt_id,
        phase or progress.get("phase"),
        stage or progress.get("stage"),
        items_processed if items_processed is not None else progress.get("items_processed"),
        items_total if items_total is not None else progress.get("items_total"),
        (
            round(stage_elapsed_seconds, 1)
            if stage_elapsed_seconds is not None
            else progress.get("stage_elapsed_seconds")
        ),
        rows_processed if rows_processed is not None else progress.get("rows_processed"),
        shards_processed if shards_processed is not None else progress.get("shards_processed"),
        shards_total if shards_total is not None else progress.get("shards_total"),
        message or progress.get("message"),
    )


async def _heartbeat(batch_id: int, attempt_id: str, progress_lock: asyncio.Lock) -> None:
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                async with progress_lock:
                    await _update_progress(batch_id, attempt_id)
            except Exception:
                log.exception("heartbeat failed batch_id=%s attempt_id=%s", batch_id, attempt_id)
    except asyncio.CancelledError:
        raise


async def _process_batch(batch_id: int, filename: str, job: dict, attempt_id: str) -> None:
    work = Path(settings.work_dir) / "uploads" / f"batch_{batch_id}_{attempt_id}_{filename}"
    started = asyncio.get_running_loop().time()
    progress_lock = asyncio.Lock()
    heartbeat = asyncio.create_task(_heartbeat(batch_id, attempt_id, progress_lock))

    async def progress(**values) -> None:
        try:
            async with progress_lock:
                await _update_progress(batch_id, attempt_id, **values)
        except Exception:
            log.exception(
                "could not persist import progress batch_id=%s attempt_id=%s",
                batch_id,
                attempt_id,
            )

    try:
        await progress(phase="downloading", message="Downloading raw object from storage")
        raw_key, _ = batch_keys(batch_id, filename)
        storage = await asyncio.to_thread(get_storage)
        await asyncio.to_thread(storage.get_file, raw_key, work)
        await progress(
            phase="downloaded",
            message=f"Raw object downloaded ({work.stat().st_size:,} bytes)",
        )

        async with SessionLocal() as session:
            batch = await session.get(Batch, batch_id)
            if batch is None:
                return
            mapping = ColumnMapping(
                source_text=str(job.get("source_column", "source")),
                target_text=str(job.get("target_column", "target")),
                src_lang=job.get("src_lang_column"),
                tgt_lang=job.get("tgt_lang_column"),
                domain=job.get("domain_column"),
                quality=job.get("quality_column"),
                document_id=job.get("document_id_column"),
            )
            await ingest_stored_file(
                session,
                batch=batch,
                local_file=work,
                filename=filename,
                src_lang=str(job["src_lang"]),
                tgt_lang=str(job["tgt_lang"]),
                domain=job.get("domain"),
                mapping=mapping,
                fmt=job.get("format"),
                policy=ReservationPolicy.resolve(
                    percent=job.get("evaluation_percent"),
                    max_samples=job.get("evaluation_max_samples"),
                    selector=job.get("evaluation_selector"),
                    seed=job.get("random_seed"),
                ),
                attempt_id=attempt_id,
                progress=progress,
            )

        log.info(
            "completed import batch_id=%s attempt_id=%s elapsed_seconds=%.1f",
            batch_id,
            attempt_id,
            asyncio.get_running_loop().time() - started,
        )
    except asyncio.CancelledError:
        log.warning("worker stopping during import batch_id=%s attempt_id=%s", batch_id, attempt_id)
        raise
    except Exception as exc:
        log.exception(
            "import failed batch_id=%s attempt_id=%s elapsed_seconds=%.1f",
            batch_id,
            attempt_id,
            asyncio.get_running_loop().time() - started,
        )
        async with SessionLocal() as session:
            batch = await session.get(Batch, batch_id)
            if batch is not None:
                stats = dict(batch.stats or {})
                if stats.get("attempt_id") == attempt_id:
                    batch.status = "failed"
                    batch.stats = {
                        **stats,
                        "error": f"{type(exc).__name__}: {exc}",
                        "finished_at": _utc_now(),
                        "progress": {
                            **dict(stats.get("progress") or {}),
                            "phase": "failed",
                            "message": str(exc),
                            "updated_at": _utc_now(),
                        },
                    }
                    await session.commit()
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        work.unlink(missing_ok=True)
        await asyncio.to_thread(
            shutil.rmtree,
            Path(settings.work_dir) / f"batch_{batch_id}",
            True,
        )


async def _run_as_singleton() -> None:
    async with engine.connect() as lock_connection:
        acquired = await lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": WORKER_LOCK_ID}
        )
        await lock_connection.commit()
        if not acquired:
            log.warning("another import worker owns the singleton lock; retrying later")
            await asyncio.sleep(RETRY_SECONDS)
            return

        log.info("import worker acquired singleton lock lock_id=%s", WORKER_LOCK_ID)
        removed_files, removed_bytes = await asyncio.to_thread(_cleanup_stale_work)
        if removed_files:
            log.warning(
                "removed stale worker files count=%s bytes=%s",
                removed_files,
                removed_bytes,
            )
        recovered = await _recover_interrupted_jobs()
        if recovered:
            log.warning("requeued %s interrupted import job(s)", recovered)

        while True:
            claimed = await _claim_batch()
            if claimed is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            await _process_batch(*claimed)


async def run() -> None:
    setup_logging(settings.debug)
    log.info(
        (
            "starting import worker poll_seconds=%s heartbeat_seconds=%s work_dir=%s "
            "batch_rows=%s candidate_limit=%s candidate_multiplier=%s"
        ),
        POLL_SECONDS,
        HEARTBEAT_SECONDS,
        settings.work_dir,
        settings.import_batch_rows,
        settings.evaluation_candidate_limit,
        settings.evaluation_candidate_multiplier,
    )
    while True:
        try:
            await _run_as_singleton()
        except asyncio.CancelledError:
            log.info("import worker shutdown requested")
            raise
        except Exception:
            log.exception("worker loop failed; retrying in %s seconds", RETRY_SECONDS)
            await asyncio.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    asyncio.run(run())
