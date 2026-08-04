from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.errors import BadRequest, NotFound
from db.session import get_session
from models import Snapshot
from storage import get_storage

router = APIRouter(prefix="/exports", tags=["exports"])

SPLITS = {"train", "validation", "test", "manifest"}


@router.get("")
async def list_exports(session: AsyncSession = Depends(get_session)):
    """Every ready snapshot is an export."""
    rows = await session.execute(
        select(Snapshot).where(Snapshot.status == "ready").order_by(Snapshot.id.desc())
    )
    return [
        {
            "snapshot_id": s.id,
            "name": s.name,
            "prefix_uri": s.prefix_uri,
            "counts": (s.stats or {}).get("counts", {}),
            "files": (s.manifest or {}).get("files", {}),
            "created_at": s.created_at,
        }
        for s in rows.scalars().all()
    ]


@router.get("/{snapshot_id}/{name}")
async def download(name: str, snapshot_id: int, session: AsyncSession = Depends(get_session)):
    """Stream a snapshot file (train|validation|test|manifest) back to the client."""
    if name not in SPLITS:
        raise BadRequest(f"unknown file {name!r}, expected one of {sorted(SPLITS)}")
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.status != "ready":
        raise NotFound("snapshot", snapshot_id)

    filename = "manifest.json" if name == "manifest" else f"{name}.parquet"
    key = f"snapshots/snapshot_{snapshot_id}/{filename}"
    local = Path(settings.work_dir) / "downloads" / f"{uuid4().hex}_{filename}"
    get_storage().get_file(key, local)
    return FileResponse(local, filename=f"{snapshot.name}_{filename}")
