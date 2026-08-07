"""Evaluation sets: benchmark collections built only from reserved samples.

An evaluation set is a view over the RESERVED_EVALUATION pool — the samples the
ingestion pipeline held back. It is independent of train/validation/test splits,
and a reserved sample may belong to several collections at once without ever
becoming trainable again.
"""

from pathlib import Path

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from core.config import settings
from models import EvaluationSet
from services.dataset_builder.builder import make_context
from services.dataset_builder.filters import EvaluationSetFilters
from storage import get_storage


async def build_evaluation_set(
    session: AsyncSession,
    eval_set: EvaluationSet,
    *,
    filters: EvaluationSetFilters | None = None,
    sample_ids: list[int] | None = None,
    limit: int | None = None,
    seed: int = 42,
) -> EvaluationSet:
    """Materialize a set from explicit ids (manual/imported) or filters (sampled).

Explicit ids that are not reserved are silently dropped: the reserved pool is
the only source of evaluation data. Contamination-quarantined rows are never
evaluation examples.
    """
    storage = get_storage()
    work = Path(settings.work_dir) / f"eval_{eval_set.id}"
    work.mkdir(parents=True, exist_ok=True)
    local = work / "data.parquet"

    ctx = await make_context(
        session, filters or EvaluationSetFilters(), None, Allocation.RESERVED_EVALUATION
    )
    try:
        where = "TRUE"
        if sample_ids:
            ids = ", ".join(str(int(i)) for i in sample_ids)
            where = f"sample_id IN ({ids})"
        order = f" ORDER BY hash(sample_id::VARCHAR || '-{int(seed)}')" if limit else ""
        tail = f" LIMIT {int(limit)}" if limit else ""
        ctx.sql(
            f"COPY (SELECT * FROM {{rel}} WHERE {where}{order}{tail}) "
            f"TO '{local}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        ctx.close()

    eval_set.sample_count = pl.scan_parquet(local).select(pl.len()).collect().item()
    eval_set.parquet_uri = storage.put_file(
        local, f"evaluation_sets/eval_{eval_set.id}/data.parquet"
    )
    local.unlink(missing_ok=True)
    await session.commit()
    await session.refresh(eval_set)
    return eval_set
