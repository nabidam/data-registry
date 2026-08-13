"""Reserve evaluation data from an existing logical dataset composition."""

import asyncio
import hashlib
import logging
from collections import Counter
from datetime import UTC, datetime
from functools import partial

import polars as pl
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.allocation import Allocation
from core.config import settings
from models import DatasetDefinition, DatasetReservation, DatasetReservationSample, Sample
from services.dataset_builder.builder import context_for_definition, make_context
from services.dataset_builder.filters import DatasetFilters
from services.evaluation.contamination import scan_snapshots
from services.evaluation.contamination_safe import (
    SelectionResult,
    document_key,
    prepare_contamination_reference,
    scan_full_corpus_contamination,
)
from services.evaluation.reservation_config import load_contamination_safe_config
from services.evaluation.selectors import get_selector, reservation_size

log = logging.getLogger(__name__)
_SCAN_ROWS = 100_000
_UPDATE_ROWS = 10_000


def _target_size(reservation: DatasetReservation, total: int) -> int:
    if reservation.target_count is not None:
        return min(int(reservation.target_count), total)
    return reservation_size(total, reservation.percent, reservation.max_samples)


def _id_hash(sample_ids: list[int]) -> str:
    payload = ",".join(str(sample_id) for sample_id in sorted(sample_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _set_allocations(
    session: AsyncSession,
    reserved_ids: list[int],
    quarantined_ids: set[int],
) -> datetime:
    """Protect the selected rows, returning the instant stamped onto all of them.

    Only rows that are still ``TRAINABLE`` are promoted, so this timestamp marks
    exactly the rows this run changed and nothing a previous run had already
    claimed. ``reservation_revert`` uses it to undo the run.
    """
    now = datetime.now(UTC)
    for start in range(0, len(reserved_ids), _UPDATE_ROWS):
        chunk = reserved_ids[start : start + _UPDATE_ROWS]
        await session.execute(
            update(Sample)
            .where(Sample.id.in_(chunk), Sample.allocation == Allocation.TRAINABLE)
            .values(allocation=Allocation.RESERVED_EVALUATION, reserved_at=now)
        )
    quarantined = sorted(quarantined_ids.difference(reserved_ids))
    for start in range(0, len(quarantined), _UPDATE_ROWS):
        chunk = quarantined[start : start + _UPDATE_ROWS]
        await session.execute(
            update(Sample)
            .where(Sample.id.in_(chunk), Sample.allocation == Allocation.TRAINABLE)
            .values(allocation=Allocation.QUARANTINED, quarantined_at=now)
        )
    return now


async def _record_selected_rows(
    session: AsyncSession,
    reservation: DatasetReservation,
    selection: SelectionResult | list[int],
) -> None:
    if isinstance(selection, SelectionResult):
        reserved_ids = selection.reserved_ids
        dev_ids = set(selection.dev_ids)
        gold_ids = set(selection.gold_ids)
        annotations = selection.annotations
    else:
        reserved_ids = selection
        dev_ids = set()
        gold_ids = set()
        annotations = {}
    values = [
        {
            "reservation_id": reservation.id,
            "sample_id": sample_id,
            "evaluation_split": "dev" if sample_id in dev_ids else "test",
            "human_verify": sample_id in gold_ids,
            "annotations": annotations.get(sample_id),
        }
        for sample_id in reserved_ids
    ]
    for start in range(0, len(values), _UPDATE_ROWS):
        await session.execute(
            insert(DatasetReservationSample), values[start : start + _UPDATE_ROWS]
        )


async def _document_sizes(
    session: AsyncSession,
    definition: DatasetDefinition,
    contamination_scope: str,
) -> tuple[dict[str, int], int]:
    """Chunk count per document over the corpus holdout will be applied to.

    One grouped scan, no embeddings. Documents appearing once are omitted: they
    cost a single row, and keeping millions of such entries in memory to say so
    would be the expensive part of a cheap measurement.
    """
    if contamination_scope == "registry":
        context = await make_context(session, DatasetFilters(), None, Allocation.TRAINABLE)
    else:
        context = await context_for_definition(session, definition, Allocation.TRAINABLE)
    try:
        # Grouped in SQL, keyed in Python: document identity is namespaced by
        # source or batch and split on the configured separator, and that rule
        # lives in one place.
        frame = context.sql(
            "SELECT source_id, batch_id, document_id, count(*) AS rows FROM {rel} "
            "WHERE document_id IS NOT NULL AND document_id <> '' GROUP BY 1, 2, 3"
        ).pl()
        total = int(context.sql("SELECT count(*) FROM {rel}").fetchone()[0])
    finally:
        context.close()
    config = load_contamination_safe_config()
    counted: Counter[str] = Counter()
    for record in frame.iter_rows(named=True):
        counted[document_key(record, config)] += int(record["rows"])
    # A single-chunk document costs one row; holding millions of those in memory
    # to say so would be the expensive part of a cheap measurement.
    sizes = {document: rows for document, rows in counted.items() if rows > 1}
    log.info(
        "Document sizes: %s multi-chunk documents over %s rows (scope=%s)",
        len(sizes),
        total,
        contamination_scope,
    )
    return sizes, total


async def run_dataset_reservation(
    session: AsyncSession,
    reservation: DatasetReservation,
    definition: DatasetDefinition,
) -> DatasetReservation:
    """Select from the composition, then protect selected rows and leakage risks."""
    reservation.status = "running"
    reservation.error = None
    await session.commit()

    composition = None
    scan_context = None
    try:
        composition = await context_for_definition(
            session, definition, Allocation.TRAINABLE
        )
        total = int(composition.sql("SELECT count(*) FROM {rel}").fetchone()[0])
        target = _target_size(reservation, total)
        candidate_limit = min(
            total,
            max(
                target,
                min(
                    settings.evaluation_candidate_limit,
                    target * settings.evaluation_candidate_multiplier,
                ),
            ),
        )
        candidates = composition.sql(
            "SELECT * FROM {rel} ORDER BY "
            f"hash(sample_id::VARCHAR || '-{reservation.seed}') LIMIT {candidate_limit}"
        ).pl()
        # Document holdout removes every chunk of a held document from the corpus
        # the contamination scan covers, not just from the candidate pool. Sizes
        # are therefore measured over that same corpus: without them selection
        # sees a document's rare appearances in a bounded pool and cannot know it
        # is about to quarantine thousands of rows.
        document_sizes, corpus_rows = await _document_sizes(
            session, definition, reservation.contamination_scope
        )
        selection = await asyncio.to_thread(
            partial(
                get_selector(reservation.selector),
                candidates,
                target,
                reservation.seed,
                None,
                document_sizes=document_sizes,
                corpus_rows=corpus_rows,
            )
        )
        reserved_ids = (
            list(selection.reserved_ids)
            if isinstance(selection, SelectionResult)
            else list(selection)
        )
        quarantined_ids = (
            set(selection.quarantined_ids)
            if isinstance(selection, SelectionResult)
            else set()
        )
        semantic_checked = 0
        semantic_prefiltered = 0
        semantic_gated = 0
        scanned_rows = 0

        if (
            reservation.selector == "contamination_safe"
            and isinstance(selection, SelectionResult)
            and reserved_ids
        ):
            config = load_contamination_safe_config()
            selected_rows = candidates.filter(pl.col("sample_id").is_in(reserved_ids))
            reference = await asyncio.to_thread(
                partial(
                    prepare_contamination_reference,
                    selected_rows,
                    config,
                    None,
                    scope=reservation.comparison_scope,
                )
            )
            if reservation.contamination_scope == "registry":
                scan_context = await make_context(
                    session, DatasetFilters(), None, Allocation.TRAINABLE
                )
            else:
                scan_context = await context_for_definition(
                    session, definition, Allocation.TRAINABLE
                )
            reader = scan_context.sql("SELECT * FROM {rel}").fetch_record_batch(_SCAN_ROWS)
            for batch in reader:
                frame = pl.from_arrow(batch)
                result = await asyncio.to_thread(
                    scan_full_corpus_contamination,
                    frame,
                    reference,
                    config,
                    None,
                )
                scanned_rows += frame.height
                semantic_checked += result.semantic_checked_rows
                semantic_prefiltered += result.semantic_prefilter_rows
                semantic_gated += result.semantic_gate_rows
                quarantined_ids.update(result.quarantined_ids)

        reservation.applied_at = await _set_allocations(
            session, reserved_ids, quarantined_ids
        )
        await _record_selected_rows(session, reservation, selection)
        selector_report = selection.report if isinstance(selection, SelectionResult) else {}
        reservation.report = {
            "dataset": {
                "id": definition.id,
                "name": definition.name,
                "batch_ids": definition.batch_ids,
                "batch_rules": definition.batch_rules,
                "composition_seed": definition.composition_seed,
                "filters": definition.filters,
            },
            "eligible_trainable": total,
            "target": target,
            "candidate_rows": candidate_limit,
            "reserved": len(reserved_ids),
            "quarantined": len(quarantined_ids.difference(reserved_ids)),
            "selected_ids_sha256": _id_hash(reserved_ids),
            "contamination_scope": reservation.contamination_scope,
            "comparison_scope": reservation.comparison_scope,
            "contamination_rows_scanned": scanned_rows,
            "semantic_rows_checked": semantic_checked,
            # Rows the cheap shingle gate admitted, before the per-reference-row
            # check. The gap between the two is what that check saves in LaBSE.
            "semantic_rows_prefiltered": semantic_prefiltered,
            "semantic_rows_shingle_gated": semantic_gated,
            "selection": selector_report,
        }
        reservation.status = "ready"
        await session.commit()

        contaminated = await scan_snapshots(
            session,
            reserved_ids,
            reason=f"dataset reservation {reservation.id}",
        )
        reservation.report = {
            **dict(reservation.report or {}),
            "historically_contaminated_snapshots": [
                {"snapshot_id": item.snapshot_id, "sample_count": item.sample_count}
                for item in contaminated
            ],
        }
        await session.commit()
        await session.refresh(reservation)
        return reservation
    except Exception as exc:
        await session.rollback()
        reservation = await session.get(DatasetReservation, reservation.id)
        if reservation is not None:
            reservation.status = "failed"
            reservation.error = str(exc)
            await session.commit()
        log.exception("dataset reservation %s failed", reservation.id if reservation else "unknown")
        raise
    finally:
        if composition is not None:
            composition.close()
        if scan_context is not None:
            scan_context.close()
