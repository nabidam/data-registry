from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import BadRequest, NotFound
from db.session import SessionLocal, get_session
from models import DatasetDefinition, Snapshot, SplitDefinition
from schemas import SnapshotIn, SnapshotOut
from services.snapshots.service import build_snapshot

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


async def _run_build(snapshot_id: int) -> None:
    """Run the build in its own session; the request already returned."""
    async with SessionLocal() as session:
        snapshot = await session.get(Snapshot, snapshot_id)
        if snapshot is None:
            return
        definition = await session.get(DatasetDefinition, snapshot.dataset_id)
        split = await session.get(SplitDefinition, snapshot.split_id) if snapshot.split_id else None
        await build_snapshot(session, snapshot, definition, split)


@router.get("", response_model=list[SnapshotOut])
async def list_snapshots(
    limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(Snapshot).order_by(Snapshot.id.desc()).limit(limit).offset(offset)
    )
    return rows.scalars().all()


@router.post("", response_model=SnapshotOut, status_code=201)
async def create_snapshot(
    payload: SnapshotIn,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Create the snapshot record and build it in the background.

    Snapshots are immutable: rebuilding means creating a new one.
    """
    definition = await session.get(DatasetDefinition, payload.dataset_id)
    if definition is None:
        raise NotFound("dataset", payload.dataset_id)
    if payload.split_id and await session.get(SplitDefinition, payload.split_id) is None:
        raise NotFound("split", payload.split_id)

    snapshot = Snapshot(**payload.model_dump(), status="building")
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    background.add_task(_run_build, snapshot.id)
    return snapshot


@router.get("/{snapshot_id}", response_model=SnapshotOut)
async def get_snapshot(snapshot_id: int, session: AsyncSession = Depends(get_session)):
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise NotFound("snapshot", snapshot_id)
    return snapshot


@router.get("/{snapshot_id}/manifest")
async def get_manifest(snapshot_id: int, session: AsyncSession = Depends(get_session)):
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise NotFound("snapshot", snapshot_id)
    if not snapshot.manifest:
        raise BadRequest(f"snapshot {snapshot_id} is {snapshot.status}, no manifest yet")
    return snapshot.manifest


@router.delete("/{snapshot_id}", status_code=204)
async def delete_snapshot(snapshot_id: int, session: AsyncSession = Depends(get_session)):
    """Removes the registry record only; exported files stay in object storage."""
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise NotFound("snapshot", snapshot_id)
    await session.delete(snapshot)
    await session.commit()
