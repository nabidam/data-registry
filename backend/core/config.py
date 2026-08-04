"""Application settings.

All configuration comes from environment variables so the same image runs in
dev (docker compose + MinIO) and production (managed Postgres + S3/GCS).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "MT Dataset Registry"
    debug: bool = True

    # Postgres holds metadata only.
    database_url: str = "postgresql+psycopg://mtreg:mtreg@postgres:5432/mtreg"

    # Object storage: "local" | "s3" (also MinIO) | "gcs"
    storage_backend: str = "s3"
    storage_root: str = "/data/storage"  # local backend only

    s3_bucket: str = "mtreg"
    s3_endpoint_url: str | None = "http://minio:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_use_ssl: bool = False

    gcs_bucket: str = "mtreg"

    # Experiment tracking: we only store references to runs.
    mlflow_tracking_uri: str | None = None

    # Work area for DuckDB spill + parquet staging before upload.
    work_dir: str = "/tmp/mtreg"

    # Evaluation reservation, applied to every import before the batch becomes
    # eligible for training. Reserved count = min(rows * percent / 100, max).
    evaluation_percent: float = 2.0
    evaluation_max_samples: int = 5_000
    evaluation_selector: str = "contamination_safe"  # see services/evaluation/selectors.py
    random_seed: int = 42

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
