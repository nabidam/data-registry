import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

import duckdb
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.errors import BadRequest, NotFound
from db.session import get_session
from models import EvaluationSet, Snapshot
from storage import get_storage

router = APIRouter(prefix="/exports", tags=["exports"])

SPLITS = {"train", "validation", "test", "manifest"}
DATA_SPLITS = ("train", "validation", "test")
EXPORT_FORMATS = {"parquet", "csv", "tsv", "jsonl", "huggingface"}
# The normalized schema is deliberately fixed. Keeping this list explicit
# prevents a custom export from ever becoming arbitrary SQL input.
EXPORT_COLUMNS = (
    "sample_id",
    "batch_id",
    "source_id",
    "src_lang",
    "tgt_lang",
    "domain",
    "quality",
    "document_id",
    "source_text",
    "target_text",
    "meta",
)
EVALUATION_EXPORT_COLUMNS = EXPORT_COLUMNS + (
    "evaluation_split",
    "human_verify",
    "n_tokens",
    "length_bucket",
    "has_math",
    "has_numbers_units",
    "has_acronyms",
    "has_mixed_script",
    "is_rare_term",
    "rare_term_score",
)


def _selected(value: str, allowed: tuple[str, ...], label: str) -> list[str]:
    selected = [item.strip() for item in value.split(",") if item.strip()]
    if not selected:
        raise BadRequest(f"choose at least one {label}")
    invalid = sorted(set(selected) - set(allowed))
    if invalid:
        raise BadRequest(f"unknown {label}: {', '.join(invalid)}")
    # Preserve the stable schema/split order rather than query-string order.
    return [item for item in allowed if item in selected]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "snapshot"


def _copy_as(
    con: duckdb.DuckDBPyConnection,
    source: Path,
    target: Path,
    columns: list[str],
    fmt: str,
) -> None:
    # Column names originate from EXPORT_COLUMNS above, not request SQL.
    projection = ", ".join(columns)
    escaped_source = str(source).replace("'", "''")
    escaped_target = str(target).replace("'", "''")
    options = {
        "parquet": "FORMAT PARQUET, COMPRESSION ZSTD",
        "csv": "FORMAT CSV, HEADER true",
        "tsv": "FORMAT CSV, HEADER true, DELIMITER '\\t'",
        # ARRAY false produces one JSON object per line, i.e. JSONL/NDJSON.
        "jsonl": "FORMAT JSON, ARRAY false",
    }[fmt]
    con.execute(
        f"COPY (SELECT {projection} FROM read_parquet('{escaped_source}')) "
        f"TO '{escaped_target}' ({options})"
    )


def _write_dataset_card(path: Path, snapshot: Snapshot) -> None:
    """Create the small repository contract expected by `datasets.load_dataset`.

    A downloaded archive can be extracted and loaded with
    ``load_dataset('/path/to/archive')``. The Parquet files remain the exact
    immutable split data; only their repository-style paths are new.
    """
    path.write_text(
        "---\n"
        "configs:\n"
        "- config_name: default\n"
        "  data_files:\n"
        "  - split: train\n"
        "    path: data/train-*.parquet\n"
        "  - split: validation\n"
        "    path: data/validation-*.parquet\n"
        "  - split: test\n"
        "    path: data/test-*.parquet\n"
        "---\n\n"
        f"# {snapshot.name}\n\n"
        "An immutable Machine Translation Dataset Registry snapshot packaged "
        "for the Hugging Face `datasets` library.\n\n"
        "```python\n"
        "from datasets import load_dataset\n"
        "dataset = load_dataset('path/to/extracted-snapshot')\n"
        "```\n"
    )


def _write_evaluation_dataset_card(path: Path, evaluation_set: EvaluationSet) -> None:
    path.write_text(
        "---\n"
        "configs:\n"
        "- config_name: default\n"
        "  data_files:\n"
        "  - split: evaluation\n"
        "    path: data/evaluation-*.parquet\n"
        "---\n\n"
        f"# {evaluation_set.name}\n\n"
        "An immutable Machine Translation Dataset Registry evaluation set packaged "
        "for the Hugging Face `datasets` library.\n\n"
        "```python\n"
        "from datasets import load_dataset\n"
        "dataset = load_dataset('path/to/extracted-evaluation-set')\n"
        "```\n"
    )


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


@router.get("/evaluation-sets/{set_id}/custom")
async def custom_evaluation_set_download(
    set_id: int,
    background: BackgroundTasks,
    format: str = "parquet",
    columns: str = ",".join(EVALUATION_EXPORT_COLUMNS),
    include_metadata: bool = True,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Build a non-persistent custom export from an immutable evaluation set."""
    if format not in EXPORT_FORMATS:
        raise BadRequest(f"unknown format {format!r}, expected one of {sorted(EXPORT_FORMATS)}")
    evaluation_set = await session.get(EvaluationSet, set_id)
    if evaluation_set is None or not evaluation_set.parquet_uri:
        raise NotFound("evaluation set", set_id)

    selected_columns = _selected(columns, EVALUATION_EXPORT_COLUMNS, "columns")
    storage = get_storage()
    work = Path(settings.work_dir) / "downloads" / f"evaluation_custom_{uuid4().hex}"
    package = work / _safe_name(evaluation_set.name)
    package.mkdir(parents=True)
    source = work / "data.parquet"
    storage.get_file(storage.key_of(evaluation_set.parquet_uri), source)
    con = duckdb.connect()

    try:
        if format == "huggingface":
            data_dir = package / "data"
            data_dir.mkdir()
            target = data_dir / "evaluation-00000-of-00001.parquet"
            _copy_as(con, source, target, selected_columns, "parquet")
            _write_evaluation_dataset_card(package / "README.md", evaluation_set)
            include_metadata = True
        else:
            extension = {"parquet": "parquet", "csv": "csv", "tsv": "tsv", "jsonl": "jsonl"}[format]
            _copy_as(con, source, package / f"data.{extension}", selected_columns, format)

        (package / "export.json").write_text(
            json.dumps(
                {
                    "source_evaluation_set": {
                        "id": evaluation_set.id,
                        "name": evaluation_set.name,
                    },
                    "format": format,
                    "columns": selected_columns,
                    "includes_evaluation_set_metadata": include_metadata,
                },
                indent=2,
            )
        )
        if include_metadata:
            (package / "evaluation_set.json").write_text(
                json.dumps(
                    {
                        "id": evaluation_set.id,
                        "name": evaluation_set.name,
                        "description": evaluation_set.description,
                        "kind": evaluation_set.kind,
                        "sample_count": evaluation_set.sample_count,
                        "spec": evaluation_set.spec,
                        "created_at": evaluation_set.created_at.isoformat(),
                    },
                    indent=2,
                )
            )

        archive_base = work / _safe_name(evaluation_set.name)
        archive = Path(shutil.make_archive(str(archive_base), "zip", work, package.name))
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        con.close()

    background.add_task(shutil.rmtree, work, ignore_errors=True)
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"{_safe_name(evaluation_set.name)}-{format}.zip",
        background=background,
    )


@router.get("/evaluation-sets/{set_id}/data")
async def download_evaluation_set(
    set_id: int,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Stream the original evaluation-set Parquet file."""
    evaluation_set = await session.get(EvaluationSet, set_id)
    if evaluation_set is None or not evaluation_set.parquet_uri:
        raise NotFound("evaluation set", set_id)

    storage = get_storage()
    work = Path(settings.work_dir) / "downloads" / f"evaluation_{uuid4().hex}"
    local = work / "data.parquet"
    storage.get_file(storage.key_of(evaluation_set.parquet_uri), local)
    background.add_task(shutil.rmtree, work, ignore_errors=True)
    return FileResponse(
        local,
        filename=f"{_safe_name(evaluation_set.name)}_data.parquet",
        background=background,
    )


@router.get("/{snapshot_id}/custom")
async def custom_download(
    snapshot_id: int,
    background: BackgroundTasks,
    format: str = "parquet",
    columns: str = ",".join(EXPORT_COLUMNS),
    splits: str = ",".join(DATA_SPLITS),
    include_manifest: bool = True,
    session: AsyncSession = Depends(get_session),
):
    """Build an on-demand, non-persistent export from an immutable snapshot.

    Export settings never change the stored snapshot. The response is a ZIP so
    split names stay intact when researchers select multiple splits.
    """
    if format not in EXPORT_FORMATS:
        raise BadRequest(f"unknown format {format!r}, expected one of {sorted(EXPORT_FORMATS)}")
    snapshot = await session.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.status != "ready":
        raise NotFound("snapshot", snapshot_id)

    selected_columns = _selected(columns, EXPORT_COLUMNS, "columns")
    selected_splits = _selected(splits, DATA_SPLITS, "splits")
    if format == "huggingface":
        # A DatasetDict is useful only when it carries the complete snapshot.
        selected_splits = list(DATA_SPLITS)
        include_manifest = True

    work = Path(settings.work_dir) / "downloads" / f"custom_{uuid4().hex}"
    package = work / _safe_name(snapshot.name)
    package.mkdir(parents=True)
    storage = get_storage()
    con = duckdb.connect()
    extensions = {"parquet": "parquet", "csv": "csv", "tsv": "tsv", "jsonl": "jsonl"}

    try:
        if format == "huggingface":
            data_dir = package / "data"
            data_dir.mkdir()
            for split in selected_splits:
                source = work / f"{split}.parquet"
                storage.get_file(f"snapshots/snapshot_{snapshot_id}/{split}.parquet", source)
                # Keep the original Parquet byte-for-byte when all canonical
                # columns are requested. Otherwise create a projected shard.
                target = data_dir / f"{split}-00000-of-00001.parquet"
                if selected_columns == list(EXPORT_COLUMNS):
                    shutil.copyfile(source, target)
                else:
                    _copy_as(con, source, target, selected_columns, "parquet")
            _write_dataset_card(package / "README.md", snapshot)
        else:
            extension = extensions[format]
            for split in selected_splits:
                source = work / f"{split}.parquet"
                storage.get_file(f"snapshots/snapshot_{snapshot_id}/{split}.parquet", source)
                _copy_as(con, source, package / f"{split}.{extension}", selected_columns, format)

        # Record exactly how this derived download was requested. This is
        # separate from manifest.json, which describes the original snapshot.
        (package / "export.json").write_text(
            json.dumps(
                {
                    "source_snapshot": {"id": snapshot.id, "name": snapshot.name},
                    "format": format,
                    "columns": selected_columns,
                    "splits": selected_splits,
                    "includes_original_manifest": include_manifest,
                },
                indent=2,
            )
        )
        if include_manifest:
            (package / "manifest.json").write_text(json.dumps(snapshot.manifest or {}, indent=2))

        archive_base = work / _safe_name(snapshot.name)
        archive = Path(shutil.make_archive(str(archive_base), "zip", work, package.name))
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        con.close()

    background.add_task(shutil.rmtree, work, ignore_errors=True)
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"{_safe_name(snapshot.name)}-{format}.zip",
        background=background,
    )


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
