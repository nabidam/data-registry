from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFound
from db.session import get_session
from models import EvaluationSet
from schemas import EvaluationSetIn, EvaluationSetOut
from services.evaluation.service import build_evaluation_set
from utils.duck import connect, parquet_source

router = APIRouter(prefix="/evaluation-sets", tags=["evaluation-sets"])


@router.get("", response_model=list[EvaluationSetOut])
async def list_sets(
    limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(EvaluationSet).order_by(EvaluationSet.id.desc()).limit(limit).offset(offset)
    )
    return rows.scalars().all()


@router.post("", response_model=EvaluationSetOut, status_code=201)
async def create_set(payload: EvaluationSetIn, session: AsyncSession = Depends(get_session)):
    """Build an evaluation set from explicit sample ids or from filters + a sample limit."""
    eval_set = EvaluationSet(
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        spec=payload.model_dump(mode="json"),
    )
    session.add(eval_set)
    await session.flush()
    await build_evaluation_set(
        session,
        eval_set,
        filters=payload.filters,
        sample_ids=payload.sample_ids,
        limit=payload.limit,
        seed=payload.seed,
    )
    return eval_set


@router.get("/{set_id}", response_model=EvaluationSetOut)
async def get_set(set_id: int, session: AsyncSession = Depends(get_session)):
    obj = await session.get(EvaluationSet, set_id)
    if obj is None:
        raise NotFound("evaluation set", set_id)
    return obj


@router.get("/{set_id}/rows")
async def set_rows(
    set_id: int, limit: int = 50, offset: int = 0, session: AsyncSession = Depends(get_session)
):
    obj = await session.get(EvaluationSet, set_id)
    if obj is None or not obj.parquet_uri:
        raise NotFound("evaluation set", set_id)
    con = connect()
    try:
        return (
            con.sql(
                f"SELECT * FROM {parquet_source([obj.parquet_uri])} "
                f"LIMIT {int(limit)} OFFSET {int(offset)}"
            )
            .pl()
            .to_dicts()
        )
    finally:
        con.close()


@router.delete("/{set_id}", status_code=204)
async def delete_set(set_id: int, session: AsyncSession = Depends(get_session)):
    obj = await session.get(EvaluationSet, set_id)
    if obj is None:
        raise NotFound("evaluation set", set_id)
    await session.delete(obj)
    await session.commit()
