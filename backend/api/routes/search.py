from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session
from schemas import DatasetFilters
from services.dataset_builder.builder import make_context, preview

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def search(
    q: str | None = None,
    sample_id: int | None = None,
    src_lang: str | None = None,
    tgt_lang: str | None = None,
    domain: str | None = None,
    source_id: int | None = None,
    batch_id: int | None = None,
    include_ignored: bool = False,
    limit: int = Query(50, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Full-row search over the Parquet data via DuckDB."""
    filters = DatasetFilters(
        src_langs=[src_lang] if src_lang else [],
        tgt_langs=[tgt_lang] if tgt_lang else [],
        domains=[domain] if domain else [],
        source_ids=[source_id] if source_id else [],
        batch_ids=[batch_id] if batch_id else [],
        text_contains=q,
        include_ignored=include_ignored,
    )
    ctx = await make_context(session, filters, [batch_id] if batch_id else None)
    try:
        if sample_id:
            return (
                ctx.sql(f"SELECT * FROM {{rel}} WHERE sample_id = {int(sample_id)}").pl().to_dicts()
            )
        return preview(ctx, limit=limit, offset=offset)
    finally:
        ctx.close()
