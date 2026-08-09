from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFound
from db.session import get_session
from models import Batch
from schemas import (
    BatchOut,
    BatchPurgeImpact,
    BatchPurgeIn,
    BatchPurgeOut,
    BatchRejectIn,
    BatchRestoreIn,
)
from services.batches.purge import (
    inspect_batch_purge,
    purge_batch,
    reject_batch,
    restore_batch,
)
from utils.duck import connect, parquet_source

router = APIRouter(prefix="/batches", tags=["batches"])


@router.get("", response_model=list[BatchOut])
async def list_batches(
    limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(Batch).order_by(Batch.id.desc()).limit(limit).offset(offset)
    )
    return rows.scalars().all()


@router.get("/count")
async def count_batches(session: AsyncSession = Depends(get_session)):
    return {"total": await session.scalar(select(func.count()).select_from(Batch))}


@router.get("/{batch_id}", response_model=BatchOut)
async def get_batch(batch_id: int, session: AsyncSession = Depends(get_session)):
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise NotFound("batch", batch_id)
    return batch


@router.post("/{batch_id}/reject", response_model=BatchOut)
async def reject(
    batch_id: int,
    payload: BatchRejectIn,
    session: AsyncSession = Depends(get_session),
):
    """Exclude a ready batch from future dataset builds without deleting it."""
    return await reject_batch(
        session,
        batch_id,
        confirm_name=payload.confirm_name,
        reason=payload.reason,
    )


@router.post("/{batch_id}/restore", response_model=BatchOut)
async def restore(
    batch_id: int,
    payload: BatchRestoreIn,
    session: AsyncSession = Depends(get_session),
):
    """Return a rejected batch to the ready pool while its objects still exist."""
    return await restore_batch(session, batch_id, confirm_name=payload.confirm_name)


@router.get("/{batch_id}/purge-impact", response_model=BatchPurgeImpact)
async def purge_impact(
    batch_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Dry-run a purge and report every dependency that blocks it."""
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise NotFound("batch", batch_id)
    return await inspect_batch_purge(session, batch)


@router.post("/{batch_id}/purge", response_model=BatchPurgeOut)
async def purge(
    batch_id: int,
    payload: BatchPurgeIn,
    session: AsyncSession = Depends(get_session),
):
    """Permanently remove an unused rejected or failed batch."""
    return await purge_batch(
        session,
        batch_id,
        confirm_name=payload.confirm_name,
        reason=payload.reason,
    )


@router.get("/{batch_id}/rows")
async def batch_rows(
    batch_id: int,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Read rows straight out of the batch Parquet file."""
    batch = await session.get(Batch, batch_id)
    if batch is None or not batch.parquet_uri:
        raise NotFound("batch", batch_id)
    con = connect()
    try:
        rel = con.sql(
            f"SELECT * FROM {parquet_source([batch.parquet_uri])} "
            f"LIMIT {int(limit)} OFFSET {int(offset)}"
        )
        return rel.pl().to_dicts()
    finally:
        con.close()
