"""Snapshot builds: materialize an immutable, reproducible export."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models import DatasetDefinition, DatasetReservation, Snapshot, SplitDefinition
from services.dataset_builder.builder import BuildContext, context_for_definition
from storage import get_storage
from utils.duck import split_bucket_expr

log = logging.getLogger(__name__)

DEFAULT_RATIOS = {"train": 0.9, "validation": 0.05, "test": 0.05}


def _split_predicates(ratios: dict[str, float], seed: int) -> dict[str, str]:
    """Map each split to a disjoint slice of the deterministic hash bucket space."""
    bucket = split_bucket_expr(seed)
    edges: dict[str, str] = {}
    low = 0.0
    for name in ("train", "validation", "test"):
        width = float(ratios.get(name, DEFAULT_RATIOS[name]))
        high = low + width
        last = name == "test"
        edges[name] = f"{bucket} >= {low}" + ("" if last else f" AND {bucket} < {high}")
        low = high
    return edges


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


async def build_snapshot(
    session: AsyncSession,
    snapshot: Snapshot,
    definition: DatasetDefinition,
    split: SplitDefinition | None,
) -> Snapshot:
    storage = get_storage()
    ratios = dict(split.ratios) if split and split.ratios else DEFAULT_RATIOS
    seed = split.seed if split else snapshot.seed
    prefix = f"snapshots/snapshot_{snapshot.id}"
    work = Path(settings.work_dir) / f"snapshot_{snapshot.id}"
    work.mkdir(parents=True, exist_ok=True)

    ctx: BuildContext = await context_for_definition(session, definition)
    reservation_rows = await session.execute(
        select(DatasetReservation)
        .where(
            DatasetReservation.dataset_id == definition.id,
            DatasetReservation.status == "ready",
        )
        .order_by(DatasetReservation.id)
    )
    reservations = reservation_rows.scalars().all()
    files: dict[str, dict] = {}
    counts: dict[str, int] = {}
    try:
        for name, predicate in _split_predicates(ratios, seed).items():
            local = work / f"{name}.parquet"
            ctx.sql(
                f"COPY (SELECT * FROM {{rel}} WHERE {predicate}) "
                f"TO '{local}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            counts[name] = int(
                ctx.sql(f"SELECT count(*) FROM {{rel}} WHERE {predicate}").fetchone()[0]
            )
            uri = storage.put_file(local, f"{prefix}/{name}.parquet")
            files[name] = {"uri": uri, "rows": counts[name], "sha256": _sha256(local)}
            local.unlink(missing_ok=True)

        manifest = {
            "snapshot": {"id": snapshot.id, "name": snapshot.name},
            "dataset_definition": {
                "id": definition.id,
                "name": definition.name,
                "batch_ids": definition.batch_ids,
                "batch_rules": definition.batch_rules,
                "composition_seed": definition.composition_seed,
                "filters": definition.filters,
            },
            "reservations": [
                {
                    "id": reservation.id,
                    "selector": reservation.selector,
                    "seed": reservation.seed,
                    "contamination_scope": reservation.contamination_scope,
                    "report": reservation.report,
                }
                for reservation in reservations
            ],
            "split": {
                "id": split.id if split else None,
                "name": split.name if split else "default",
                "version": split.version if split else 1,
                "ratios": ratios,
                "seed": seed,
            },
            "files": files,
            "counts": counts,
            "total": sum(counts.values()),
            "created_at": datetime.now(UTC).isoformat(),
        }
        manifest_path = work / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
        storage.put_file(manifest_path, f"{prefix}/manifest.json")
        manifest_path.unlink(missing_ok=True)

        snapshot.prefix_uri = storage.uri(prefix)
        snapshot.manifest = manifest
        snapshot.stats = {"counts": counts, "total": manifest["total"]}
        snapshot.seed = seed
        snapshot.status = "ready"
    except Exception as exc:  # surface the failure instead of leaving it "building"
        snapshot.status = "failed"
        snapshot.error = str(exc)
        log.exception("snapshot %s failed", snapshot.id)
    finally:
        ctx.close()

    await session.commit()
    await session.refresh(snapshot)
    return snapshot
