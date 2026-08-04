#!/bin/sh
set -e

# Migrations run on boot: one container, one command, no separate release step.
if [ "${INSTALL_EVALUATION:-false}" = "true" ]; then
    uv run --group evaluation --no-dev alembic upgrade head
    exec uv run --group evaluation --no-dev uvicorn main:app --host 0.0.0.0 --port 8000 "$@"
fi

uv run --no-dev alembic upgrade head
exec uv run --no-dev uvicorn main:app --host 0.0.0.0 --port 8000 "$@"
