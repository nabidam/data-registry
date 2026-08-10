from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import BadRequest, NotFound
from core.locks import lock_allocation_boundary
from db.session import get_session
from models import Batch, DatasetDefinition, DatasetReservation, Snapshot
from schemas import (
    DatasetIn,
    DatasetOut,
    DatasetReservationIn,
    DatasetReservationOut,
    DatasetReservationRevertIn,
    ReservationRevertImpact,
)
from services.dataset_builder.builder import (
    allocation_counts,
    context_for_definition,
    count_rows,
    preview,
    statistics,
)
from services.evaluation.reservation_revert import (
    inspect_reservation_revert,
    revert_dataset_reservation,
)
from services.evaluation.reservation_config import (
    load_contamination_safe_config,
    validate_policy,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])


async def _get(session: AsyncSession, dataset_id: int) -> DatasetDefinition:
    obj = await session.get(DatasetDefinition, dataset_id)
    if obj is None:
        raise NotFound("dataset", dataset_id)
    return obj


async def _validate_batches(session: AsyncSession, payload: DatasetIn) -> list[int]:
    requested = (
        [rule.batch_id for rule in payload.batch_rules]
        if payload.batch_rules
        else list(payload.batch_ids)
    )
    if not requested:
        return []
    rows = await session.execute(select(Batch.id, Batch.status).where(Batch.id.in_(requested)))
    found = {batch_id: status for batch_id, status in rows}
    missing = sorted(set(requested).difference(found))
    if missing:
        raise BadRequest(f"unknown batch ids: {missing}")
    unavailable = sorted(batch_id for batch_id, status in found.items() if status != "ready")
    if unavailable:
        raise BadRequest(f"batch ids are not ready: {unavailable}")
    return requested


@router.get("", response_model=list[DatasetOut])
async def list_datasets(
    limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(DatasetDefinition).order_by(DatasetDefinition.id.desc()).limit(limit).offset(offset)
    )
    return rows.scalars().all()


@router.get("/reservations", response_model=list[DatasetReservationOut])
async def list_reservations(
    limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(DatasetReservation)
        .order_by(DatasetReservation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return rows.scalars().all()


@router.post("", response_model=DatasetOut, status_code=201)
async def create_dataset(payload: DatasetIn, session: AsyncSession = Depends(get_session)):
    batch_ids = await _validate_batches(session, payload)
    obj = DatasetDefinition(
        name=payload.name,
        description=payload.description,
        batch_ids=batch_ids,
        batch_rules=[rule.model_dump(mode="json") for rule in payload.batch_rules],
        composition_seed=payload.composition_seed,
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
    batch_ids = await _validate_batches(session, payload)
    obj.name = payload.name
    obj.description = payload.description
    obj.batch_ids = batch_ids
    obj.batch_rules = [rule.model_dump(mode="json") for rule in payload.batch_rules]
    obj.composition_seed = payload.composition_seed
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


@router.get("/{dataset_id}/allocation-summary")
async def dataset_allocation_summary(
    dataset_id: int, session: AsyncSession = Depends(get_session)
):
    obj = await _get(session, dataset_id)
    return await allocation_counts(session, obj)


@router.get("/{dataset_id}/reservations", response_model=list[DatasetReservationOut])
async def list_dataset_reservations(
    dataset_id: int, session: AsyncSession = Depends(get_session)
):
    await _get(session, dataset_id)
    rows = await session.execute(
        select(DatasetReservation)
        .where(DatasetReservation.dataset_id == dataset_id)
        .order_by(DatasetReservation.id.desc())
    )
    return rows.scalars().all()


@router.post(
    "/{dataset_id}/reservations",
    response_model=DatasetReservationOut,
    status_code=202,
)
async def create_dataset_reservation(
    dataset_id: int,
    payload: DatasetReservationIn,
    session: AsyncSession = Depends(get_session),
):
    await _get(session, dataset_id)
    if payload.selector == "contamination_safe":
        # Reject a broken policy now rather than after the worker has spent an
        # hour on selection and then marked the reservation failed.
        try:
            validate_policy(load_contamination_safe_config())
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
    await lock_allocation_boundary(session)
    active = await session.scalar(
        select(DatasetReservation.id).where(
            DatasetReservation.status.in_(["queued", "running"])
        )
    )
    if active is not None:
        raise BadRequest(f"dataset reservation {active} is already active")
    building_snapshot = await session.scalar(
        select(Snapshot.id).where(Snapshot.status == "building")
    )
    if building_snapshot is not None:
        raise BadRequest(
            f"snapshot {building_snapshot} is still building; "
            "wait before changing global allocations"
        )
    reservation = DatasetReservation(
        dataset_id=dataset_id,
        **payload.model_dump(exclude={"confirm_irreversible"}),
    )
    session.add(reservation)
    await session.commit()
    await session.refresh(reservation)
    return reservation


async def _get_reservation(
    session: AsyncSession, dataset_id: int, reservation_id: int
) -> DatasetReservation:
    reservation = await session.get(DatasetReservation, reservation_id)
    if reservation is None or reservation.dataset_id != dataset_id:
        raise NotFound("dataset reservation", reservation_id)
    return reservation


@router.get(
    "/{dataset_id}/reservations/{reservation_id}/revert-impact",
    response_model=ReservationRevertImpact,
)
async def reservation_revert_impact(
    dataset_id: int,
    reservation_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Dry-run a revert and report every dependency that blocks it."""
    reservation = await _get_reservation(session, dataset_id, reservation_id)
    return await inspect_reservation_revert(session, reservation)


@router.post(
    "/{dataset_id}/reservations/{reservation_id}/revert",
    response_model=ReservationRevertImpact,
)
async def revert_reservation(
    dataset_id: int,
    reservation_id: int,
    payload: DatasetReservationRevertIn,
    session: AsyncSession = Depends(get_session),
):
    """Return this reservation's samples to TRAINABLE.

    The recovery path for a reservation that was itself wrong. Reservation and
    quarantine are otherwise permanent, so this is deliberately restricted to the
    most recent completed run and refuses once an evaluation set depends on it.
    """
    reservation = await _get_reservation(session, dataset_id, reservation_id)
    await lock_allocation_boundary(session)
    try:
        return await revert_dataset_reservation(session, reservation)
    except ValueError as exc:
        await session.rollback()
        raise BadRequest(str(exc)) from exc


@router.get("/{dataset_id}/statistics")
async def dataset_statistics(dataset_id: int, session: AsyncSession = Depends(get_session)):
    obj = await _get(session, dataset_id)
    ctx = await context_for_definition(session, obj)
    try:
        return statistics(ctx)
    finally:
        ctx.close()
