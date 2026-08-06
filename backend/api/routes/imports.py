import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.errors import BadRequest, NotFound
from db.session import get_session
from models import Batch
from schemas import BatchOut
from services.evaluation.reservation import ReservationPolicy
from services.evaluation.reservation_config import load_contamination_safe_config
from services.evaluation.selectors import SELECTORS
from services.ingestion.readers import SUPPORTED_FORMATS, detect_format, read_any
from services.ingestion.service import batch_keys
from storage import get_storage

router = APIRouter(prefix="/imports", tags=["imports"])


class ImportStartIn(BaseModel):
    batch_name: str
    filename: str
    size: int = Field(gt=0)


class MultipartPart(BaseModel):
    part_number: int = Field(ge=1, le=10_000)
    etag: str


class ImportCompleteIn(BaseModel):
    parts: list[MultipartPart] = Field(min_length=1)
    src_lang: str
    tgt_lang: str
    source_id: int | None = None
    domain: str | None = None
    source_column: str = "source"
    target_column: str = "target"
    src_lang_column: str | None = None
    tgt_lang_column: str | None = None
    domain_column: str | None = None
    quality_column: str | None = None
    document_id_column: str | None = None
    format: str | None = None
    notes: str | None = None
    evaluation_percent: float | None = None
    evaluation_max_samples: int | None = None
    evaluation_selector: str | None = None
    random_seed: int | None = None


class ImportUploadOut(BaseModel):
    batch: BatchOut
    upload_id: str
    part_size: int


def _stage_upload(file: UploadFile) -> Path:
    work = Path(settings.work_dir) / "uploads"
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"{uuid4().hex}_{file.filename}"
    with path.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    return path


def _discard_incomplete_line(path: Path) -> None:
    """Keep a bounded CSV/JSONL preview parseable when the browser sends a head slice."""
    data = path.read_bytes()
    last_newline = data.rfind(b"\n")
    if last_newline > 0:
        path.write_bytes(data[:last_newline])


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    if not name or name in {".", ".."}:
        raise BadRequest("a filename is required")
    return name


def _upload_details(batch: Batch) -> dict:
    details = (batch.stats or {}).get("upload")
    if not details or not isinstance(details.get("id"), str):
        raise BadRequest("this import has no active upload")
    return details


@router.get("/formats")
async def formats():
    return {"formats": SUPPORTED_FORMATS}


@router.get("/reservation-defaults")
async def reservation_defaults():
    """Reservation settings the UI pre-fills, plus the selectors it may choose."""
    return {
        **ReservationPolicy.resolve().as_dict(),
        "selectors": sorted(SELECTORS),
        "policies": {"contamination_safe": load_contamination_safe_config()},
    }


@router.post("/start", response_model=ImportUploadOut, status_code=201)
async def start_import(payload: ImportStartIn, session: AsyncSession = Depends(get_session)):
    """Create a batch and a resumable MinIO upload before any file bytes move."""
    if settings.storage_backend.lower() == "local":
        raise BadRequest("large imports require S3-compatible object storage")

    filename = _safe_filename(payload.filename)
    part_size = settings.import_upload_part_size_mb * 1024 * 1024
    if part_size < 5 * 1024 * 1024:
        raise BadRequest("IMPORT_UPLOAD_PART_SIZE_MB must be at least 5 for S3 multipart uploads")
    batch = Batch(name=payload.batch_name, status="uploading", format=detect_format(filename))
    session.add(batch)
    await session.flush()
    raw_key, _ = batch_keys(batch.id, filename)
    try:
        storage = await asyncio.to_thread(get_storage)
        upload_id = await asyncio.to_thread(storage.create_multipart_upload, raw_key)
    except Exception as exc:
        await session.rollback()
        raise BadRequest(f"could not start object-storage upload: {exc}") from exc

    batch.raw_uri = storage.uri(raw_key)
    batch.stats = {"upload": {"id": upload_id, "part_size": part_size, "bytes": payload.size}}
    await session.commit()
    await session.refresh(batch)
    return ImportUploadOut(batch=batch, upload_id=upload_id, part_size=part_size)


@router.post("/{batch_id}/parts/{part_number}")
async def upload_part(
    batch_id: int,
    part_number: int,
    upload_id: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Pass one bounded browser chunk straight through to MinIO."""
    if not 1 <= part_number <= 10_000:
        raise BadRequest("invalid multipart part number")
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise NotFound("batch", batch_id)
    upload = _upload_details(batch)
    if batch.status != "uploading" or upload_id != upload["id"]:
        raise BadRequest("upload is no longer active")
    if file.size is not None and file.size > upload["part_size"]:
        raise BadRequest("upload part exceeds IMPORT_UPLOAD_PART_SIZE_MB")
    filename = _safe_filename(Path(batch.raw_uri or "").name)
    raw_key, _ = batch_keys(batch.id, filename)
    try:
        storage = await asyncio.to_thread(get_storage)
        etag = await asyncio.to_thread(
            storage.upload_multipart_part,
            raw_key,
            upload_id,
            part_number,
            file.file,
        )
    except Exception as exc:
        raise BadRequest(f"could not store upload part: {exc}") from exc
    return {"part_number": part_number, "etag": etag}


@router.post("/{batch_id}/complete", response_model=BatchOut, status_code=202)
async def complete_import(
    batch_id: int,
    payload: ImportCompleteIn,
    session: AsyncSession = Depends(get_session),
):
    """Finalize object storage and enqueue normalization outside the API process."""
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise NotFound("batch", batch_id)
    if batch.status != "uploading":
        raise BadRequest("upload is no longer active")
    upload = _upload_details(batch)
    filename = _safe_filename(Path(batch.raw_uri or "").name)
    raw_key, _ = batch_keys(batch.id, filename)
    parts = sorted(
        (part.model_dump() for part in payload.parts), key=lambda part: part["part_number"]
    )
    # boto accepts S3's TitleCase field names. Keep the browser/API model
    # readable and translate it at this boundary.
    object_parts = [{"PartNumber": part["part_number"], "ETag": part["etag"]} for part in parts]
    if len({part["PartNumber"] for part in object_parts}) != len(object_parts):
        raise BadRequest("multipart part numbers must be unique")
    try:
        storage = await asyncio.to_thread(get_storage)
        await asyncio.to_thread(
            storage.complete_multipart_upload,
            raw_key,
            upload["id"],
            object_parts,
        )
    except Exception as exc:
        raise BadRequest(f"could not complete object-storage upload: {exc}") from exc

    batch.source_id = payload.source_id
    batch.format = (payload.format or detect_format(filename)).lower()
    batch.notes = payload.notes
    # Keep the complete job specification in Postgres. The worker can resume a
    # queued job after either service is restarted, without asking the browser
    # to upload the object again.
    now = datetime.now(UTC).isoformat()
    batch.status = "queued"
    batch.stats = {
        "job": payload.model_dump(exclude={"parts"}),
        "queued_at": now,
        "attempt": 0,
        "progress": {
            "phase": "queued",
            "message": "Upload complete; waiting for the import worker",
            "rows_processed": 0,
            "shards_processed": 0,
            "updated_at": now,
        },
    }
    await session.commit()
    await session.refresh(batch)
    return batch


@router.post("/{batch_id}/retry", response_model=BatchOut, status_code=202)
async def retry_import(batch_id: int, session: AsyncSession = Depends(get_session)):
    """Requeue a failed import without uploading its immutable raw object again."""
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise NotFound("batch", batch_id)
    stats = dict(batch.stats or {})
    if batch.status != "failed":
        raise BadRequest(f"only failed imports can be retried; batch is {batch.status}")
    if not batch.raw_uri or not isinstance(stats.get("job"), dict):
        raise BadRequest("failed import has no resumable raw object and job specification")

    now = datetime.now(UTC).isoformat()
    previous_error = stats.pop("error", None)
    batch.status = "queued"
    batch.stats = {
        **stats,
        "queued_at": now,
        "previous_error": previous_error,
        "progress": {
            "phase": "queued",
            "message": "Retry requested; waiting for the import worker",
            "rows_processed": 0,
            "shards_processed": 0,
            "updated_at": now,
        },
    }
    await session.commit()
    await session.refresh(batch)
    return batch


@router.post("/inspect")
async def inspect(file: UploadFile = File(...), partial: bool = Form(False)):
    """Peek at a file's columns so the UI can offer a column mapping."""
    path = await asyncio.to_thread(_stage_upload, file)
    try:
        fmt = detect_format(file.filename or "")
        if partial and fmt in {"csv", "tsv", "jsonl"}:
            await asyncio.to_thread(_discard_incomplete_line, path)
        df = await asyncio.to_thread(lambda: read_any(path, fmt).head(5))
        return {"format": fmt, "columns": df.columns, "preview": df.to_dicts()}
    except Exception as exc:
        raise BadRequest(str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


@router.post("", response_model=BatchOut, status_code=202)
async def create_import(
    file: UploadFile = File(...),
    batch_name: str = Form(...),
    src_lang: str = Form(...),
    tgt_lang: str = Form(...),
    source_id: int | None = Form(None),
    domain: str | None = Form(None),
    source_column: str = Form("source"),
    target_column: str = Form("target"),
    src_lang_column: str | None = Form(None),
    tgt_lang_column: str | None = Form(None),
    domain_column: str | None = Form(None),
    quality_column: str | None = Form(None),
    document_id_column: str | None = Form(None),
    format: str | None = Form(None),
    notes: str | None = Form(None),
    evaluation_percent: float | None = Form(None),
    evaluation_max_samples: int | None = Form(None),
    evaluation_selector: str | None = Form(None),
    random_seed: int | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    """Stage a conventional multipart upload and queue it for the worker."""
    if file.size and file.size >= settings.import_stream_threshold_mb * 1024 * 1024:
        raise BadRequest("large imports must use the resumable /imports/start upload flow")
    path = await asyncio.to_thread(_stage_upload, file)
    try:
        filename = _safe_filename(file.filename or "upload.dat")
        resolved_format = (format or detect_format(filename)).lower()
        batch = Batch(
            name=batch_name,
            source_id=source_id,
            status="uploading",
            format=resolved_format,
            notes=notes,
        )
        session.add(batch)
        await session.flush()
        raw_key, _ = batch_keys(batch.id, filename)
        storage = await asyncio.to_thread(get_storage)
        batch.raw_uri = await asyncio.to_thread(storage.put_file, path, raw_key)
        now = datetime.now(UTC).isoformat()
        batch.status = "queued"
        batch.stats = {
            "job": {
                "src_lang": src_lang,
                "tgt_lang": tgt_lang,
                "source_id": source_id,
                "domain": domain,
                "source_column": source_column,
                "target_column": target_column,
                "src_lang_column": src_lang_column,
                "tgt_lang_column": tgt_lang_column,
                "domain_column": domain_column,
                "quality_column": quality_column,
                "document_id_column": document_id_column,
                "format": resolved_format,
                "notes": notes,
                "evaluation_percent": evaluation_percent,
                "evaluation_max_samples": evaluation_max_samples,
                "evaluation_selector": evaluation_selector,
                "random_seed": random_seed,
            },
            "queued_at": now,
            "attempt": 0,
            "progress": {
                "phase": "queued",
                "message": "Upload complete; waiting for the import worker",
                "rows_processed": 0,
                "shards_processed": 0,
                "updated_at": now,
            },
        }
        await session.commit()
        await session.refresh(batch)
        return batch
    except Exception as exc:
        await session.rollback()
        raise BadRequest(str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)
