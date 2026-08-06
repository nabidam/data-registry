#!/bin/sh
set -e

# Only the API owns schema migrations. The worker waits for the API healthcheck
# in Compose, avoiding concurrent Alembic runs during deployment.
if [ "${INSTALL_EVALUATION:-false}" = "true" ]; then
    if [ "${1:-}" = "worker" ]; then
        exec uv run --group evaluation --no-dev python -m services.ingestion.worker
    fi
    uv run --group evaluation --no-dev alembic upgrade head
    exec uv run --group evaluation --no-dev uvicorn main:app --host 0.0.0.0 --port 8000 "$@"
fi

if [ "${1:-}" = "worker" ]; then
    exec uv run --no-dev python -m services.ingestion.worker
fi
uv run --no-dev alembic upgrade head
exec uv run --no-dev uvicorn main:app --host 0.0.0.0 --port 8000 "$@"
