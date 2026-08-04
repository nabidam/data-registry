from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFound
from db.session import get_session
from models import Annotation, Sample
from schemas import AnnotationIn, AnnotationOut

router = APIRouter(prefix="/annotations", tags=["annotations"])


@router.get("", response_model=list[AnnotationOut])
async def list_annotations(
    sample_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Annotation).order_by(Annotation.id.desc())
    if sample_id:
        stmt = stmt.where(Annotation.sample_id == sample_id)
    rows = await session.execute(stmt.limit(limit).offset(offset))
    return rows.scalars().all()


@router.post("", response_model=AnnotationOut, status_code=201)
async def create_annotation(payload: AnnotationIn, session: AsyncSession = Depends(get_session)):
    """Annotations are append-only; original samples are never modified.

    The sample's ``status`` column is a denormalized copy of the latest
    ignore decision so dataset builds can filter without a join.
    """
    sample = await session.get(Sample, payload.sample_id)
    if sample is None:
        raise NotFound("sample", payload.sample_id)

    annotation = Annotation(**payload.model_dump())
    session.add(annotation)
    sample.status = "ignored" if payload.ignored else "active"
    if payload.quality is not None:
        sample.quality = payload.quality
    await session.commit()
    await session.refresh(annotation)
    return annotation


@router.delete("/{annotation_id}", status_code=204)
async def delete_annotation(annotation_id: int, session: AsyncSession = Depends(get_session)):
    obj = await session.get(Annotation, annotation_id)
    if obj is None:
        raise NotFound("annotation", annotation_id)
    await session.delete(obj)
    await session.commit()
