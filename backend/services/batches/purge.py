"""Guarded removal for an erroneous batch that has no downstream artifacts."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import BadRequest, NotFound
from core.locks import lock_allocation_boundary
from models import (
    Annotation,
    Batch,
    BatchPurgeAudit,
    DatasetDefinition,
    DatasetReservation,
    DatasetReservationSample,
    EvaluationSet,
    Sample,
    Snapshot,
)
from schemas import BatchPurgeImpact, BatchPurgeOut
from storage import get_storage
from utils.duck import connect, parquet_source

PURGEABLE_STATUSES = {"failed", "rejected"}
ACTIVE_BATCH_STATUSES = {"uploading", "queued", "importing"}


def _references_batch(definition: DatasetDefinition, batch_id: int) -> bool:
    filtered_ids = {
        int(value) for value in (definition.filters or {}).get("batch_ids", [])
    }
    if filtered_ids:
        return batch_id in filtered_ids
    rules = list(definition.batch_rules or [])
    if rules:
        return any(int(rule["batch_id"]) == batch_id for rule in rules)
    return batch_id in {int(value) for value in definition.batch_ids or []}


def _uses_all_ready_batches(definition: DatasetDefinition) -> bool:
    filtered_ids = (definition.filters or {}).get("batch_ids", [])
    return not definition.batch_rules and not definition.batch_ids and not filtered_ids


def _artifact_contains_batch(uris: list[str], batch_id: int) -> bool:
    con = connect()
    try:
        source = parquet_source(uris)
        return bool(
            con.sql(f"SELECT EXISTS(SELECT 1 FROM {source} WHERE batch_id = {int(batch_id)})")
            .fetchone()[0]
        )
    finally:
        con.close()


async def _artifact_dependencies(
    session: AsyncSession, batch_id: int
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    errors: list[str] = []
    storage = get_storage()

    snapshot_rows = await session.execute(select(Snapshot).where(Snapshot.status == "ready"))
    for snapshot in snapshot_rows.scalars():
        uris = [
            storage.uri(f"snapshots/snapshot_{snapshot.id}/{split}.parquet")
            for split in ("train", "validation", "test")
        ]
        try:
            if await asyncio.to_thread(_artifact_contains_batch, uris, batch_id):
                blockers.append(f"snapshot #{snapshot.id} ({snapshot.name}) contains this batch")
        except Exception as exc:
            errors.append(f"could not inspect snapshot #{snapshot.id}: {exc}")

    set_rows = await session.execute(
        select(EvaluationSet).where(EvaluationSet.parquet_uri.is_not(None))
    )
    for evaluation_set in set_rows.scalars():
        try:
            if await asyncio.to_thread(
                _artifact_contains_batch, [str(evaluation_set.parquet_uri)], batch_id
            ):
                blockers.append(
                    f"evaluation set #{evaluation_set.id} ({evaluation_set.name}) "
                    "contains this batch"
                )
        except Exception as exc:
            errors.append(f"could not inspect evaluation set #{evaluation_set.id}: {exc}")

    return blockers, errors


async def inspect_batch_purge(session: AsyncSession, batch: Batch) -> BatchPurgeImpact:
    blockers: list[str] = []
    warnings: list[str] = []

    if batch.status not in PURGEABLE_STATUSES:
        if batch.status == "ready":
            blockers.append("reject this ready batch before purging it")
        elif batch.status in ACTIVE_BATCH_STATUSES:
            blockers.append(f"the batch is still {batch.status}")
        else:
            blockers.append(f"status {batch.status!r} is not purgeable")

    active_reservations = await session.execute(
        select(DatasetReservation.id).where(
            DatasetReservation.status.in_(["queued", "running"])
        )
    )
    active_reservation_ids = [row[0] for row in active_reservations]
    if active_reservation_ids:
        blockers.append(
            "dataset reservations are active: "
            + ", ".join(f"#{value}" for value in active_reservation_ids)
        )

    building_snapshots = await session.execute(
        select(Snapshot.id).where(Snapshot.status == "building")
    )
    building_snapshot_ids = [row[0] for row in building_snapshots]
    if building_snapshot_ids:
        blockers.append(
            "snapshots are building: "
            + ", ".join(f"#{value}" for value in building_snapshot_ids)
        )

    definition_rows = await session.execute(select(DatasetDefinition))
    all_ready_names: list[str] = []
    for definition in definition_rows.scalars():
        if _references_batch(definition, batch.id):
            blockers.append(
                f"dataset #{definition.id} ({definition.name}) explicitly references this batch"
            )
        elif _uses_all_ready_batches(definition):
            all_ready_names.append(f"#{definition.id} ({definition.name})")
    if all_ready_names:
        warnings.append(
            "all-ready-batches datasets will stop seeing this rejected batch: "
            + ", ".join(all_ready_names)
        )

    reservation_rows = await session.execute(
        select(DatasetReservation.id, DatasetReservation.dataset_id)
        .join(
            DatasetReservationSample,
            DatasetReservationSample.reservation_id == DatasetReservation.id,
        )
        .join(Sample, Sample.id == DatasetReservationSample.sample_id)
        .where(Sample.batch_id == batch.id)
        .distinct()
    )
    for reservation_id, dataset_id in reservation_rows:
        blockers.append(
            f"dataset reservation #{reservation_id} for dataset #{dataset_id} selected this batch"
        )

    artifact_blockers, inspection_errors = await _artifact_dependencies(session, batch.id)
    blockers.extend(artifact_blockers)
    blockers.extend(inspection_errors)

    annotation_count = await session.scalar(
        select(func.count())
        .select_from(Annotation)
        .join(Sample, Sample.id == Annotation.sample_id)
        .where(Sample.batch_id == batch.id)
    )
    if annotation_count:
        warnings.append(f"{annotation_count:,} sample annotations will be deleted")

    return BatchPurgeImpact(
        batch_id=batch.id,
        batch_name=batch.name,
        status=batch.status,
        sample_count=batch.sample_count,
        can_purge=not blockers,
        blockers=blockers,
        warnings=warnings,
        storage_prefixes=[f"raw/batch_{batch.id}/", f"batches/batch_{batch.id}/"],
    )


def _confirm_batch_name(batch: Batch, confirm_name: str) -> None:
    if confirm_name != batch.name:
        raise BadRequest("confirmation must match the batch name exactly")


async def _ensure_lifecycle_idle(session: AsyncSession) -> None:
    active_reservation = await session.scalar(
        select(DatasetReservation.id).where(
            DatasetReservation.status.in_(["queued", "running"])
        )
    )
    if active_reservation is not None:
        raise BadRequest(
            f"dataset reservation #{active_reservation} is active; wait before changing batches"
        )
    building_snapshot = await session.scalar(
        select(Snapshot.id).where(Snapshot.status == "building")
    )
    if building_snapshot is not None:
        raise BadRequest(
            f"snapshot #{building_snapshot} is building; wait before changing batches"
        )


async def reject_batch(
    session: AsyncSession, batch_id: int, *, confirm_name: str, reason: str
) -> Batch:
    await lock_allocation_boundary(session)
    await _ensure_lifecycle_idle(session)
    batch = await session.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if batch is None:
        raise NotFound("batch", batch_id)
    _confirm_batch_name(batch, confirm_name)
    if batch.status != "ready":
        raise BadRequest("only a ready batch can be rejected")
    stats = dict(batch.stats or {})
    stats["rejection"] = {
        "reason": reason,
        "rejected_at": datetime.now(UTC).isoformat(),
        "previous_status": batch.status,
    }
    batch.stats = stats
    batch.status = "rejected"
    await session.commit()
    await session.refresh(batch)
    return batch


async def restore_batch(
    session: AsyncSession, batch_id: int, *, confirm_name: str
) -> Batch:
    await lock_allocation_boundary(session)
    await _ensure_lifecycle_idle(session)
    batch = await session.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if batch is None:
        raise NotFound("batch", batch_id)
    _confirm_batch_name(batch, confirm_name)
    if batch.status != "rejected":
        raise BadRequest("only a rejected batch can be restored")
    if not batch.parquet_uri:
        raise BadRequest("the rejected batch has no published Parquet data")
    stats = dict(batch.stats or {})
    rejection = dict(stats.get("rejection") or {})
    rejection["restored_at"] = datetime.now(UTC).isoformat()
    stats["rejection"] = rejection
    batch.stats = stats
    batch.status = "ready"
    await session.commit()
    await session.refresh(batch)
    return batch


async def purge_batch(
    session: AsyncSession,
    batch_id: int,
    *,
    confirm_name: str,
    reason: str,
) -> BatchPurgeOut:
    await lock_allocation_boundary(session)
    batch = await session.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if batch is None:
        raise NotFound("batch", batch_id)
    _confirm_batch_name(batch, confirm_name)
    impact = await inspect_batch_purge(session, batch)
    if not impact.can_purge:
        raise BadRequest("batch cannot be purged: " + "; ".join(impact.blockers))

    storage = get_storage()
    deleted_objects = 0
    for prefix in impact.storage_prefixes:
        deleted_objects += await asyncio.to_thread(storage.delete_prefix, prefix)

    sample_ids = select(Sample.id).where(Sample.batch_id == batch.id)
    await session.execute(
        delete(DatasetReservationSample).where(
            DatasetReservationSample.sample_id.in_(sample_ids)
        )
    )
    await session.execute(delete(Annotation).where(Annotation.sample_id.in_(sample_ids)))
    deleted_samples = (
        await session.execute(delete(Sample).where(Sample.batch_id == batch.id))
    ).rowcount
    audit = BatchPurgeAudit(
        batch_id=batch.id,
        batch_name=batch.name,
        reason=reason,
    )
    session.add(audit)
    await session.delete(batch)
    await session.commit()
    await session.refresh(audit)
    return BatchPurgeOut(
        batch_id=batch_id,
        batch_name=confirm_name,
        audit_id=audit.id,
        deleted_samples=int(deleted_samples or 0),
        deleted_objects=deleted_objects,
    )
