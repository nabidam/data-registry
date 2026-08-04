from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFound
from db.session import get_session
from models import DatasetDefinition
from schemas import DatasetIn, DatasetOut
from services.dataset_builder.builder import (
    context_for_definition,
    count_rows,
    preview,
    statistics,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])


async def _get(session: AsyncSession, dataset_id: int) -> DatasetDefinition:
    obj = await session.get(DatasetDefinition, dataset_id)
    if obj is None:
        raise NotFound("dataset", dataset_id)
    return obj


@router.get("", response_model=list[DatasetOut])
async def list_datasets(
    limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(DatasetDefinition).order_by(DatasetDefinition.id.desc()).limit(limit).offset(offset)
    )
    return rows.scalars().all()


@router.post("", response_model=DatasetOut, status_code=201)
async def create_dataset(payload: DatasetIn, session: AsyncSession = Depends(get_session)):
    obj = DatasetDefinition(
        name=payload.name,
        description=payload.description,
        batch_ids=payload.batch_ids,
        filters=payload.filters.model_dump(),
    )
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get_dataset(dataset_id: int, session: AsyncSession = Depends(get_session)):
    return await _get(session, dataset_id)


@router.patch("/{dataset_id}", response_model=DatasetOut)
async def update_dataset(
    dataset_id: int, payload: DatasetIn, session: AsyncSession = Depends(get_session)
):
    obj = await _get(session, dataset_id)
    obj.name = payload.name
    obj.description = payload.description
    obj.batch_ids = payload.batch_ids
    obj.filters = payload.filters.model_dump()
    await session.commit()
    await session.refresh(obj)
    return obj


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset(dataset_id: int, session: AsyncSession = Depends(get_session)):
    obj = await _get(session, dataset_id)
    await session.delete(obj)
    await session.commit()


@router.get("/{dataset_id}/preview")
async def preview_dataset(
    dataset_id: int,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    obj = await _get(session, dataset_id)
    ctx = await context_for_definition(session, obj)
    try:
        return preview(ctx, limit=limit, offset=offset)
    finally:
        ctx.close()


@router.get("/{dataset_id}/count")
async def count_dataset(dataset_id: int, session: AsyncSession = Depends(get_session)):
    obj = await _get(session, dataset_id)
    ctx = await context_for_definition(session, obj)
    try:
        return {"total": count_rows(ctx)}
    finally:
        ctx.close()


@router.get("/{dataset_id}/statistics")
async def dataset_statistics(dataset_id: int, session: AsyncSession = Depends(get_session)):
    obj = await _get(session, dataset_id)
    ctx = await context_for_definition(session, obj)
    try:
        return statistics(ctx)
    finally:
        ctx.close()
