from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from core.errors import NotFound
from db.session import get_session
from models import Batch, Sample
from schemas import AllocationIn, SampleOut
from services.evaluation.reservation import set_allocation
from utils.duck import connect, parquet_source

router = APIRouter(prefix="/samples", tags=["samples"])


@router.get("")
async def list_samples(
    batch_id: int | None = None,
    src_lang: str | None = None,
    tgt_lang: str | None = None,
    domain: str | None = None,
    source_id: int | None = None,
    allocation: Allocation | None = None,
    min_quality: float | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Metadata-only listing, served from Postgres."""
    stmt = select(Sample)
    conditions = []
    if batch_id:
        conditions.append(Sample.batch_id == batch_id)
    if src_lang:
        conditions.append(Sample.src_lang == src_lang)
    if tgt_lang:
        conditions.append(Sample.tgt_lang == tgt_lang)
    if domain:
        conditions.append(Sample.domain == domain)
    if source_id:
        conditions.append(Sample.source_id == source_id)
    if allocation:
        conditions.append(Sample.allocation == allocation)
    if min_quality is not None:
        conditions.append(Sample.quality >= min_quality)
    if conditions:
        stmt = stmt.where(*conditions)

    total = await session.scalar(
        select(func.count()).select_from(Sample).where(*conditions)
        if conditions
        else select(func.count()).select_from(Sample)
    )
    rows = await session.execute(stmt.order_by(Sample.id).limit(limit).offset(offset))
    items = [SampleOut.model_validate(s) for s in rows.scalars().all()]
    return {"total": total, "items": items}


@router.post("/allocation")
async def change_allocation(payload: AllocationIn, session: AsyncSession = Depends(get_session)):
    """Move samples between allocations.

    Reserving samples that earlier snapshots already exported does not rewrite
    those snapshots; the overlap is recorded as historical contamination instead.
    """
    return await set_allocation(
        session,
        payload.sample_ids,
        payload.allocation,
        reason=payload.reason or "manual allocation change",
    )


@router.get("/{sample_id}")
async def get_sample(sample_id: int, session: AsyncSession = Depends(get_session)):
    """Metadata from Postgres joined with the text from Parquet."""
    sample = await session.get(Sample, sample_id)
    if sample is None:
        raise NotFound("sample", sample_id)
    batch = await session.get(Batch, sample.batch_id)
    text = {}
    if batch and batch.parquet_uri:
        con = connect()
        try:
            rows = (
                con.sql(
                    f"SELECT source_text, target_text, meta FROM "
                    f"{parquet_source([batch.parquet_uri])} WHERE sample_id = {int(sample_id)}"
                )
                .pl()
                .to_dicts()
            )
            text = rows[0] if rows else {}
        finally:
            con.close()
    return {**SampleOut.model_validate(sample).model_dump(), **text}
