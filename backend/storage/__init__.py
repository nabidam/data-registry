from functools import lru_cache

from core.config import settings
from storage.base import Storage
from storage.local import LocalStorage
from storage.s3 import S3Storage


@lru_cache
def get_storage() -> Storage:
    backend = settings.storage_backend.lower()
    if backend == "local":
        return LocalStorage(settings.storage_root)
    if backend == "s3":
        return S3Storage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            use_ssl=settings.s3_use_ssl,
        )
    if backend == "gcs":
        # GCS via its S3 interoperability endpoint (HMAC keys).
        return S3Storage(
            bucket=settings.gcs_bucket,
            endpoint_url=settings.s3_endpoint_url or "https://storage.googleapis.com",
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            use_ssl=True,
        )
    raise ValueError(f"unknown storage backend: {settings.storage_backend}")


__all__ = ["Storage", "get_storage"]
