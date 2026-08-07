FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Local images stay lean. Production builds opt in with
# `--build-arg INSTALL_EVALUATION=true`.
ARG INSTALL_EVALUATION=false

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    INSTALL_EVALUATION=${INSTALL_EVALUATION}

WORKDIR /app

# Dependency layer first so code edits do not reinstall the world.
COPY pyproject.toml uv.lock* /app/
RUN --mount=type=cache,id=mtdataregistry-uv,target=/root/.cache/uv,sharing=locked \
    if [ "$INSTALL_EVALUATION" = "true" ]; then \
      uv sync --locked --group evaluation --no-dev; \
    else \
      uv sync --locked --no-dev; \
    fi

# Bundle the S3/MinIO filesystem extension from an offline artifact. The file
# must match the DuckDB version and image platform; see duckdb_extensions/README.md.
COPY duckdb_extensions/ /tmp/duckdb_extensions/
RUN /app/.venv/bin/python -c "import duckdb, gzip; from pathlib import Path; source = Path('/tmp/duckdb_extensions/httpfs.duckdb_extension.gz'); target = Path('/tmp/httpfs.duckdb_extension'); target.write_bytes(gzip.decompress(source.read_bytes())); duckdb.connect().execute(f\"INSTALL '{target}'\")"

COPY . /app
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
CMD ["/app/entrypoint.sh"]
