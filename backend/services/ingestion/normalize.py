"""Normalization into the single canonical schema.

Everything that enters the registry ends up shaped exactly like this, which is
what makes batches combinable without any per-source special cases downstream.
"""

import json

import polars as pl
from pydantic import BaseModel, Field

CANONICAL_COLUMNS = [
    "sample_id",
    "batch_id",
    "source_id",
    "src_lang",
    "tgt_lang",
    "domain",
    "quality",
    "source_text",
    "target_text",
    "meta",
]


class ColumnMapping(BaseModel):
    """How raw columns map onto the canonical schema."""

    source_text: str = "source"
    target_text: str = "target"
    src_lang: str | None = None
    tgt_lang: str | None = None
    domain: str | None = None
    quality: str | None = None
    meta_columns: list[str] = Field(default_factory=list)


def _column(df: pl.DataFrame, name: str | None, default, dtype: pl.DataType) -> pl.Expr:
    if name and name in df.columns:
        return pl.col(name).cast(dtype, strict=False)
    return pl.lit(default, dtype=dtype)


def normalize(
    df: pl.DataFrame,
    mapping: ColumnMapping,
    *,
    batch_id: int,
    source_id: int | None,
    src_lang: str,
    tgt_lang: str,
    domain: str | None,
) -> pl.DataFrame:
    """Map a raw frame to the canonical schema and drop unusable rows."""
    missing = [c for c in (mapping.source_text, mapping.target_text) if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns {missing}; available: {df.columns}")

    meta_cols = [c for c in mapping.meta_columns if c in df.columns]
    meta_expr = (
        pl.struct(meta_cols).map_elements(json.dumps, return_dtype=pl.Utf8)
        if meta_cols
        else pl.lit(None, dtype=pl.Utf8)
    )

    out = df.select(
        pl.col(mapping.source_text)
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .alias("source_text"),
        pl.col(mapping.target_text)
        .cast(pl.Utf8, strict=False)
        .str.strip_chars()
        .alias("target_text"),
        _column(df, mapping.src_lang, src_lang, pl.Utf8).alias("src_lang"),
        _column(df, mapping.tgt_lang, tgt_lang, pl.Utf8).alias("tgt_lang"),
        _column(df, mapping.domain, domain, pl.Utf8).alias("domain"),
        _column(df, mapping.quality, None, pl.Float64).alias("quality"),
        meta_expr.alias("meta"),
    )

    out = out.filter(
        pl.col("source_text").is_not_null()
        & pl.col("target_text").is_not_null()
        & (pl.col("source_text").str.len_chars() > 0)
        & (pl.col("target_text").str.len_chars() > 0)
    ).unique(subset=["source_text", "target_text"], keep="first")

    return out.with_columns(
        pl.lit(batch_id, dtype=pl.Int32).alias("batch_id"),
        pl.lit(source_id, dtype=pl.Int32).alias("source_id"),
    )
