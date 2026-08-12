FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Every dependency group is installed by default: this image runs on a machine
# that needs all of them, and one code path means the built image and the
# running container can never disagree about what is present. A lean build is
# still possible with `--build-arg UV_SYNC_ARGS="--no-dev --no-group evaluation"`.
ARG UV_SYNC_ARGS="--all-groups"

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    # Deliberately outside /app. Compose bind-mounts the source tree over /app,
    # which would otherwise hide the environment this image just built and force
    # a re-install on every start.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Dependency layer first so code edits do not reinstall the world.
COPY pyproject.toml uv.lock* /app/
RUN --mount=type=cache,id=mtdataregistry-uv,target=/root/.cache/uv,sharing=locked \
    uv sync --locked ${UV_SYNC_ARGS}

# Bundle the S3/MinIO filesystem extension from an offline artifact. The file
# must match the DuckDB version and image platform; see duckdb_extensions/README.md.
COPY duckdb_extensions/ /tmp/duckdb_extensions/
RUN python -c "import duckdb, gzip; from pathlib import Path; source = Path('/tmp/duckdb_extensions/httpfs.duckdb_extension.gz'); target = Path('/tmp/httpfs.duckdb_extension'); target.write_bytes(gzip.decompress(source.read_bytes())); duckdb.connect().execute(f\"INSTALL '{target}'\")"

COPY . /app
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
CMD ["/app/entrypoint.sh"]
