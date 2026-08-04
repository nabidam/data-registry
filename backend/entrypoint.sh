#!/bin/sh
set -e

# Migrations run on boot: one container, one command, no separate release step.
uv run --no-dev alembic upgrade head
exec uv run --no-dev uvicorn main:app --host 0.0.0.0 --port 8000 "$@"
