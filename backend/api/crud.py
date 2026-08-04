"""Generic CRUD router.

Most entities are plain metadata records; this keeps them to one line each
instead of five near-identical route modules.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NotFound
from db.session import get_session


def crud_router(
    *,
    model: Any,
    read_schema: type[BaseModel],
    create_schema: type[BaseModel],
    prefix: str,
    tag: str,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.get("", response_model=list[read_schema])
    async def list_items(
        limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_session)
    ):
        rows = await session.execute(
            select(model).order_by(model.id.desc()).limit(limit).offset(offset)
        )
        return rows.scalars().all()

    @router.get("/count")
    async def count_items(session: AsyncSession = Depends(get_session)):
        total = await session.scalar(select(func.count()).select_from(model))
        return {"total": total}

    @router.post("", response_model=read_schema, status_code=201)
    async def create_item(payload: create_schema, session: AsyncSession = Depends(get_session)):
        obj = model(**payload.model_dump())
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        return obj

    @router.get("/{item_id}", response_model=read_schema)
    async def get_item(item_id: int, session: AsyncSession = Depends(get_session)):
        obj = await session.get(model, item_id)
        if obj is None:
            raise NotFound(tag, item_id)
        return obj

    @router.patch("/{item_id}", response_model=read_schema)
    async def update_item(
        item_id: int, payload: create_schema, session: AsyncSession = Depends(get_session)
    ):
        obj = await session.get(model, item_id)
        if obj is None:
            raise NotFound(tag, item_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(obj, key, value)
        await session.commit()
        await session.refresh(obj)
        return obj

    @router.delete("/{item_id}", status_code=204)
    async def delete_item(item_id: int, session: AsyncSession = Depends(get_session)):
        obj = await session.get(model, item_id)
        if obj is None:
            raise NotFound(tag, item_id)
        await session.delete(obj)
        await session.commit()

    return router
