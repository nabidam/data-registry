"""Ingestion: raw file -> immutable batch (Parquet) + sample metadata (Postgres).

Pipeline: read -> normalize -> canonical samples -> evaluation selection ->
reserved / quarantined / trainable. Reservation happens here, before the batch is visible to
the dataset builder, so evaluation data can never leak into a snapshot.
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import polars as pl
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from core.config import settings
from db.session import raw_psycopg_connection
from models import Batch
from services.evaluation.contamination_safe import (
    prepare_contamination_reference,
    scan_full_corpus_contamination,
)
from services.evaluation.reservation import (
    ReservationPolicy,
    allocate,
    apply_selection,
    reservation_size,
)
from services.evaluation.reservation_config import load_contamination_safe_config
from services.evaluation.selectors import get_selector
from services.ingestion.normalize import ColumnMapping, normalize
from services.ingestion.readers import detect_format, iter_any, read_any
from storage import get_storage

log = logging.getLogger(__name__)

ProgressCallback = Callable[..., Awaitable[None]]

COPY_CHUNK = 200_000
SAMPLE_COLUMNS = (
    "id",
    "batch_id",
    "source_id",
    "src_lang",
    "tgt_lang",
    "domain",
    "quality",
    "allocation",
    "reserved_at",
    "quarantined_at",
    "created_at",
)


def _thread_progress_bridge(
    progress: ProgressCallback | None,
    *,
    phase: str,
    rows_processed: int | None = None,
    shards_processed: int | None = None,
    shards_total: int | None = None,
):
    """Schedule async progress writes from CPU work running in a thread."""
    if progress is None:
        return None
    loop = asyncio.get_running_loop()

    def report(
        stage: str,
        message: str,
        completed: int | None,
        total: int | None,
        elapsed: float | None,
    ) -> None:
        values = {
            "phase": phase,
            "stage": stage,
            "message": message,
            "items_processed": completed,
            "items_total": total,
            "stage_elapsed_seconds": elapsed,
            "rows_processed": rows_processed,
            "shards_processed": shards_processed,
            "shards_total": shards_total,
        }

        def schedule() -> None:
            asyncio.create_task(progress(**values))

        loop.call_soon_threadsafe(schedule)

    return report


def batch_keys(batch_id: int, filename: str) -> tuple[str, str]:
    """Object keys for an import and its sharded normalized output."""
    return f"raw/batch_{batch_id}/{filename}", f"batches/batch_{batch_id}"


async def _reserve_sample_ids(session: AsyncSession, n: int) -> list[int]:
    """Grab a block of ids up front so Parquet and Postgres agree on sample_id."""
    rows = await session.execute(
        text("SELECT nextval('samples_id_seq') FROM generate_series(1, :n)"), {"n": n}
    )
    return [r[0] for r in rows]


async def ingest_file(
    session: AsyncSession,
    *,
    local_file: Path,
    filename: str,
    batch_name: str,
    source_id: int | None,
    src_lang: str,
    tgt_lang: str,
    domain: str | None,
    mapping: ColumnMapping,
    fmt: str | None = None,
    notes: str | None = None,
    policy: ReservationPolicy | None = None,
) -> Batch:
    storage = get_storage()
    fmt = (fmt or detect_format(filename)).lower()

    batch = Batch(
        name=batch_name,
        source_id=source_id,
        status="importing",
        format=fmt,
        notes=notes,
    )
    session.add(batch)
    await session.flush()  # need the id for storage keys and sample rows

    raw_key, _ = batch_keys(batch.id, filename)
    batch.raw_uri = storage.put_file(local_file, raw_key)

    return await ingest_stored_file(
        session,
        batch=batch,
        local_file=local_file,
        filename=filename,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        domain=domain,
        mapping=mapping,
        fmt=fmt,
        policy=policy,
    )


async def ingest_stored_file(
    session: AsyncSession,
    *,
    batch: Batch,
    local_file: Path,
    filename: str,
    src_lang: str,
    tgt_lang: str,
    domain: str | None,
    mapping: ColumnMapping,
    fmt: str | None = None,
    policy: ReservationPolicy | None = None,
    attempt_id: str | None = None,
    progress: ProgressCallback | None = None,
) -> Batch:
    """Normalize a raw object already registered on ``batch``.

    This is used by the resumable import flow after MinIO has received every
    upload part. Keeping it separate means the request which completes an
    upload can return before CPU-heavy normalization starts.
    """
    storage = get_storage()
    fmt = (fmt or detect_format(filename)).lower()
    _, batch_prefix = batch_keys(batch.id, filename)
    attempt_id = attempt_id or uuid4().hex
    parquet_prefix = f"{batch_prefix}/attempt_{attempt_id}"

    if (
        fmt in {"csv", "tsv"}
        and local_file.stat().st_size >= settings.import_stream_threshold_mb * 1024 * 1024
    ):
        return await _ingest_large_delimited(
            session,
            batch=batch,
            local_file=local_file,
            filename=filename,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            domain=domain,
            mapping=mapping,
            fmt=fmt,
            policy=policy or ReservationPolicy.resolve(),
            parquet_prefix=parquet_prefix,
            progress=progress,
        )

    if progress:
        await progress(phase="normalizing", message="Reading and normalizing import")
    df = normalize(
        read_any(local_file, fmt),
        mapping,
        batch_id=batch.id,
        source_id=batch.source_id,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        domain=domain,
    )
    if df.height == 0:
        raise ValueError("no valid translation pairs found in file")

    ids = await _reserve_sample_ids(session, df.height)
    df = df.with_columns(pl.Series("sample_id", ids, dtype=pl.Int64)).select(
        "sample_id",
        "batch_id",
        "source_id",
        "src_lang",
        "tgt_lang",
        "domain",
        "quality",
        "document_id",
        "source_text",
        "target_text",
        "meta",
    )

    # Evaluation selection runs before anything downstream can see the batch.
    if progress:
        await progress(
            phase="selecting_evaluation",
            message="Running contamination-safe evaluation selection",
            rows_processed=df.height,
        )
    df, reservation = await asyncio.to_thread(
        allocate, df, policy or ReservationPolicy.resolve()
    )

    work = Path(settings.work_dir) / f"batch_{batch.id}"
    work.mkdir(parents=True, exist_ok=True)
    shards = _write_parquet_shards(df, work)
    reservation["manifest"] = {
        **reservation.get("selection", {}).get("manifest", {}),
        "parquet_shards": [
            {"name": shard.name, "bytes": shard.stat().st_size, "sha256": _sha256_file(shard)}
            for shard in shards
        ],
    }
    for shard in shards:
        await asyncio.to_thread(storage.put_file, shard, f"{parquet_prefix}/{shard.name}")
        shard.unlink(missing_ok=True)
    # DuckDB reads this glob as one relation. The individual files remain
    # bounded and immutable, so a large batch never becomes one giant object.
    batch.parquet_uri = storage.uri(f"{parquet_prefix}/*.parquet")

    await _copy_samples(session, df)

    await session.refresh(batch, attribute_names=["stats"])
    previous_stats = dict(batch.stats or {})
    batch.sample_count = df.height
    batch.status = "ready"
    batch.stats = {
        **previous_stats,
        **_batch_stats(df),
        "reservation": reservation,
        "finished_at": datetime.now(UTC).isoformat(),
        "progress": {
            **dict(previous_stats.get("progress") or {}),
            "phase": "complete",
            "message": f"Imported {df.height:,} rows",
            "rows_processed": df.height,
            "shards_processed": len(shards),
            "shards_total": len(shards),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    }
    await session.commit()
    await session.refresh(batch)
    log.info(
        "ingested batch %s (%s samples, %s reserved for evaluation)",
        batch.name,
        batch.sample_count,
        reservation["reserved"],
    )
    return batch


async def _ingest_large_delimited(
    session: AsyncSession,
    *,
    batch: Batch,
    local_file: Path,
    filename: str,
    src_lang: str,
    tgt_lang: str,
    domain: str | None,
    mapping: ColumnMapping,
    fmt: str,
    policy: ReservationPolicy,
    parquet_prefix: str,
    progress: ProgressCallback | None,
) -> Batch:
    """Ingest a large CSV/TSV without materializing the corpus in RAM.

    The complete corpus is normalized into bounded temporary Parquet shards.
    Evaluation selection runs over a deterministic bounded candidate pool, and
    the selected IDs are then applied while each shard is written and copied
    to object storage. This keeps memory proportional to one input batch plus
    the configured candidate limit, not to the upload size.
    """
    storage = get_storage()
    work = Path(settings.work_dir) / f"batch_{batch.id}"
    temp = work / "normalized"
    final = work / "final"
    temp.mkdir(parents=True, exist_ok=True)
    final.mkdir(parents=True, exist_ok=True)
    for old in (*temp.glob("*.parquet"), *final.glob("*.parquet")):
        old.unlink(missing_ok=True)

    candidate_limit = max(1, settings.evaluation_candidate_limit)
    candidate_pool: pl.DataFrame | None = None
    imported = 0
    shard_count = 0
    for raw in iter_any(local_file, fmt, batch_size=settings.import_batch_rows):
        normalized = normalize(
            raw,
            mapping,
            batch_id=batch.id,
            source_id=batch.source_id,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            domain=domain,
        )
        if normalized.height == 0:
            continue

        ids = await _reserve_sample_ids(session, normalized.height)
        normalized = normalized.with_columns(pl.Series("sample_id", ids, dtype=pl.Int64)).select(
            "sample_id",
            "batch_id",
            "source_id",
            "src_lang",
            "tgt_lang",
            "domain",
            "quality",
            "document_id",
            "source_text",
            "target_text",
            "meta",
        )
        normalized.write_parquet(temp / f"normalized-{shard_count:06d}.parquet", compression="zstd")
        shard_count += 1
        imported += normalized.height

        ranked = normalized.with_columns(
            pl.concat_str(["source_text", "target_text"], separator="\x00")
            .hash(seed=policy.seed)
            .alias("_candidate_rank")
        )
        if candidate_pool is None:
            candidate_pool = ranked.sort("_candidate_rank").head(candidate_limit)
        else:
            candidate_pool = (
                pl.concat([candidate_pool, ranked], how="vertical_relaxed")
                .sort("_candidate_rank")
                .head(candidate_limit)
            )
        if progress:
            await progress(
                phase="normalizing",
                message=f"Normalized {imported:,} rows into {shard_count} temporary shards",
                rows_processed=imported,
                shards_processed=shard_count,
            )

    if imported == 0 or candidate_pool is None:
        raise ValueError("no valid translation pairs found in file")

    target = reservation_size(imported, policy.percent, policy.max_samples)
    effective_candidate_limit = min(
        candidate_pool.height,
        max(target, target * settings.evaluation_candidate_multiplier),
    )
    candidates = candidate_pool.head(effective_candidate_limit).drop("_candidate_rank")
    if progress:
        await progress(
            phase="selecting_evaluation",
            message=(
                f"Selecting up to {target:,} evaluation rows from "
                f"{candidates.height:,} deterministic candidates"
            ),
            rows_processed=imported,
            shards_processed=shard_count,
            shards_total=shard_count,
        )
    selection_progress = _thread_progress_bridge(
        progress,
        phase="selecting_evaluation",
        rows_processed=imported,
        shards_processed=shard_count,
        shards_total=shard_count,
    )
    selection = await asyncio.to_thread(
        get_selector(policy.selector),
        candidates,
        target,
        policy.seed,
        selection_progress,
    )
    _, reservation = apply_selection(
        candidates,
        policy,
        selection,
        imported_count=imported,
    )
    reservation["selection"] = {
        **reservation.get("selection", {}),
        "bounded_candidate_pool": True,
        "candidate_limit": candidate_limit,
        "effective_candidate_limit": effective_candidate_limit,
        "candidate_multiplier": settings.evaluation_candidate_multiplier,
        "candidate_rows": candidates.height,
    }

    contamination_config = None
    contamination_reference = None
    if policy.selector == "contamination_safe" and selection.reserved_ids:
        contamination_config = load_contamination_safe_config()
        selected_rows = candidates.filter(pl.col("sample_id").is_in(selection.reserved_ids))
        if progress:
            await progress(
                phase="preparing_contamination_scan",
                message="Preparing selected evaluation rows for a full-corpus leakage scan",
            )
        reference_progress = _thread_progress_bridge(
            progress,
            phase="preparing_contamination_scan",
            rows_processed=imported,
            shards_processed=0,
            shards_total=shard_count,
        )
        contamination_reference = await asyncio.to_thread(
            prepare_contamination_reference,
            selected_rows,
            contamination_config,
            reference_progress,
        )

    parquet_manifest: list[dict[str, object]] = []
    language_pairs: dict[str, int] = {}
    domains: dict[str, int] = {}
    source_shards = sorted(temp.glob("*.parquet"))
    base_quarantined = set(getattr(selection, "quarantined_ids", []))
    full_scan_quarantined = 0
    full_scan_semantic_checked = 0
    published_rows = 0
    for index, source_shard in enumerate(source_shards):
        frame = pl.read_parquet(source_shard)
        published_rows += frame.height
        extra_quarantined: set[int] = set()
        if contamination_reference is not None and contamination_config is not None:
            scan_progress = _thread_progress_bridge(
                progress,
                phase="scanning_contamination",
                rows_processed=published_rows,
                shards_processed=index,
                shards_total=len(source_shards),
            )
            scan_result = await asyncio.to_thread(
                scan_full_corpus_contamination,
                frame,
                contamination_reference,
                contamination_config,
                scan_progress,
            )
            extra_quarantined = scan_result.quarantined_ids
            extra_quarantined.difference_update(base_quarantined)
            full_scan_quarantined += len(extra_quarantined)
            full_scan_semantic_checked += scan_result.semantic_checked_rows
        frame_selection = (
            replace(
                selection,
                quarantined_ids=list(base_quarantined | extra_quarantined),
            )
            if hasattr(selection, "quarantined_ids")
            else selection
        )
        frame, _ = apply_selection(
            frame,
            policy,
            frame_selection,
            imported_count=imported,
        )
        output = final / f"part-{index:06d}.parquet"
        frame.write_parquet(output, compression="zstd")
        if output.stat().st_size > settings.parquet_shard_size_mb * 1024 * 1024:
            raise ValueError(
                "an import frame exceeds PARQUET_SHARD_SIZE_MB; lower IMPORT_BATCH_ROWS"
            )
        parquet_manifest.append(
            {"name": output.name, "bytes": output.stat().st_size, "sha256": _sha256_file(output)}
        )
        for row in (
            frame.group_by(["src_lang", "tgt_lang"])
            .len()
            .with_columns(
                (pl.col("src_lang") + "-" + pl.col("tgt_lang")).alias("pair")
            )
            .iter_rows(named=True)
        ):
            language_pairs[str(row["pair"])] = language_pairs.get(str(row["pair"]), 0) + int(
                row["len"]
            )
        for row in frame.group_by("domain").len().iter_rows(named=True):
            key = str(row["domain"])
            domains[key] = domains.get(key, 0) + int(row["len"])
        await _copy_samples(session, frame)
        await asyncio.to_thread(storage.put_file, output, f"{parquet_prefix}/{output.name}")
        output.unlink(missing_ok=True)
        source_shard.unlink(missing_ok=True)
        if progress:
            await progress(
                phase="scanning_and_publishing",
                message=(
                    f"Checked and published shard {index + 1}/{len(source_shards)}; "
                    f"{full_scan_quarantined:,} leakage rows quarantined; "
                    f"{full_scan_semantic_checked:,} rows semantically verified"
                ),
                rows_processed=published_rows,
                shards_processed=index + 1,
                shards_total=len(source_shards),
            )

    batch.parquet_uri = storage.uri(f"{parquet_prefix}/*.parquet")
    total_quarantined = len(base_quarantined) + full_scan_quarantined
    reservation["quarantined"] = total_quarantined
    reservation["trainable"] = imported - reservation["reserved"] - total_quarantined
    reservation["selection"] = {
        **reservation.get("selection", {}),
        "full_corpus_scan": contamination_reference is not None,
        "full_corpus_quarantined": full_scan_quarantined,
        "full_corpus_semantic_checked": full_scan_semantic_checked,
    }
    reservation["manifest"] = {
        **reservation.get("selection", {}).get("manifest", {}),
        "parquet_shards": parquet_manifest,
    }
    await session.refresh(batch, attribute_names=["stats"])
    previous_stats = dict(batch.stats or {})
    now = datetime.now(UTC).isoformat()
    batch.sample_count = imported
    batch.status = "ready"
    batch.stats = {
        **previous_stats,
        "rows": imported,
        "language_pairs": language_pairs,
        "domains": domains,
        "reservation": reservation,
        "finished_at": now,
        "progress": {
            **dict(previous_stats.get("progress") or {}),
            "phase": "complete",
            "message": (
                f"Imported {imported:,} rows; reserved {reservation['reserved']:,} "
                f"and quarantined {reservation['quarantined']:,}"
            ),
            "rows_processed": imported,
            "shards_processed": len(source_shards),
            "shards_total": len(source_shards),
            "updated_at": now,
        },
    }
    await session.commit()
    await session.refresh(batch)
    log.info(
        "ingested large batch %s (%s samples, %s reserved, %s quarantined)",
        batch.name,
        batch.sample_count,
        reservation["reserved"],
        reservation["quarantined"],
    )
    return batch


def _write_parquet_shards(df: pl.DataFrame, work: Path) -> list[Path]:
    """Write compressed Parquet shards, splitting until each is within the limit."""
    max_bytes = settings.parquet_shard_size_mb * 1024 * 1024
    if max_bytes < 1:
        raise ValueError("PARQUET_SHARD_SIZE_MB must be at least 1")

    estimated_bytes = max(df.estimated_size(), 1)
    initial_rows = max(1, int(df.height * max_bytes / estimated_bytes))
    shards: list[Path] = []

    def write_slice(offset: int, length: int) -> None:
        candidate = work / f".shard-{len(shards):05d}.parquet"
        df.slice(offset, length).write_parquet(candidate, compression="zstd")
        if candidate.stat().st_size <= max_bytes:
            final = work / f"part-{len(shards):05d}.parquet"
            candidate.replace(final)
            shards.append(final)
            return

        if length == 1:
            candidate.unlink(missing_ok=True)
            raise ValueError("one normalized row exceeds PARQUET_SHARD_SIZE_MB")

        candidate.unlink(missing_ok=True)
        left = length // 2
        write_slice(offset, left)
        write_slice(offset + left, length - left)

    for offset in range(0, df.height, initial_rows):
        write_slice(offset, min(initial_rows, df.height - offset))
    return shards


async def _copy_samples(session: AsyncSession, df: pl.DataFrame) -> None:
    conn = await raw_psycopg_connection(session)
    copy_statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier("samples"),
        sql.SQL(", ").join(sql.Identifier(column) for column in SAMPLE_COLUMNS),
    )
    now = datetime.now(UTC)
    meta = df.select(
        "sample_id",
        "batch_id",
        "source_id",
        "src_lang",
        "tgt_lang",
        "domain",
        "quality",
        "allocation",
    )
    for chunk in meta.iter_slices(COPY_CHUNK):
        records = [
            (
                r["sample_id"],
                r["batch_id"],
                r["source_id"],
                r["src_lang"],
                r["tgt_lang"],
                r["domain"],
                r["quality"],
                r["allocation"],
                now if r["allocation"] == Allocation.RESERVED_EVALUATION else None,
                now if r["allocation"] == Allocation.QUARANTINED else None,
                now,
            )
            for r in chunk.iter_rows(named=True)
        ]
        async with conn.cursor() as cursor:
            async with cursor.copy(copy_statement) as copy:
                for record in records:
                    await copy.write_row(record)


def _batch_stats(df: pl.DataFrame) -> dict:
    by_pair = (
        df.group_by(["src_lang", "tgt_lang"])
        .len()
        .with_columns((pl.col("src_lang") + "-" + pl.col("tgt_lang")).alias("pair"))
    )
    by_domain = df.group_by("domain").len()
    return {
        "rows": df.height,
        "language_pairs": dict(zip(by_pair["pair"], by_pair["len"], strict=True)),
        "domains": {str(k): v for k, v in zip(by_domain["domain"], by_domain["len"], strict=True)},
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
