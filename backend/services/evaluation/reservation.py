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
from services.evaluation.contamination_safe import SelectionResult
from services.evaluation.selectors import get_selector, reservation_size

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReservationPolicy:
    """Per-import reservation settings; defaults come from application config."""

    percent: float
    max_samples: int
    selector: str
    seed: int
    comparison_scope: str = "pair"

    @classmethod
    def resolve(
        cls,
        percent: float | None = None,
        max_samples: int | None = None,
        selector: str | None = None,
        seed: int | None = None,
        comparison_scope: str | None = None,
    ) -> "ReservationPolicy":
        return cls(
            percent=settings.evaluation_percent if percent is None else percent,
            max_samples=settings.evaluation_max_samples if max_samples is None else max_samples,
            selector=selector or settings.evaluation_selector,
            seed=settings.random_seed if seed is None else seed,
            comparison_scope=comparison_scope or settings.evaluation_comparison_scope,
        )

    def as_dict(self) -> dict:
        return {
            "evaluation_percent": self.percent,
            "evaluation_max_samples": self.max_samples,
            "evaluation_selector": self.selector,
            "random_seed": self.seed,
            "comparison_scope": self.comparison_scope,
        }


def allocate(df: pl.DataFrame, policy: ReservationPolicy) -> tuple[pl.DataFrame, dict]:
    """Attach an ``allocation`` column to normalized rows.

    Returns the frame plus a report describing what was reserved, which is kept
    on the batch so a reservation is auditable after the fact.
    """
    target = reservation_size(df.height, policy.percent, policy.max_samples)
    selection = get_selector(policy.selector)(df, target, policy.seed)
    return apply_selection(df, policy, selection, imported_count=df.height)


def apply_selection(
    df: pl.DataFrame,
    policy: ReservationPolicy,
    selection: SelectionResult | list[int],
    *,
    imported_count: int,
) -> tuple[pl.DataFrame, dict]:
    """Apply a selection made from a bounded candidate pool to one frame.

    Large imports select from a deterministic bounded pool, then call this
    function for every normalized shard. Rows not present in the selection
    remain trainable and never need to be held in memory together.
    """
    target = reservation_size(imported_count, policy.percent, policy.max_samples)
    if isinstance(selection, SelectionResult):
        reserved = selection.reserved_ids
        quarantined = selection.quarantined_ids
        dev = selection.dev_ids
        gold = selection.gold_ids
        annotations = selection.annotations
        selector_report = selection.report
    else:
        reserved = selection
        quarantined = []
        dev = []
        gold = []
        annotations = {}
        selector_report = {"strategy": policy.selector}

    df = df.with_columns(
        pl.when(pl.col("sample_id").is_in(pl.Series("reserved", reserved, dtype=pl.Int64)))
        .then(pl.lit(str(Allocation.RESERVED_EVALUATION)))
        .when(pl.col("sample_id").is_in(pl.Series("quarantined", quarantined, dtype=pl.Int64)))
        .then(pl.lit(str(Allocation.QUARANTINED)))
        .otherwise(pl.lit(str(Allocation.TRAINABLE)))
        .alias("allocation")
    ).with_columns(
        pl.when(pl.col("sample_id").is_in(pl.Series("reserved", reserved, dtype=pl.Int64)))
        .then(
            pl.when(pl.col("sample_id").is_in(pl.Series("dev", dev, dtype=pl.Int64)))
            .then(pl.lit("dev"))
            .otherwise(pl.lit("test"))
        )
        .otherwise(pl.lit(None, dtype=pl.Utf8))
        .alias("evaluation_split"),
        pl.col("sample_id")
        .is_in(pl.Series("gold", gold, dtype=pl.Int64))
        .alias("human_verify"),
        pl.Series(
            "n_tokens",
            [annotations.get(int(sample_id), {}).get("n_tokens") for sample_id in df["sample_id"]],
            dtype=pl.Int32,
        ),
        pl.Series(
            "length_bucket",
            [
                annotations.get(int(sample_id), {}).get("length_bucket")
                for sample_id in df["sample_id"]
            ],
            dtype=pl.Utf8,
        ),
        *[
            pl.Series(
                column,
                [annotations.get(int(sample_id), {}).get(column) for sample_id in df["sample_id"]],
                dtype=pl.Boolean,
            )
            for column in (
                "has_math",
                "has_numbers_units",
                "has_acronyms",
                "has_mixed_script",
                "is_rare_term",
            )
        ],
        pl.Series(
            "rare_term_score",
            [
                annotations.get(int(sample_id), {}).get("rare_term_score")
                for sample_id in df["sample_id"]
            ],
            dtype=pl.Float64,
        ),
    )
    report = {
        **policy.as_dict(),
        "imported": imported_count,
        "target": target,
        "reserved": len(reserved),
        "quarantined": len(quarantined),
        "trainable": imported_count - len(reserved) - len(quarantined),
        "selection": selector_report,
    }
    log.info(
        "reserved %s/%s samples for evaluation via %r selector",
        report["reserved"],
        imported_count,
        policy.selector,
    )
    return df, report


def restore_allocation(sample: Sample) -> str:
    """Allocation a sample returns to when un-ignored: reservation is permanent."""
    if sample.quarantined_at:
        return Allocation.QUARANTINED
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
        if allocation == Allocation.TRAINABLE and (sample.reserved_at or sample.quarantined_at):
            # Reservation and contamination quarantine are both permanent.
            sample.allocation = restore_allocation(sample)
            continue
        if allocation == Allocation.RESERVED_EVALUATION and sample.quarantined_at:
            sample.allocation = Allocation.QUARANTINED
            continue
        if allocation == Allocation.RESERVED_EVALUATION and sample.reserved_at is None:
            sample.reserved_at = now
            newly_reserved.append(sample.id)
        if allocation == Allocation.QUARANTINED and sample.quarantined_at is None:
            sample.quarantined_at = now
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
