"""Historical contamination.

Snapshots are immutable and history is never rewritten. If a sample is reserved
for evaluation *after* an existing snapshot already trained on it, we record the
overlap instead: that evaluation sample is historically contaminated for those
earlier snapshots, and downstream benchmarking can account for it.

Reservation during import can never contaminate anything — the samples did not
exist when earlier snapshots were built — so this only runs when an existing
sample's allocation changes.
"""

import logging

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Snapshot, SnapshotContamination
from utils.duck import connect, parquet_source

log = logging.getLogger(__name__)

MAX_RECORDED_IDS = 10_000  # keep the JSONB row bounded; the count is always exact


def _snapshot_uris(snapshot: Snapshot) -> list[str]:
    files = (snapshot.manifest or {}).get("files", {})
    return [f["uri"] for f in files.values() if f.get("uri")]


async def scan_snapshots(
    session: AsyncSession, sample_ids: list[int], *, reason: str
) -> list[SnapshotContamination]:
    """Record, for every ready snapshot, which of ``sample_ids`` it already contains."""
    if not sample_ids:
        return []

    rows = await session.execute(select(Snapshot).where(Snapshot.status == "ready"))
    ids = pl.DataFrame({"sample_id": pl.Series(sample_ids, dtype=pl.Int64)})
    found: list[SnapshotContamination] = []

    for snapshot in rows.scalars().all():
        uris = _snapshot_uris(snapshot)
        if not uris:
            continue
        con = connect()
        try:
            con.register("reserved", ids)
            hit = (
                con.sql(
                    f"SELECT s.sample_id FROM {parquet_source(uris)} s "
                    "SEMI JOIN reserved r ON r.sample_id = s.sample_id"
                )
                .pl()["sample_id"]
                .to_list()
            )
        finally:
            con.close()
        if not hit:
            continue

        record = SnapshotContamination(
            snapshot_id=snapshot.id,
            sample_count=len(hit),
            sample_ids=hit[:MAX_RECORDED_IDS],
            reason=reason,
        )
        session.add(record)
        found.append(record)
        log.warning(
            "snapshot %s historically contaminated: %s reserved samples already exported",
            snapshot.id,
            len(hit),
        )

    if found:
        await session.commit()
    return found
