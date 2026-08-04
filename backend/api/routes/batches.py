from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFound
from db.session import get_session
from models import Batch
from schemas import BatchOut
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
