import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.errors import BadRequest
from db.session import get_session
from schemas import BatchOut, ColumnMapping
from services.ingestion.readers import SUPPORTED_FORMATS, detect_format, read_any
from services.ingestion.service import ingest_file

router = APIRouter(prefix="/imports", tags=["imports"])


def _stage_upload(file: UploadFile) -> Path:
    work = Path(settings.work_dir) / "uploads"
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"{uuid4().hex}_{file.filename}"
    with path.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    return path


@router.get("/formats")
async def formats():
    return {"formats": SUPPORTED_FORMATS}


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
    format: str | None = Form(None),
    notes: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
):
    """Import a raw file as one immutable ingestion batch."""
    path = _stage_upload(file)
    mapping = ColumnMapping(
        source_text=source_column,
        target_text=target_column,
        src_lang=src_lang_column,
        tgt_lang=tgt_lang_column,
        domain=domain_column,
        quality=quality_column,
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
        )
    except Exception as exc:
        await session.rollback()
        raise BadRequest(str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)
