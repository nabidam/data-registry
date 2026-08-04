"""Evaluation reservation: the step every import passes through.

Runs between normalization and the moment a batch becomes visible to the
dataset builder, so reserved samples are isolated from the very start of their
lifecycle and no snapshot can ever have seen them as trainable.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from core.config import settings
from models import Sample
from services.evaluation.contamination import scan_snapshots
from services.evaluation.selectors import get_selector, reservation_size

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReservationPolicy:
    """Per-import reservation settings; defaults come from application config."""

    percent: float
    max_samples: int
    selector: str
    seed: int

    @classmethod
    def resolve(
        cls,
        percent: float | None = None,
        max_samples: int | None = None,
        selector: str | None = None,
        seed: int | None = None,
    ) -> "ReservationPolicy":
        return cls(
            percent=settings.evaluation_percent if percent is None else percent,
            max_samples=settings.evaluation_max_samples if max_samples is None else max_samples,
            selector=selector or settings.evaluation_selector,
            seed=settings.random_seed if seed is None else seed,
        )

    def as_dict(self) -> dict:
        return {
            "evaluation_percent": self.percent,
            "evaluation_max_samples": self.max_samples,
            "evaluation_selector": self.selector,
            "random_seed": self.seed,
        }


def allocate(df: pl.DataFrame, policy: ReservationPolicy) -> tuple[pl.DataFrame, dict]:
    """Attach an ``allocation`` column to normalized rows.

    Returns the frame plus a report describing what was reserved, which is kept
    on the batch so a reservation is auditable after the fact.
    """
    target = reservation_size(df.height, policy.percent, policy.max_samples)
    reserved = get_selector(policy.selector)(df, target, policy.seed)

    df = df.with_columns(
        pl.when(pl.col("sample_id").is_in(pl.Series("r", reserved, dtype=pl.Int64)))
        .then(pl.lit(str(Allocation.RESERVED_EVALUATION)))
        .otherwise(pl.lit(str(Allocation.TRAINABLE)))
        .alias("allocation")
    )
    report = {
        **policy.as_dict(),
        "imported": df.height,
        "target": target,
        "reserved": len(reserved),
        "trainable": df.height - len(reserved),
    }
    log.info(
        "reserved %s/%s samples for evaluation via %r selector",
        report["reserved"],
        df.height,
        policy.selector,
    )
    return df, report


def restore_allocation(sample: Sample) -> str:
    """Allocation a sample returns to when un-ignored: reservation is permanent."""
    return Allocation.RESERVED_EVALUATION if sample.reserved_at else Allocation.TRAINABLE


async def set_allocation(
    session: AsyncSession,
    sample_ids: list[int],
    allocation: str,
    *,
    reason: str = "manual allocation change",
) -> dict:
    """Change the allocation of existing samples.

    Reserving after the fact cannot rewrite history, so any snapshot that already
    exported one of these samples is recorded as historically contaminated.
    """
    if allocation not in set(Allocation):
        raise ValueError(f"unknown allocation {allocation!r}")

    rows = await session.execute(select(Sample).where(Sample.id.in_(sample_ids)))
    samples = rows.scalars().all()
    now = datetime.now(UTC)
    newly_reserved: list[int] = []

    for sample in samples:
        if allocation == Allocation.TRAINABLE and sample.reserved_at:
            # A reserved sample never becomes trainable again.
            sample.allocation = Allocation.RESERVED_EVALUATION
            continue
        if allocation == Allocation.RESERVED_EVALUATION and sample.reserved_at is None:
            sample.reserved_at = now
            newly_reserved.append(sample.id)
        sample.allocation = allocation

    await session.commit()
    contaminated = await scan_snapshots(session, newly_reserved, reason=reason)
    return {
        "updated": len(samples),
        "newly_reserved": len(newly_reserved),
        "contaminated_snapshots": [
            {"snapshot_id": c.snapshot_id, "sample_count": c.sample_count} for c in contaminated
        ],
    }
