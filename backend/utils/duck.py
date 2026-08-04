"""DuckDB helpers.

DuckDB is the only query engine over Parquet. Connections are short-lived and
created per request/job, configured with whatever the storage backend needs.
"""

from collections.abc import Sequence

import duckdb

from core.config import settings
from storage import get_storage


def connect(threads: int | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{settings.work_dir}/duckdb';")
    if threads:
        con.execute(f"SET threads={threads};")
    get_storage().configure_duckdb(con)
    return con


def parquet_source(uris: Sequence[str]) -> str:
    """SQL fragment reading a list of Parquet files as one relation."""
    if not uris:
        # Empty relation with the canonical schema shape.
        return (
            "(SELECT NULL::BIGINT AS sample_id, NULL::INTEGER AS batch_id, "
            "NULL::INTEGER AS source_id, NULL::VARCHAR AS src_lang, "
            "NULL::VARCHAR AS tgt_lang, NULL::VARCHAR AS domain, "
            "NULL::DOUBLE AS quality, NULL::VARCHAR AS source_text, "
            "NULL::VARCHAR AS target_text, NULL::VARCHAR AS meta WHERE false)"
        )
    quoted = ", ".join("'" + u.replace("'", "''") + "'" for u in uris)
    return f"read_parquet([{quoted}], union_by_name=true)"


def split_bucket_expr(seed: int) -> str:
    """Deterministic uniform bucket in [0, 1) derived from sample id + seed.

    Hash-based rather than shuffle-based so a split is reproducible without
    materializing or ordering the whole dataset.
    """
    return f"((hash(sample_id::VARCHAR || '-' || {int(seed)}::VARCHAR) % 1000000) / 1000000.0)"
