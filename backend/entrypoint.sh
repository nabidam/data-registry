#!/bin/sh
set -e

# Dependencies belong to the image, not to startup. /opt/venv is built once at
# image build time and is on PATH, so nothing here resolves, syncs, or downloads
# anything: containers start offline and instantly. Changing a dependency means
# rebuilding the image, which is the only way the built image and the running
# container can be guaranteed to agree.

# Only the API owns schema migrations. The worker waits for the API healthcheck
# in Compose, avoiding concurrent Alembic runs during deployment.
if [ "${1:-}" = "worker" ]; then
    exec python -m services.ingestion.worker
fi

alembic upgrade head
exec uvicorn main:app --host 0.0.0.0 --port 8000 "$@"
