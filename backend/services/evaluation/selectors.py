"""Evaluation-selection strategies.

A selector answers one question: given the normalized rows of an import, which
``sample_id``s should be held back as evaluation data?

Selectors are plain functions registered by name so a future algorithm can
replace the default without touching the ingestion pipeline — the only contract
is the ``Selector`` signature below.
"""

from collections.abc import Callable
from math import ceil

import polars as pl

from services.evaluation.contamination_safe import SelectionResult, select as safe_select
from services.evaluation.reservation_config import load_contamination_safe_config

# (rows, n, seed) -> reservation selection. ``rows`` is the canonical schema.
Selector = Callable[[pl.DataFrame, int, int], list[int] | SelectionResult]

_URL = r"(https?://|www\.)"


def _tie_break(seed: int) -> pl.Expr:
    """Deterministic per-sample jitter so equal scores resolve reproducibly."""
    return (pl.col("sample_id").cast(pl.Utf8) + f"-{seed}").hash(seed=seed) % 1_000_000 / 1e9


def _score(seed: int) -> pl.Expr:
    """Heuristic 'is this a good benchmark sentence?' score in roughly [0, 1].

    Favours self-contained, well-formed, information-dense pairs and penalizes
    the usual crawl noise: fragments, boilerplate, URLs, digit soup and pairs
    whose two sides are wildly different lengths (likely misalignment).
    """
    src = pl.col("source_text").str.strip_chars()
    tgt = pl.col("target_text").str.strip_chars()
    src_len = src.str.len_chars()
    tgt_len = tgt.str.len_chars()
    words = src.str.split(" ").list.len()

    # Sentence-length sweet spot: long enough to be meaningful, short enough to score.
    length_fit = (1.0 - ((src_len - 90).abs() / 200.0)).clip(0.0, 1.0)
    word_fit = ((words >= 4) & (words <= 60)).cast(pl.Float64)

    # Misalignment guard: the two sides should be comparable in length.
    ratio = tgt_len / pl.max_horizontal(src_len, pl.lit(1))
    ratio_fit = (1.0 - (ratio.log(2)).abs()).clip(0.0, 1.0)

    # Noise guards.
    alpha_ratio = src.str.count_matches(r"[^\W\d_]") / pl.max_horizontal(src_len, pl.lit(1))
    clean = (
        pl.when(src.str.contains(_URL) | tgt.str.contains(_URL)).then(0.0).otherwise(1.0)
        * pl.when(src.str.contains(r"[<>{}|\\]")).then(0.0).otherwise(1.0)
    )
    # Lexical richness: distinct words over total words.
    richness = (
        src.str.to_lowercase().str.split(" ").list.unique().list.len()
        / pl.max_horizontal(words, pl.lit(1))
    ).clip(0.0, 1.0)

    quality = pl.col("quality").fill_null(0.5).clip(0.0, 1.0)
    terminated = src.str.contains(r"[.!?۔؟]\s*$").cast(pl.Float64)

    return (
        0.22 * length_fit
        + 0.14 * word_fit
        + 0.20 * ratio_fit
        + 0.14 * alpha_ratio
        + 0.10 * richness
        + 0.08 * terminated
        + 0.12 * quality
    ) * clean + _tie_break(seed)


def heuristic_select(rows: pl.DataFrame, n: int, seed: int) -> list[int]:
    """Default strategy: score for benchmark quality, then spread across strata.

    Selection is stratified over (language pair, domain) so the reserved pool
    mirrors the shape of the import instead of collapsing onto whichever stratum
    happens to contain the highest-scoring sentences.
    """
    if n <= 0 or rows.height == 0:
        return []

    strata = ["src_lang", "tgt_lang", "domain"]
    scored = (
        rows.select("sample_id", *strata, "source_text", "target_text", "quality")
        .with_columns(
            _score(seed).alias("_score"),
            pl.col("source_text").str.strip_chars().str.to_lowercase().alias("_key"),
        )
        # One benchmark item per distinct source sentence; keep the best scoring.
        .sort("_score", descending=True)
        .unique(subset=["_key"], keep="first", maintain_order=True)
    )

    total = scored.height
    # Proportional share of the reservation per stratum, at least one each.
    group_size = pl.col("sample_id").count().over(strata).cast(pl.Float64)
    quota = (group_size / total * n).ceil().clip(lower_bound=1.0)
    rank = pl.col("_score").rank("ordinal", descending=True).over(strata)

    picked = (
        scored.with_columns(rank.alias("_rank"), quota.alias("_quota"))
        .filter(pl.col("_rank") <= pl.col("_quota"))
        # Rank first: take every stratum's best before any stratum's second.
        .sort(["_rank", "_score"], descending=[False, True])
        .head(n)
    )
    return picked["sample_id"].to_list()


def random_select(rows: pl.DataFrame, n: int, seed: int) -> list[int]:
    """Uniform sampling. Kept as an explicit opt-in baseline, never the default."""
    if n <= 0 or rows.height == 0:
        return []
    return (
        rows.select("sample_id")
        .with_columns(_tie_break(seed).alias("_r"))
        .sort("_r")
        .head(n)["sample_id"]
        .to_list()
    )


def contamination_safe_select(rows: pl.DataFrame, n: int, seed: int) -> SelectionResult:
    """Ported test-set selection with document and near-duplicate protection."""
    return safe_select(rows, n, seed, load_contamination_safe_config())


SELECTORS: dict[str, Selector] = {
    "contamination_safe": contamination_safe_select,
    "heuristic": heuristic_select,
    "random": random_select,
}


def get_selector(name: str) -> Selector:
    try:
        return SELECTORS[name]
    except KeyError:
        raise ValueError(
            f"unknown evaluation selector {name!r}; available: {sorted(SELECTORS)}"
        ) from None


def reservation_size(imported: int, percent: float, max_samples: int) -> int:
    """min(imported * percent%, max_samples), never more than what was imported."""
    return max(0, min(ceil(imported * percent / 100.0), int(max_samples), imported))
