import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.errors import BadRequest, NotFound
from db.session import SessionLocal, get_session
from models import Batch
from schemas import BatchOut, ColumnMapping
from services.evaluation.reservation import ReservationPolicy
from services.evaluation.reservation_config import load_contamination_safe_config
from services.evaluation.selectors import SELECTORS
from services.ingestion.readers import SUPPORTED_FORMATS, detect_format, read_any
from services.ingestion.service import batch_keys, ingest_file, ingest_stored_file
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


async def _process_uploaded_batch(batch_id: int, filename: str, payload: ImportCompleteIn) -> None:
    """Run after the 202 response, with its own DB session and local work file."""
    work = Path(settings.work_dir) / "uploads" / f"batch_{batch_id}_{uuid4().hex}_{filename}"
    try:
        async with SessionLocal() as session:
            batch = await session.get(Batch, batch_id)
            if batch is None:
                return
            raw_key, _ = batch_keys(batch.id, filename)
            get_storage().get_file(raw_key, work)
            mapping = ColumnMapping(
                source_text=payload.source_column,
                target_text=payload.target_column,
                src_lang=payload.src_lang_column,
                tgt_lang=payload.tgt_lang_column,
                domain=payload.domain_column,
                quality=payload.quality_column,
                document_id=payload.document_id_column,
            )
            await ingest_stored_file(
                session,
                batch=batch,
                local_file=work,
                filename=filename,
                src_lang=payload.src_lang,
                tgt_lang=payload.tgt_lang,
                domain=payload.domain,
                mapping=mapping,
                fmt=payload.format,
                policy=ReservationPolicy.resolve(
                    percent=payload.evaluation_percent,
                    max_samples=payload.evaluation_max_samples,
                    selector=payload.evaluation_selector,
                    seed=payload.random_seed,
                ),
            )
    except Exception as exc:
        # A background failure must be visible in the batch list; never leave an
        # import looking as though it is still running forever.
        async with SessionLocal() as session:
            batch = await session.get(Batch, batch_id)
            if batch is not None:
                batch.status = "failed"
                batch.stats = {"error": str(exc)}
                await session.commit()
        raise
    finally:
        work.unlink(missing_ok=True)


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
        upload_id = get_storage().create_multipart_upload(raw_key)
    except Exception as exc:
        await session.rollback()
        raise BadRequest(f"could not start object-storage upload: {exc}") from exc

    batch.raw_uri = get_storage().uri(raw_key)
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
        etag = get_storage().upload_multipart_part(raw_key, upload_id, part_number, file.file)
    except Exception as exc:
        raise BadRequest(f"could not store upload part: {exc}") from exc
    return {"part_number": part_number, "etag": etag}


@router.post("/{batch_id}/complete", response_model=BatchOut, status_code=202)
async def complete_import(
    batch_id: int,
    payload: ImportCompleteIn,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Finalize object storage, then normalize outside the browser request."""
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
        get_storage().complete_multipart_upload(raw_key, upload["id"], object_parts)
    except Exception as exc:
        raise BadRequest(f"could not complete object-storage upload: {exc}") from exc

    batch.source_id = payload.source_id
    batch.format = (payload.format or detect_format(filename)).lower()
    batch.notes = payload.notes
    batch.status = "importing"
    batch.stats = None
    await session.commit()
    await session.refresh(batch)
    background_tasks.add_task(_process_uploaded_batch, batch.id, filename, payload)
    return batch


@router.post("/inspect")
async def inspect(file: UploadFile = File(...)):
    """Peek at a file's columns so the UI can offer a column mapping."""
    path = _stage_upload(file)
    try:
        fmt = detect_format(file.filename or "")
        df = read_any(path, fmt).head(5)
        return {"format": fmt, "columns": df.columns, "preview": df.to_dicts()}
    except Exception as exc:
        raise BadRequest(str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


@router.post("", response_model=BatchOut, status_code=201)
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
    """Import a raw file as one immutable ingestion batch.

    Part of the same transaction: the evaluation-selection pipeline reserves a
    slice of the batch before any of it becomes trainable. The reservation
    settings default to the application config and can be overridden per import.
    """
    path = _stage_upload(file)
    mapping = ColumnMapping(
        source_text=source_column,
        target_text=target_column,
        src_lang=src_lang_column,
        tgt_lang=tgt_lang_column,
        domain=domain_column,
        quality=quality_column,
        document_id=document_id_column,
    )
    try:
        return await ingest_file(
            session,
            local_file=path,
            filename=file.filename or "upload.dat",
            batch_name=batch_name,
            source_id=source_id,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            domain=domain,
            mapping=mapping,
            fmt=format,
            notes=notes,
            policy=ReservationPolicy.resolve(
                percent=evaluation_percent,
                max_samples=evaluation_max_samples,
                selector=evaluation_selector,
                seed=random_seed,
            ),
        )
    except Exception as exc:
        await session.rollback()
        raise BadRequest(str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)
