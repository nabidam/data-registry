"""Measure candidate corpus-quality rules against the registry. Read-only.

This is step 0 of the quality-flag work: before anything stores a flag, filters a
build, or changes an allocation, we need to know how many rows each candidate
rule actually matches, per batch, per language pair, and per allocation.

Nothing here writes to Postgres or to object storage. The only file it creates is
a local DuckDB scratch database used to hold source-text hashes for the
cross-batch duplicate pass, which is deleted on exit unless ``--keep-scratch``.

Why the rules are pair-scoped
-----------------------------
ADR 003 records what happens when a detector written for one language pair is
applied to another: it returns zero and the report presents the zero as a
measurement. Every rule here therefore declares which pairs it can honestly
judge, via :mod:`services.evaluation.language_profiles`. A rule that cannot apply
to a pair is reported as ``n/a`` for that pair, never as ``0``.

Text is normalized with the pair's own profile before matching, so Persian rows
that differ only by ``ي``/``ی`` keyboard variants compare equal. A naive
substring search over raw text undercounts for exactly that reason.

Usage (on the production machine)::

    cd backend
    uv run python scripts/measure_quality_flags.py
    uv run python scripts/measure_quality_flags.py --batch-id 3 --batch-id 4
    uv run python scripts/measure_quality_flags.py --sample-rows 200000 --json report.json

Environment is read the same way the API reads it, so run it where ``.env`` (or
the exported settings) point at the production Postgres and object storage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Allow `uv run python scripts/measure_quality_flags.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb  # noqa: E402
import polars as pl  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from sqlalchemy import select  # noqa: E402

from db.session import SessionLocal  # noqa: E402
from models import Batch  # noqa: E402
from services.evaluation.language_profiles import (  # noqa: E402
    PairProfile,
    pair_key,
    resolve_pair,
)
from utils.duck import connect, parquet_source  # noqa: E402


def _make_console() -> Console:
    """Console tuned for a detached run read back through ``docker logs``.

    Without a terminal to negotiate with, rich falls back to 80 columns and
    crushes the wider tables, so a usable default width is set explicitly and
    ``COLUMNS`` can override it.
    """
    if sys.stdout.isatty():
        return Console(log_path=False)
    return Console(log_path=False, width=int(os.environ.get("COLUMNS") or 160))


console = _make_console()

READ_COLUMNS = (
    "sample_id",
    "batch_id",
    "src_lang",
    "tgt_lang",
    "source_text",
    "target_text",
)

# A 6-gram repeating 3 times, matching the downstream audit's definition.
LOOP_NGRAM = 6
LOOP_REPEATS = 3

# Length-ratio candidates. The audit used a single threshold; measuring several
# lets us pick one instead of inheriting it.
LENGTH_RATIOS = (2.5, 3.0, 4.0)

# Share of target characters that must belong to the target language's expected
# script before we stop calling the row untranslated.
UNTRANSLATED_SCRIPT_SHARE = 0.20

LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
WORDLIKE_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def _nfkc_key(text: str) -> str:
    """Script-neutral comparison key, used only for source==target equality."""
    return " ".join(unicodedata.normalize("NFKC", text).lower().split())


def _hash64(text: str) -> int:
    """Stable 64-bit key. Stable across runs, unlike ``hash()``."""
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big")


# --- rule definitions ------------------------------------------------------
# Each rule is data, not scattered logic, so the production ruleset can inherit
# this exact shape (and these exact patterns) once the numbers are agreed.


@dataclass(frozen=True)
class Rule:
    """One candidate defect check.

    ``applies`` decides whether the rule can honestly judge a pair at all.
    ``match`` receives the pair profile plus both sides already normalized by
    that profile.
    """

    name: str
    description: str
    applies: Callable[[PairProfile], bool]
    match: Callable[[PairProfile, str, str], bool]


def _always(_: PairProfile) -> bool:
    return True


def _target_lang_is(*codes: str) -> Callable[[PairProfile], bool]:
    return lambda profile: profile.target.code in codes


def _has_target_script(profile: PairProfile) -> bool:
    return profile.target.script is not None


def _contains(needle: str, *, side: str) -> Callable[[PairProfile, str, str], bool]:
    def match(_: PairProfile, source: str, target: str) -> bool:
        text = source if side == "source" else target
        return needle in text

    return match


def _boilerplate_anchored(
    needle: str, window: int = 120
) -> Callable[[PairProfile, str, str], bool]:
    """Match only near the start or end of the target.

    Translator boilerplate is appended or prepended; the same phrase in the middle
    of a paragraph is far more likely to be genuine content.
    """

    def match(_: PairProfile, __: str, target: str) -> bool:
        return needle in target[:window] or needle in target[-window:]

    return match


def _cooccurs(first: str, second: str) -> Callable[[PairProfile, str, str], bool]:
    def match(_: PairProfile, __: str, target: str) -> bool:
        return first in target and second in target

    return match


def _untranslated(profile: PairProfile, _: str, target: str) -> bool:
    """Target carries almost none of its own script but plenty of Latin.

    Expressed against the target profile's expected script rather than "is it
    Persian", so the rule means the same thing for a ru-fa or en-ar corpus.
    """
    assert profile.target.script is not None
    letters = WORDLIKE_RE.findall(target)
    if len(letters) < 8:
        return False
    expected = sum(1 for ch in letters if profile.target.script.search(ch))
    latin = sum(1 for ch in letters if LATIN_CHAR_RE.match(ch))
    return (expected / len(letters)) < UNTRANSLATED_SCRIPT_SHARE and (latin / len(letters)) > 0.5


def _copy_of_source(_: PairProfile, source: str, target: str) -> bool:
    return _nfkc_key(source) == _nfkc_key(target)


def _length_ratio_rule(limit: float) -> Callable[[PairProfile, str, str], bool]:
    def match(_: PairProfile, source: str, target: str) -> bool:
        src_len = len(source.strip())
        tgt_len = len(target.strip())
        if src_len < 20 or tgt_len == 0:
            return False
        ratio = max(src_len / tgt_len, tgt_len / src_len)
        return ratio > limit

    return match


def _persian(text: str) -> str:
    """Normalize a Persian literal the same way profile-normalized rows are."""
    from services.evaluation.language_profiles import get_language_profile

    return get_language_profile("fa").normalize(text)


# Confirmed reproduced verbatim by the trained model.
BP_AI_TRANSLATED = _persian("ترجمه شده توسط هوش مصنوعی")
BP_AI_NOTE = _persian(
    "لطفاً توجه داشته باشید که این ترجمه ممکن است به طور کامل دقیق نباشد"
)
BP_AI_PHRASE = _persian("هوش مصنوعی")
BP_TRANSLATION_WORD = _persian("ترجمه")
BP_RIGHTS = _persian("کلیه حقوق محفوظ است")


RULES: tuple[Rule, ...] = (
    Rule(
        "loop_target",
        f"target contains a {LOOP_NGRAM}-gram repeating {LOOP_REPEATS}+ times",
        _always,
        lambda profile, source, target: False,  # handled separately; see _loop_flags
    ),
    Rule(
        "untranslated_target",
        "target is mostly Latin script instead of its own",
        _has_target_script,
        _untranslated,
    ),
    Rule(
        "copy_of_source",
        "target equals source after NFKC + case folding",
        _always,
        _copy_of_source,
    ),
    *(
        Rule(
            f"length_ratio_{limit:g}x",
            f"source/target character length differs by more than {limit:g}x",
            _always,
            _length_ratio_rule(limit),
        )
        for limit in LENGTH_RATIOS
    ),
    # Boilerplate, measured as competing variants so the false-positive cost of
    # each is visible before one is chosen. See the handoff caveat: a bare search
    # for "هوش مصنوعی" also matches legitimate science content.
    Rule(
        "bp_ai_naive",
        "target contains 'هوش مصنوعی' anywhere (expected to over-match)",
        _target_lang_is("fa"),
        _contains(BP_AI_PHRASE, side="target"),
    ),
    Rule(
        "bp_ai_cooccur",
        "target contains both 'ترجمه' and 'هوش مصنوعی'",
        _target_lang_is("fa"),
        _cooccurs(BP_TRANSLATION_WORD, BP_AI_PHRASE),
    ),
    Rule(
        "bp_ai_anchored",
        "'هوش مصنوعی' within 120 chars of the target's start or end",
        _target_lang_is("fa"),
        _boilerplate_anchored(BP_AI_PHRASE),
    ),
    Rule(
        "bp_ai_translated_exact",
        "target contains 'ترجمه شده توسط هوش مصنوعی' (confirmed leak)",
        _target_lang_is("fa"),
        _contains(BP_AI_TRANSLATED, side="target"),
    ),
    Rule(
        "bp_ai_note_exact",
        "target contains the 'this translation may be inaccurate' notice (confirmed leak)",
        _target_lang_is("fa"),
        _contains(BP_AI_NOTE, side="target"),
    ),
    Rule(
        "bp_rights_reserved",
        "target contains 'کلیه حقوق محفوظ است'",
        _target_lang_is("fa", "ar"),
        _contains(BP_RIGHTS, side="target"),
    ),
    Rule(
        "bp_rights_anchored",
        "'کلیه حقوق محفوظ است' within 120 chars of the target's start or end",
        _target_lang_is("fa", "ar"),
        _boilerplate_anchored(BP_RIGHTS),
    ),
    Rule(
        "bp_google_translate_target",
        "target mentions 'Google Translate'",
        _always,
        _contains("Google Translate", side="target"),
    ),
    Rule(
        "bp_google_translate_source",
        "source mentions 'Google Translate'",
        _always,
        _contains("Google Translate", side="source"),
    ),
    Rule(
        "bp_downloaded_from_target",
        "target mentions 'Downloaded from'",
        _always,
        _contains("Downloaded from", side="target"),
    ),
    Rule(
        "bp_downloaded_from_source",
        "source mentions 'Downloaded from'",
        _always,
        _contains("Downloaded from", side="source"),
    ),
)

RULE_NAMES = tuple(rule.name for rule in RULES)


# --- loop detection --------------------------------------------------------


def _repeats_ngram(tokens: list[str], n: int, times: int) -> bool:
    if len(tokens) < n + times - 1:
        return False
    counts: Counter[tuple[str, ...]] = Counter(
        tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)
    )
    return counts.most_common(1)[0][1] >= times


def _loop_prefilter(frame: pl.DataFrame) -> pl.Series:
    """Cheap necessary condition for a repeating n-gram, evaluated in polars.

    A 6-gram occurring three times forces repeated tokens, so a target whose
    tokens are all distinct cannot match. Punctuation is stripped first because
    the exact check tokenizes on word characters. Pass ``--no-prefilter`` to
    verify on a slice that the prefilter is not hiding matches.
    """
    tokens = (
        pl.col("target_text")
        .str.to_lowercase()
        .str.replace_all(r"[^\w\s]", " ")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
        .str.split(" ")
    )
    return frame.select(
        (
            (tokens.list.len() >= LOOP_NGRAM + LOOP_REPEATS - 1)
            & ((tokens.list.len() - tokens.list.unique().list.len()) >= 2)
        ).alias("candidate")
    )["candidate"]


# --- accumulation ----------------------------------------------------------


@dataclass
class Bucket:
    """Counters for one (batch, pair, allocation) group."""

    rows: int = 0
    hits: Counter[str] = field(default_factory=Counter)


@dataclass
class Totals:
    buckets: dict[tuple[int, str, str], Bucket] = field(default_factory=dict)
    applicable_pairs: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    examples: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    loop_checked: int = 0

    def bucket(self, batch_id: int, pair: str, allocation: str) -> Bucket:
        return self.buckets.setdefault((batch_id, pair, allocation), Bucket())


def _record_example(totals: Totals, rule: str, limit: int, row: dict) -> None:
    bucket = totals.examples[rule]
    if len(bucket) >= limit:
        return
    bucket.append(
        {
            "sample_id": int(row["sample_id"]),
            "batch_id": int(row["batch_id"]),
            "source_text": row["source_text"][:160],
            "target_text": row["target_text"][:160],
        }
    )


def _measure_chunk(
    frame: pl.DataFrame,
    totals: Totals,
    scratch: duckdb.DuckDBPyConnection,
    *,
    examples: int,
    use_prefilter: bool,
) -> None:
    loop_candidate = (
        _loop_prefilter(frame)
        if use_prefilter
        else pl.Series("candidate", [True] * frame.height)
    )
    key_rows: list[tuple[int, int, str, int, int]] = []

    for (src_lang, tgt_lang), group in frame.with_columns(
        loop_candidate.alias("_loop_candidate")
    ).group_by(["src_lang", "tgt_lang"]):
        profile = resolve_pair(src_lang, tgt_lang)
        pair = pair_key(src_lang, tgt_lang)
        active = [rule for rule in RULES if rule.applies(profile)]
        for rule in active:
            totals.applicable_pairs[rule.name].add(pair)

        for row in group.iter_rows(named=True):
            allocation = row["allocation"] or "UNKNOWN"
            bucket = totals.bucket(int(row["batch_id"]), pair, allocation)
            bucket.rows += 1

            source = profile.normalize_source(row["source_text"])
            target = profile.normalize_target(row["target_text"])

            for rule in active:
                if rule.name == "loop_target":
                    if not row["_loop_candidate"]:
                        continue
                    totals.loop_checked += 1
                    hit = _repeats_ngram(
                        profile.target_tokens(row["target_text"]), LOOP_NGRAM, LOOP_REPEATS
                    )
                elif not rule.match(profile, source, target):
                    continue
                else:
                    hit = True
                if not hit:
                    continue
                bucket.hits[rule.name] += 1
                _record_example(totals, rule.name, examples, row)

            source_key = profile.source_key(row["source_text"])
            key_rows.append(
                (
                    int(row["sample_id"]),
                    int(row["batch_id"]),
                    pair,
                    _hash64(source_key),
                    _hash64(source_key + "\x00" + profile.target_key(row["target_text"])),
                )
            )

    if key_rows:
        keys = pl.DataFrame(
            key_rows,
            schema={
                "sample_id": pl.Int64,
                "batch_id": pl.Int32,
                "pair_key": pl.Utf8,
                "src_hash": pl.UInt64,
                "pair_hash": pl.UInt64,
            },
            orient="row",
        )
        scratch.register("chunk_keys", keys)
        scratch.execute("INSERT INTO keys SELECT * FROM chunk_keys")
        scratch.unregister("chunk_keys")


# --- reading ---------------------------------------------------------------


def _read_batch(
    uri: str, *, chunk_rows: int, sample_rows: int, seed: int
) -> Iterator[pl.DataFrame]:
    """Stream one batch's Parquet shards as bounded polars frames."""
    con = connect()
    try:
        source = parquet_source([uri])
        available = set(con.sql(f"SELECT * FROM {source} LIMIT 0").columns)
        # Older shards predate the allocation column; union_by_name cannot invent it.
        allocation = "allocation" if "allocation" in available else "NULL AS allocation"
        columns = ", ".join(READ_COLUMNS) + f", {allocation}"
        query = f"SELECT {columns} FROM {source}"
        if sample_rows:
            query += f" USING SAMPLE {int(sample_rows)} ROWS (reservoir, {int(seed)})"
        reader = con.sql(query).fetch_arrow_reader(chunk_rows)
        for record_batch in reader:
            frame = pl.from_arrow(record_batch)
            yield frame.filter(
                pl.col("source_text").is_not_null() & pl.col("target_text").is_not_null()
            )
    finally:
        con.close()


@dataclass(frozen=True)
class BatchRef:
    id: int
    name: str
    parquet_uri: str
    sample_count: int


async def _ready_batches(batch_ids: list[int]) -> list[BatchRef]:
    async with SessionLocal() as session:
        stmt = select(Batch.id, Batch.name, Batch.parquet_uri, Batch.sample_count).where(
            Batch.status == "ready", Batch.parquet_uri.is_not(None)
        )
        if batch_ids:
            stmt = stmt.where(Batch.id.in_(batch_ids))
        rows = await session.execute(stmt.order_by(Batch.id))
        return [BatchRef(r[0], r[1], r[2], int(r[3] or 0)) for r in rows]


# --- progress --------------------------------------------------------------


def _duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class ProgressLog:
    """Timestamped, throttled progress lines.

    Deliberately one line per interval rather than an updating progress bar: a
    full-corpus run is expected to be detached, where ``docker logs`` shows a
    carriage-returned bar as unreadable noise. ``Console.log`` timestamps each
    line so a stalled scan is visible in the gap between them.
    """

    def __init__(self, total_rows: int, *, every_seconds: float) -> None:
        self.total_rows = total_rows
        self.every_seconds = every_seconds
        self.started = time.monotonic()
        self.done_rows = 0
        self._last_print = self.started
        self._batch_started = self.started
        self._batch_rows = 0
        self._batch_label = ""
        self._batch_expected = 0

    def event(self, message: str) -> None:
        console.log(message)

    def start_batch(self, batch: BatchRef, expected: int) -> None:
        self._batch_started = time.monotonic()
        self._batch_rows = 0
        self._batch_label = f"batch {batch.id} {batch.name}"
        self._batch_expected = expected
        console.log(f"{self._batch_label}: reading ~{expected:,} rows")

    def advance(self, rows: int) -> None:
        self._batch_rows += rows
        self.done_rows += rows
        now = time.monotonic()
        if now - self._last_print < self.every_seconds:
            return
        self._last_print = now
        console.log(self._line(now))

    def finish_batch(self) -> None:
        elapsed = time.monotonic() - self._batch_started
        rate = self._batch_rows / elapsed if elapsed > 0 else 0.0
        console.log(
            f"{self._batch_label}: done, {self._batch_rows:,} rows in "
            f"{_duration(elapsed)} ({rate:,.0f} rows/s)"
        )
        self._last_print = time.monotonic()

    def _line(self, now: float) -> str:
        elapsed = now - self.started
        rate = self.done_rows / elapsed if elapsed > 0 else 0.0
        parts = [
            f"{self._batch_label}: {self._batch_rows:,}",
            f"/{self._batch_expected:,}" if self._batch_expected else "",
            f" rows | corpus {self.done_rows:,}",
        ]
        if self.total_rows:
            share = self.done_rows / self.total_rows
            remaining = (self.total_rows - self.done_rows) / rate if rate > 0 else 0.0
            parts.append(
                f"/{self.total_rows:,} ({share:.1%}) | {rate:,.0f} rows/s | "
                f"eta {_duration(remaining)}"
            )
        else:
            parts.append(f" | {rate:,.0f} rows/s")
        return "".join(parts)


# --- duplicate analysis ----------------------------------------------------


def _duplicate_report(
    scratch: duckdb.DuckDBPyConnection, progress: ProgressLog | None = None
) -> dict:
    """Corpus-level duplicate counts. Per-import dedup already removed the rest.

    This is a full aggregation over every hashed row, so on a large corpus it can
    run for minutes with nothing else to report; each grouping is logged as it
    starts.
    """

    def scalar(query: str) -> int:
        result = scratch.sql(query).fetchone()
        return int(result[0]) if result and result[0] is not None else 0

    def duplicate_rows(label: str, key: str, *, scoped_by_pair: bool) -> dict:
        if progress:
            progress.event(f"duplicates: aggregating {label}")
        started = time.monotonic()
        group = f"pair_key, {key}" if scoped_by_pair else key
        # One pass per grouping. Every figure below is an aggregate over the same
        # duplicate groups, so asking for them separately would rescan the table.
        row = scratch.sql(
            f"WITH groups AS (SELECT {group}, count(*) AS copies, "
            "count(DISTINCT batch_id) AS batches FROM keys "
            f"GROUP BY {group} HAVING copies > 1) "
            # Rows that would be dropped if one copy per group is kept.
            "SELECT coalesce(sum(copies - 1), 0), count(*), "
            "count(*) FILTER (WHERE batches > 1), "
            "coalesce(sum(copies - 1) FILTER (WHERE batches > 1), 0), "
            "coalesce(max(copies), 0) FROM groups"
        ).fetchone()
        if progress:
            progress.event(f"duplicates: {label} took {_duration(time.monotonic() - started)}")
        return {
            "redundant_rows": int(row[0]),
            "groups": int(row[1]),
            "cross_batch_groups": int(row[2]),
            "cross_batch_redundant_rows": int(row[3]),
            "max_copies": int(row[4]),
        }

    return {
        "total_rows": scalar("SELECT count(*) FROM keys"),
        "duplicate_source_within_pair": duplicate_rows(
            "duplicate_source_within_pair", "src_hash", scoped_by_pair=True
        ),
        "duplicate_source_any_pair": duplicate_rows(
            "duplicate_source_any_pair", "src_hash", scoped_by_pair=False
        ),
        "duplicate_pair_within_pair": duplicate_rows(
            "duplicate_pair_within_pair", "pair_hash", scoped_by_pair=True
        ),
    }


# --- reporting -------------------------------------------------------------


def _print_rules() -> None:
    table = Table(title="Candidate rules", show_lines=False)
    table.add_column("rule")
    table.add_column("meaning")
    for rule in RULES:
        table.add_row(rule.name, rule.description)
    console.print(table)


def _print_totals(totals: Totals, batch_names: dict[int, str]) -> None:
    corpus_rows = sum(bucket.rows for bucket in totals.buckets.values())
    corpus_hits: Counter[str] = Counter()
    for bucket in totals.buckets.values():
        corpus_hits.update(bucket.hits)

    table = Table(title=f"Corpus totals over {corpus_rows:,} rows")
    table.add_column("rule")
    table.add_column("rows", justify="right")
    table.add_column("share", justify="right")
    table.add_column("applicable pairs")
    for name in RULE_NAMES:
        pairs = sorted(totals.applicable_pairs.get(name, ()))
        hits = corpus_hits.get(name, 0)
        table.add_row(
            name,
            f"{hits:,}" if pairs else "n/a",
            f"{hits / corpus_rows:.2%}" if pairs and corpus_rows else "n/a",
            ", ".join(pairs) or "none",
        )
    console.print(table)

    by_alloc: dict[str, Bucket] = {}
    for (_, _, allocation), bucket in totals.buckets.items():
        target = by_alloc.setdefault(allocation, Bucket())
        target.rows += bucket.rows
        target.hits.update(bucket.hits)

    # Transposed: one row per rule, one column per allocation. A flagged
    # RESERVED_EVALUATION row corrupts a benchmark rather than the weights, and
    # cannot be un-reserved, so its count is read separately from the rest.
    allocations = sorted(by_alloc)
    alloc_table = Table(title="By allocation")
    alloc_table.add_column("rule", overflow="fold")
    for allocation in allocations:
        alloc_table.add_column(
            f"{allocation}\n({by_alloc[allocation].rows:,} rows)", justify="right"
        )
    for name in RULE_NAMES:
        applicable = bool(totals.applicable_pairs.get(name))
        alloc_table.add_row(
            name,
            *[
                f"{by_alloc[allocation].hits.get(name, 0):,}" if applicable else "n/a"
                for allocation in allocations
            ],
        )
    console.print(alloc_table)

    pair_table = Table(title="By batch and language pair")
    pair_table.add_column("batch")
    pair_table.add_column("pair")
    pair_table.add_column("allocation")
    pair_table.add_column("rows", justify="right")
    pair_table.add_column("flagged rules (rule=rows)")
    for (batch_id, pair, allocation), bucket in sorted(totals.buckets.items()):
        flagged = ", ".join(
            f"{name}={count:,}" for name, count in sorted(bucket.hits.items()) if count
        )
        pair_table.add_row(
            f"{batch_id} {batch_names.get(batch_id, '')}",
            pair,
            allocation,
            f"{bucket.rows:,}",
            flagged or "-",
        )
    console.print(pair_table)


def _print_duplicates(report: dict) -> None:
    table = Table(title="Cross-batch duplicates (per-import dedup already ran)")
    table.add_column("grouping", overflow="fold")
    table.add_column("redundant rows", justify="right")
    table.add_column("groups", justify="right")
    table.add_column("cross-batch groups", justify="right")
    table.add_column("cross-batch redundant", justify="right")
    table.add_column("max copies", justify="right")
    for name, values in report.items():
        if not isinstance(values, dict):
            continue
        table.add_row(
            name,
            f"{values['redundant_rows']:,}",
            f"{values['groups']:,}",
            f"{values['cross_batch_groups']:,}",
            f"{values['cross_batch_redundant_rows']:,}",
            f"{values['max_copies']:,}",
        )
    console.print(table)


def _print_examples(totals: Totals) -> None:
    for name in RULE_NAMES:
        rows = totals.examples.get(name)
        if not rows:
            continue
        table = Table(title=f"examples: {name}")
        table.add_column("sample_id", justify="right")
        table.add_column("source")
        table.add_column("target")
        for row in rows:
            table.add_row(str(row["sample_id"]), row["source_text"], row["target_text"])
        console.print(table)


# --- entrypoint ------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--batch-id", type=int, action="append", default=[])
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=0,
        help="reservoir-sample this many rows per batch instead of reading all of it",
    )
    parser.add_argument("--chunk-rows", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--examples", type=int, default=5)
    parser.add_argument(
        "--no-prefilter",
        action="store_true",
        help="run the exact n-gram check on every row (slow; use with --sample-rows)",
    )
    parser.add_argument("--skip-duplicates", action="store_true")
    parser.add_argument("--json", type=Path, help="write the full report here")
    parser.add_argument(
        "--scratch",
        type=Path,
        default=Path("quality_scratch.duckdb"),
        help="local DuckDB file for duplicate hashes",
    )
    parser.add_argument("--keep-scratch", action="store_true")
    parser.add_argument(
        "--progress-seconds",
        type=float,
        default=15.0,
        help="minimum seconds between progress lines",
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    started = time.monotonic()

    console.log("connecting to Postgres for the batch list")
    batches = await _ready_batches(args.batch_id)
    if not batches:
        console.print("[red]no ready batches with a parquet_uri found[/red]")
        return 1

    _print_rules()

    def expected_rows(batch: BatchRef) -> int:
        if args.sample_rows:
            return min(batch.sample_count, args.sample_rows) or args.sample_rows
        return batch.sample_count

    total_expected = sum(expected_rows(batch) for batch in batches)
    console.log(
        f"measuring {len(batches)} batch(es), ~{total_expected:,} rows, read-only. "
        f"prefilter={'off' if args.no_prefilter else 'on'} "
        f"sample_rows={args.sample_rows or 'all'} scratch={args.scratch}"
    )

    args.scratch.unlink(missing_ok=True)
    scratch = duckdb.connect(str(args.scratch))
    totals = Totals()
    batch_names = {batch.id: batch.name for batch in batches}
    progress = ProgressLog(total_expected, every_seconds=args.progress_seconds)
    try:
        scratch.execute(
            "CREATE TABLE keys (sample_id BIGINT, batch_id INTEGER, pair_key VARCHAR, "
            "src_hash UBIGINT, pair_hash UBIGINT)"
        )
        for batch in batches:
            progress.start_batch(batch, expected_rows(batch))
            for frame in _read_batch(
                batch.parquet_uri,
                chunk_rows=args.chunk_rows,
                sample_rows=args.sample_rows,
                seed=args.seed,
            ):
                _measure_chunk(
                    frame,
                    totals,
                    scratch,
                    examples=args.examples,
                    use_prefilter=not args.no_prefilter,
                )
                progress.advance(frame.height)
            progress.finish_batch()

        if args.skip_duplicates:
            duplicates = {}
            progress.event("duplicates: skipped")
        else:
            duplicates = _duplicate_report(scratch, progress)
    except KeyboardInterrupt:
        # Report what was measured rather than throwing away hours of scanning.
        console.log("[yellow]interrupted; reporting partial results[/yellow]")
        duplicates = {}
    finally:
        scratch.close()
        if not args.keep_scratch:
            args.scratch.unlink(missing_ok=True)

    progress.event(f"measured {progress.done_rows:,} rows; building report")
    console.print()
    _print_totals(totals, batch_names)
    if duplicates:
        _print_duplicates(duplicates)
    _print_examples(totals)
    console.print(
        f"\nloop check: {totals.loop_checked:,} rows passed the cheap prefilter and were "
        f"tokenized{' (prefilter disabled)' if args.no_prefilter else ''}"
    )
    console.log(f"finished in {_duration(time.monotonic() - started)}")

    if args.json:
        report = {
            "rules": [
                {
                    "name": rule.name,
                    "description": rule.description,
                    "applicable_pairs": sorted(totals.applicable_pairs.get(rule.name, ())),
                }
                for rule in RULES
            ],
            "buckets": [
                {
                    "batch_id": batch_id,
                    "batch_name": batch_names.get(batch_id),
                    "pair_key": pair,
                    "allocation": allocation,
                    "rows": bucket.rows,
                    "hits": dict(bucket.hits),
                }
                for (batch_id, pair, allocation), bucket in sorted(totals.buckets.items())
            ],
            "duplicates": duplicates,
            "examples": {name: rows for name, rows in totals.examples.items()},
            "settings": {
                "sample_rows": args.sample_rows,
                "seed": args.seed,
                "prefilter": not args.no_prefilter,
                "loop_ngram": LOOP_NGRAM,
                "loop_repeats": LOOP_REPEATS,
            },
        }
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        console.log(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
