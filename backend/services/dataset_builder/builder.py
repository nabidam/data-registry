"""Dataset builder: resolve a dataset definition into rows, on demand.

Nothing is copied or materialized here. A dataset definition is a query over
the immutable batch Parquet files; only snapshots write files.

Every training context is scoped to TRAINABLE by default, so
reserved evaluation, contamination-quarantined, and ignored samples are excluded with no configuration —
that is what makes builds deterministic. Postgres is the authority on
allocation; the copy inside the batch Parquet is only the value at ingest time.
"""

import duckdb
import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from models import Batch, DatasetDefinition, Sample
from services.dataset_builder.filters import DatasetFilters
from utils.duck import connect, parquet_source


async def batch_uris(session: AsyncSession, batch_ids: list[int] | None) -> list[str]:
    stmt = select(Batch.parquet_uri).where(Batch.status == "ready", Batch.parquet_uri.is_not(None))
    if batch_ids:
        stmt = stmt.where(Batch.id.in_(batch_ids))
    return [r[0] for r in await session.execute(stmt)]


async def _sample_ids(
    session: AsyncSession, batch_ids: list[int] | None, *, allocation: str, equal: bool
) -> pl.DataFrame:
    """Sample ids whose allocation matches (or does not match) ``allocation``."""
    column = Sample.allocation
    stmt = select(Sample.id).where(column == allocation if equal else column != allocation)
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


def _composition_relation(
    source: str,
    where: str,
    batch_rules: list[dict] | None,
    seed: int,
) -> str:
    """Resolve stable per-batch membership before allocation is considered."""
    if not batch_rules:
        return f"(SELECT s.* FROM {source} s WHERE {where})"

    predicates: list[str] = []
    for rule in batch_rules:
        batch_id = int(rule["batch_id"])
        mode = str(rule.get("mode", "all"))
        if mode == "all":
            amount = "TRUE"
        elif mode == "percent":
            percent = float(rule["value"])
            amount = (
                "_composition_rank <= "
                f"ceil(_composition_count * {percent} / 100.0)"
            )
        elif mode == "count":
            amount = f"_composition_rank <= {int(rule['value'])}"
        else:
            raise ValueError(f"unknown batch composition mode {mode!r}")
        predicates.append(f"(batch_id = {batch_id} AND {amount})")

    included_ids = ", ".join(str(int(rule["batch_id"])) for rule in batch_rules)
    selection = " OR ".join(predicates) or "FALSE"
    return (
        "(SELECT ranked.* EXCLUDE (_composition_rank, _composition_count) FROM ("
        "SELECT s.*, "
        "row_number() OVER (PARTITION BY batch_id ORDER BY "
        f"hash(sample_id::VARCHAR || '-{int(seed)}'), sample_id) AS _composition_rank, "
        "count(*) OVER (PARTITION BY batch_id) AS _composition_count "
        f"FROM {source} s WHERE batch_id IN ({included_ids}) AND {where}"
        f") ranked WHERE {selection})"
    )


async def make_context(
    session: AsyncSession,
    filters: DatasetFilters,
    batch_ids: list[int] | None = None,
    allocation: str | None = Allocation.TRAINABLE,
    *,
    batch_rules: list[dict] | None = None,
    composition_seed: int = 42,
) -> BuildContext:
    """Build a context optionally restricted to one allocation.

    TRAINABLE is expressed as an anti join against everything else (the small
    side is the reserved + ignored pool); any other allocation is a semi join
    against its own, equally small, id list.
    """
    uris = await batch_uris(session, batch_ids)
    con = connect()
    src = parquet_source(uris)
    where = filters.where_sql()
    composition = _composition_relation(src, where, batch_rules, composition_seed)
    if allocation is None:
        return BuildContext(con, composition)

    trainable = allocation == Allocation.TRAINABLE

    ids = await _sample_ids(session, batch_ids, allocation=allocation, equal=not trainable)
    con.register("allocation_ids", ids)
    join = "ANTI JOIN" if trainable else "SEMI JOIN"
    relation = (
        f"(SELECT s.* FROM {composition} s "
        f"{join} allocation_ids a ON a.sample_id = s.sample_id "
        ")"
    )
    return BuildContext(con, relation)


async def context_for_definition(
    session: AsyncSession,
    definition: DatasetDefinition,
    allocation: str | None = Allocation.TRAINABLE,
) -> BuildContext:
    filters = DatasetFilters(**(definition.filters or {}))
    rules = list(definition.batch_rules or [])
    batch_ids = [int(rule["batch_id"]) for rule in rules] if rules else definition.batch_ids or None
    return await make_context(
        session,
        filters,
        batch_ids,
        allocation,
        batch_rules=rules,
        composition_seed=definition.composition_seed,
    )


async def allocation_counts(
    session: AsyncSession, definition: DatasetDefinition
) -> dict[str, int]:
    """Count fixed composition membership by current global allocation."""
    counts: dict[str, int] = {}
    total_ctx = await context_for_definition(session, definition, None)
    try:
        counts["composed"] = count_rows(total_ctx)
    finally:
        total_ctx.close()
    for allocation in Allocation:
        ctx = await context_for_definition(session, definition, allocation)
        try:
            counts[str(allocation).lower()] = count_rows(ctx)
        finally:
            ctx.close()
    return counts


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
