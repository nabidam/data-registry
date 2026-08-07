"""Evaluation sets: benchmark collections built only from reserved samples.

An evaluation set is a view over the RESERVED_EVALUATION pool — the samples the
ingestion pipeline held back. It is independent of train/validation/test splits,
and a reserved sample may belong to several collections at once without ever
becoming trainable again.
"""

from pathlib import Path

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from core.config import settings
from models import DatasetReservationSample, EvaluationSet
from services.dataset_builder.builder import make_context
from services.dataset_builder.filters import DatasetFilters, EvaluationSetFilters
from storage import get_storage


async def build_evaluation_set(
    session: AsyncSession,
    eval_set: EvaluationSet,
    *,
    filters: EvaluationSetFilters | None = None,
    sample_ids: list[int] | None = None,
    reservation_id: int | None = None,
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

    selected_filters = filters or EvaluationSetFilters()
    if reservation_id is None:
        ctx = await make_context(
            session, selected_filters, None, Allocation.RESERVED_EVALUATION
        )
    else:
        base_fields = set(DatasetFilters.model_fields)
        base_filters = DatasetFilters(
            **selected_filters.model_dump(include=base_fields)
        )
        ctx = await make_context(
            session, base_filters, None, Allocation.RESERVED_EVALUATION
        )
        rows = await session.execute(
            select(
                DatasetReservationSample.sample_id,
                DatasetReservationSample.evaluation_split,
                DatasetReservationSample.human_verify,
            ).where(DatasetReservationSample.reservation_id == reservation_id)
        )
        members = rows.all()
        metadata = pl.DataFrame(
            {
                "sample_id": pl.Series([row[0] for row in members], dtype=pl.Int64),
                "reservation_evaluation_split": pl.Series(
                    [row[1] for row in members], dtype=pl.Utf8
                ),
                "reservation_human_verify": pl.Series(
                    [row[2] for row in members], dtype=pl.Boolean
                ),
            }
        )
        ctx.con.register("reservation_members", metadata)
        predicates = ["TRUE"]
        if selected_filters.evaluation_splits:
            values = ", ".join(
                "'" + value.replace("'", "''") + "'"
                for value in selected_filters.evaluation_splits
            )
            predicates.append(f"m.reservation_evaluation_split IN ({values})")
        if selected_filters.human_verify is not None:
            predicates.append(
                "m.reservation_human_verify IS "
                + ("TRUE" if selected_filters.human_verify else "FALSE")
            )
        ctx.relation = (
            "(SELECT s.* EXCLUDE (evaluation_split, human_verify), "
            "m.reservation_evaluation_split AS evaluation_split, "
            "m.reservation_human_verify AS human_verify "
            f"FROM {ctx.relation} s JOIN reservation_members m USING (sample_id) "
            f"WHERE {' AND '.join(predicates)})"
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
