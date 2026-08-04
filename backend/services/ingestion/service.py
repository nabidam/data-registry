"""Ingestion: raw file -> immutable batch (Parquet) + sample metadata (Postgres)."""

import logging
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from db.session import raw_asyncpg_connection
from models import Batch
from services.ingestion.normalize import ColumnMapping, normalize
from services.ingestion.readers import detect_format, read_any
from storage import get_storage

log = logging.getLogger(__name__)

COPY_CHUNK = 200_000
SAMPLE_COLUMNS = (
    "id",
    "batch_id",
    "source_id",
    "src_lang",
    "tgt_lang",
    "domain",
    "quality",
    "status",
    "created_at",
)


def batch_keys(batch_id: int, filename: str) -> tuple[str, str]:
    return f"raw/batch_{batch_id}/{filename}", f"batches/batch_{batch_id}/data.parquet"


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

    raw_key, parquet_key = batch_keys(batch.id, filename)
    batch.raw_uri = storage.put_file(local_file, raw_key)

    df = normalize(
        read_any(local_file, fmt),
        mapping,
        batch_id=batch.id,
        source_id=source_id,
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
        "source_text",
        "target_text",
        "meta",
    )

    work = Path(settings.work_dir) / f"batch_{batch.id}"
    work.mkdir(parents=True, exist_ok=True)
    local_parquet = work / "data.parquet"
    df.write_parquet(local_parquet, compression="zstd")
    batch.parquet_uri = storage.put_file(local_parquet, parquet_key)
    local_parquet.unlink(missing_ok=True)

    await _copy_samples(session, df)

    batch.sample_count = df.height
    batch.status = "ready"
    batch.stats = _batch_stats(df)
    await session.commit()
    await session.refresh(batch)
    log.info("ingested batch %s (%s samples)", batch.name, batch.sample_count)
    return batch


async def _copy_samples(session: AsyncSession, df: pl.DataFrame) -> None:
    conn = await raw_asyncpg_connection(session)
    now = datetime.now(UTC)
    meta = df.select(
        "sample_id", "batch_id", "source_id", "src_lang", "tgt_lang", "domain", "quality"
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
                "active",
                now,
            )
            for r in chunk.iter_rows(named=True)
        ]
        await conn.copy_records_to_table("samples", records=records, columns=SAMPLE_COLUMNS)


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
