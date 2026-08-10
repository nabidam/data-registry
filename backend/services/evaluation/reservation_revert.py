"""Undo the allocations a dataset reservation applied.

Reservation and quarantine are permanent by design: a snapshot must never be able
to see a row that an evaluation set also saw, and `restore_allocation` deliberately
refuses to hand a protected row back to training. That invariant protects the
registry from drift, but it leaves no recovery path when a reservation itself was
wrong — a mistaken `comparison_scope`, a policy typo, a selector bug that
quarantined far more than intended.

This module is that recovery path, and it is narrow on purpose. It reverses one
reservation, only while that reservation is still the most recent completed one,
and only for rows whose protection timestamp still matches the run. Anything a
human or a later job has touched since is left alone.

Batch-level mistakes have their own reversal: reject and purge the batch. This
module is for reservations run over an existing composition, where the rows
predate the mistake and must survive it.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from models import (
    DatasetReservation,
    DatasetReservationSample,
    EvaluationSet,
    Sample,
    Snapshot,
)

log = logging.getLogger(__name__)

REVERTIBLE_STATUSES = {"ready", "failed"}


@dataclass
class ReservationRevertImpact:
    reservation_id: int
    dataset_id: int
    status: str
    applied_at: datetime | None
    reserved_samples: int
    quarantined_samples: int
    can_revert: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def _counts(session: AsyncSession, applied_at: datetime) -> tuple[int, int]:
    """Rows still carrying this run's protection, by kind."""
    reserved = await session.scalar(
        select(func.count())
        .select_from(Sample)
        .where(
            Sample.reserved_at == applied_at,
            Sample.allocation.in_([Allocation.RESERVED_EVALUATION, Allocation.IGNORED]),
        )
    )
    quarantined = await session.scalar(
        select(func.count())
        .select_from(Sample)
        .where(
            Sample.quarantined_at == applied_at,
            Sample.allocation.in_([Allocation.QUARANTINED, Allocation.IGNORED]),
        )
    )
    return int(reserved or 0), int(quarantined or 0)


async def inspect_reservation_revert(
    session: AsyncSession, reservation: DatasetReservation
) -> ReservationRevertImpact:
    """Dry-run a revert and report everything that blocks it."""
    blockers: list[str] = []
    warnings: list[str] = []

    if reservation.status not in REVERTIBLE_STATUSES:
        if reservation.status in {"queued", "running"}:
            blockers.append(f"the reservation is still {reservation.status}")
        elif reservation.status == "reverted":
            blockers.append("the reservation has already been reverted")
        else:
            blockers.append(f"status {reservation.status!r} is not revertible")

    if reservation.applied_at is None:
        # Pre-migration runs, and runs that failed before applying allocations.
        blockers.append(
            "this reservation did not record when it applied allocations; "
            "its rows cannot be attributed to it and must be corrected by hand"
        )

    active = await session.execute(
        select(DatasetReservation.id).where(
            DatasetReservation.status.in_(["queued", "running"])
        )
    )
    active_ids = [row[0] for row in active]
    if active_ids:
        blockers.append(
            "dataset reservations are active: " + ", ".join(f"#{i}" for i in active_ids)
        )

    building = await session.execute(select(Snapshot.id).where(Snapshot.status == "building"))
    building_ids = [row[0] for row in building]
    if building_ids:
        blockers.append(
            "snapshots are building: " + ", ".join(f"#{i}" for i in building_ids)
        )

    # A later reservation may have selected rows this one left trainable. Undoing
    # an older run underneath a newer one would hand those rows back to training
    # while the newer benchmark still contains their neighbours.
    if reservation.applied_at is not None:
        newer = await session.execute(
            select(DatasetReservation.id)
            .where(
                DatasetReservation.id != reservation.id,
                DatasetReservation.status == "ready",
                DatasetReservation.applied_at.is_not(None),
                DatasetReservation.applied_at > reservation.applied_at,
            )
            .order_by(DatasetReservation.applied_at)
        )
        newer_ids = [row[0] for row in newer]
        if newer_ids:
            blockers.append(
                "later reservations have run since: "
                + ", ".join(f"#{i}" for i in newer_ids)
                + "; revert those first"
            )

    # An evaluation set materialized from this reservation is a published
    # benchmark. Un-reserving its rows would let training see them.
    # The creating payload is stored verbatim in spec, so this is where the link
    # between an evaluation set and its reservation lives.
    eval_sets = await session.execute(
        select(EvaluationSet.id, EvaluationSet.name).where(
            EvaluationSet.spec["reservation_id"].astext == str(reservation.id)
        )
    )
    for eval_id, name in eval_sets:
        blockers.append(
            f"evaluation set #{eval_id} ({name}) was built from this reservation; "
            "delete it before reverting"
        )

    reserved_count, quarantined_count = (
        await _counts(session, reservation.applied_at)
        if reservation.applied_at is not None
        else (0, 0)
    )

    recorded = int(
        await session.scalar(
            select(func.count())
            .select_from(DatasetReservationSample)
            .where(DatasetReservationSample.reservation_id == reservation.id)
        )
        or 0
    )
    if recorded and reserved_count < recorded:
        warnings.append(
            f"{recorded - reserved_count:,} of {recorded:,} selected samples no longer "
            "carry this reservation's timestamp and will be left as they are"
        )
    if not reserved_count and not quarantined_count and not blockers:
        warnings.append("no samples still carry this reservation's protection")

    return ReservationRevertImpact(
        reservation_id=reservation.id,
        dataset_id=reservation.dataset_id,
        status=reservation.status,
        applied_at=reservation.applied_at,
        reserved_samples=reserved_count,
        quarantined_samples=quarantined_count,
        can_revert=not blockers,
        blockers=blockers,
        warnings=warnings,
    )


async def revert_dataset_reservation(
    session: AsyncSession, reservation: DatasetReservation
) -> ReservationRevertImpact:
    """Return this reservation's rows to TRAINABLE and mark the run reverted.

    Re-inspects under the caller's transaction so a concurrent job cannot slip
    between the impact check and the update. Rows are matched on the protection
    timestamp and on still holding the allocation this run gave them, so a row a
    human has since re-classified is never overwritten.
    """
    impact = await inspect_reservation_revert(session, reservation)
    if not impact.can_revert:
        raise ValueError(
            f"dataset reservation {reservation.id} cannot be reverted: "
            + "; ".join(impact.blockers)
        )

    applied_at = reservation.applied_at
    reserved = await session.execute(
        update(Sample)
        .where(
            Sample.reserved_at == applied_at,
            Sample.allocation == Allocation.RESERVED_EVALUATION,
        )
        .values(allocation=Allocation.TRAINABLE, reserved_at=None)
    )
    quarantined = await session.execute(
        update(Sample)
        .where(
            Sample.quarantined_at == applied_at,
            Sample.allocation == Allocation.QUARANTINED,
        )
        .values(allocation=Allocation.TRAINABLE, quarantined_at=None)
    )
    # An ignored row keeps its own allocation, but must lose the protection it
    # would otherwise be restored to when someone un-ignores it later.
    await session.execute(
        update(Sample)
        .where(Sample.reserved_at == applied_at, Sample.allocation == Allocation.IGNORED)
        .values(reserved_at=None)
    )
    await session.execute(
        update(Sample)
        .where(Sample.quarantined_at == applied_at, Sample.allocation == Allocation.IGNORED)
        .values(quarantined_at=None)
    )

    # The selection record is deleted with the reservation's claim on those rows;
    # keeping it would let an evaluation set reference members that are trainable.
    await session.execute(
        DatasetReservationSample.__table__.delete().where(
            DatasetReservationSample.reservation_id == reservation.id
        )
    )

    reservation.status = "reverted"
    reservation.report = {
        **dict(reservation.report or {}),
        "reverted": {
            "at": datetime.now(UTC).isoformat(),
            "applied_at": applied_at.isoformat() if applied_at else None,
            "restored_reserved": reserved.rowcount,
            "restored_quarantined": quarantined.rowcount,
            "warnings": impact.warnings,
        },
    }
    await session.commit()
    log.warning(
        "reverted dataset reservation %s: %s reserved and %s quarantined samples "
        "returned to TRAINABLE",
        reservation.id,
        reserved.rowcount,
        quarantined.rowcount,
    )
    return ReservationRevertImpact(
        reservation_id=reservation.id,
        dataset_id=reservation.dataset_id,
        status=reservation.status,
        applied_at=applied_at,
        reserved_samples=reserved.rowcount,
        quarantined_samples=quarantined.rowcount,
        can_revert=False,
        blockers=[],
        warnings=impact.warnings,
    )
