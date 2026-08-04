"""Dataset builder: resolve a dataset definition into rows, on demand.

Nothing is copied or materialized here. A dataset definition is a query over
the immutable batch Parquet files; only snapshots write files.
"""

import duckdb
import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Batch, DatasetDefinition, Sample
from services.dataset_builder.filters import DatasetFilters
from utils.duck import connect, parquet_source


async def batch_uris(session: AsyncSession, batch_ids: list[int] | None) -> list[str]:
    stmt = select(Batch.parquet_uri).where(Batch.status == "ready", Batch.parquet_uri.is_not(None))
    if batch_ids:
        stmt = stmt.where(Batch.id.in_(batch_ids))
    return [r[0] for r in await session.execute(stmt)]


async def ignored_sample_ids(session: AsyncSession, batch_ids: list[int] | None) -> pl.DataFrame:
    """Ignored samples are excluded at build time; the underlying data is untouched."""
    stmt = select(Sample.id).where(Sample.status == "ignored")
    if batch_ids:
        stmt = stmt.where(Sample.batch_id.in_(batch_ids))
    ids = [r[0] for r in await session.execute(stmt)]
    return pl.DataFrame({"sample_id": pl.Series(ids, dtype=pl.Int64)})


class BuildContext:
    """A configured DuckDB connection plus the SQL relation for a dataset."""

    def __init__(self, con: duckdb.DuckDBPyConnection, relation: str) -> None:
        self.con = con
        self.relation = relation

    def sql(self, query: str) -> duckdb.DuckDBPyRelation:
        return self.con.sql(query.format(rel=self.relation))

    def close(self) -> None:
        self.con.close()


async def make_context(
    session: AsyncSession,
    filters: DatasetFilters,
    batch_ids: list[int] | None = None,
) -> BuildContext:
    uris = await batch_uris(session, batch_ids)
    con = connect()
    src = parquet_source(uris)
    where = filters.where_sql()

    if filters.include_ignored:
        relation = f"(SELECT * FROM {src} WHERE {where})"
    else:
        ignored = await ignored_sample_ids(session, batch_ids)  # noqa: F841 - used by DuckDB
        con.register("ignored_samples", ignored)
        relation = (
            f"(SELECT s.* FROM {src} s "
            "ANTI JOIN ignored_samples i ON i.sample_id = s.sample_id "
            f"WHERE {where})"
        )
    return BuildContext(con, relation)


async def context_for_definition(
    session: AsyncSession, definition: DatasetDefinition
) -> BuildContext:
    filters = DatasetFilters(**(definition.filters or {}))
    return await make_context(session, filters, definition.batch_ids or None)


def count_rows(ctx: BuildContext) -> int:
    return int(ctx.sql("SELECT count(*) FROM {rel}").fetchone()[0])


def preview(ctx: BuildContext, limit: int = 50, offset: int = 0) -> list[dict]:
    rel = ctx.sql(f"SELECT * FROM {{rel}} LIMIT {int(limit)} OFFSET {int(offset)}")
    return rel.pl().to_dicts()


def statistics(ctx: BuildContext) -> dict:
    def group(expr: str, alias: str) -> list[dict]:
        return (
            ctx.sql(
                f"SELECT {expr} AS {alias}, count(*) AS count FROM {{rel}} "
                f"GROUP BY 1 ORDER BY count DESC LIMIT 100"
            )
            .pl()
            .to_dicts()
        )

    return {
        "total": count_rows(ctx),
        "by_language_pair": group("src_lang || '-' || tgt_lang", "language_pair"),
        "by_domain": group("coalesce(domain, 'unknown')", "domain"),
        "by_source": group("source_id", "source_id"),
        "by_batch": group("batch_id", "batch_id"),
    }
