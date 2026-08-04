from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from db.session import get_session
from models import (
    Batch,
    DatasetDefinition,
    EvaluationSet,
    Experiment,
    Model,
    Sample,
    Snapshot,
    SnapshotContamination,
    Source,
)

router = APIRouter(prefix="/stats", tags=["stats"])


async def _grouped(session: AsyncSession, *cols, limit: int = 50):
    stmt = (
        select(*cols, func.count().label("count"))
        .group_by(*cols)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [dict(r) for r in (await session.execute(stmt)).mappings()]


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)):
    """Dashboard counters. All served from Postgres metadata."""

    async def count(model) -> int:
        return await session.scalar(select(func.count()).select_from(model)) or 0

    async def allocated(allocation: Allocation) -> int:
        return (
            await session.scalar(
                select(func.count()).select_from(Sample).where(Sample.allocation == allocation)
            )
            or 0
        )

    return {
        "samples": await count(Sample),
        "batches": await count(Batch),
        "sources": await count(Source),
        "datasets": await count(DatasetDefinition),
        "snapshots": await count(Snapshot),
        "evaluation_sets": await count(EvaluationSet),
        "experiments": await count(Experiment),
        "models": await count(Model),
        "trainable_samples": await allocated(Allocation.TRAINABLE),
        "reserved_samples": await allocated(Allocation.RESERVED_EVALUATION),
        "quarantined_samples": await allocated(Allocation.QUARANTINED),
        "ignored_samples": await allocated(Allocation.IGNORED),
        "contaminated_snapshots": await count(SnapshotContamination),
    }


@router.get("/allocations")
async def allocations(session: AsyncSession = Depends(get_session)):
    return await _grouped(session, Sample.allocation)


@router.get("/language-pairs")
async def language_pairs(session: AsyncSession = Depends(get_session)):
    return await _grouped(session, Sample.src_lang, Sample.tgt_lang)


@router.get("/domains")
async def domains(session: AsyncSession = Depends(get_session)):
    return await _grouped(session, Sample.domain)


@router.get("/batches")
async def by_batch(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        select(Batch.id, Batch.name, Batch.sample_count).order_by(Batch.id.desc()).limit(50)
    )
    return [dict(r) for r in rows.mappings()]


@router.get("/sources")
async def by_source(session: AsyncSession = Depends(get_session)):
    return await _grouped(session, Sample.source_id)
