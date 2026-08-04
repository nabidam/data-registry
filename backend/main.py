from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import api_router
from core.config import settings
from core.logging import setup_logging

setup_logging(settings.debug)
Path(settings.work_dir).mkdir(parents=True, exist_ok=True)

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/settings")
async def app_settings():
    """Non-secret configuration, shown on the Settings page."""
    return {
        "app_name": settings.app_name,
        "storage_backend": settings.storage_backend,
        "bucket": settings.s3_bucket if settings.storage_backend != "local" else None,
        "storage_root": settings.storage_root if settings.storage_backend == "local" else None,
        "s3_endpoint_url": settings.s3_endpoint_url,
        "mlflow_tracking_uri": settings.mlflow_tracking_uri,
        "work_dir": settings.work_dir,
    }
