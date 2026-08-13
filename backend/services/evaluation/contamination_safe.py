"""Full contamination-safe MT evaluation reservation pipeline.

This ports the selection stages from the supplied ``build_test_set.py`` into
immutable batch ingestion.  Rather than emitting duplicate train/dev/test CSVs,
the registry retains split and gold annotations in batch Parquet, reserves only
evaluation rows, and quarantines every row excluded to prevent leakage.

Every stage here is language-pair scoped.  Rows carry a ``pair_key`` derived from
their ``src_lang``/``tgt_lang``, and deduplication, quotas, document holdout, and
contamination comparison all group by it.  The rule is that each pair protects
itself by default; comparing across pairs is an explicit ``comparison_scope``
choice, because it changes what "contamination" means and can remove rows another
pair considers valid.  Language-specific behaviour — normalization, expected
script, acronym and unit detection, and which features are measurable at all —
lives in ``language_profiles``, not here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from core.config import settings
from services.evaluation.language_profiles import (
    ALL_FEATURES,
    FEATURE_RARE_TERM,
    PairProfile,
)
from services.evaluation.language_profiles import pair_key as make_pair_key
from services.evaluation.language_profiles import resolve_pair
from services.evaluation.reservation_config import (
    COMPARISON_SCOPES,
    DOCUMENT_NAMESPACES,
    resolve_pair_policy,
    validate_policy,
)

SelectionProgress = Callable[[str, str, int | None, int | None, float | None], None]
log = logging.getLogger(__name__)

# Comparison scopes decide which rows may be compared during the contamination
# scan. See config/evaluation_reservation.yaml for the operator-facing wording.
# The tuple itself lives in reservation_config so policy validation can reach it
# without importing this module and its heavy dependencies.
SCOPE_PAIR = "pair"
SCOPE_SOURCE_LANGUAGE = "source_language"
SCOPE_TARGET_LANGUAGE = "target_language"
SCOPE_ANY = "any"

# Columns the selector and scan read. A frame that lacks one (an older Parquet
# shard, or a caller that projected it away) is backfilled with nulls, which
# resolves to the fallback language profile rather than raising.
_ROW_COLUMNS = (
    "sample_id",
    "source_text",
    "target_text",
    "domain",
    "document_id",
    "src_lang",
    "tgt_lang",
    "source_id",
    "batch_id",
)


def _records(rows: pl.DataFrame) -> list[dict[str, Any]]:
    """Project the columns the pipeline needs, tolerating frames that lack some."""
    present = [column for column in _ROW_COLUMNS if column in rows.columns]
    missing = [column for column in _ROW_COLUMNS if column not in rows.columns]
    frame = rows.select(present)
    if missing:
        frame = frame.with_columns(
            [pl.lit(None, dtype=pl.Utf8).alias(column) for column in missing]
        )
    return frame.to_dicts()


def _profile_for(record: dict[str, Any]) -> PairProfile:
    return resolve_pair(record.get("src_lang"), record.get("tgt_lang"))


def comparison_scope(config: dict[str, Any], override: str | None = None) -> str:
    """Resolve and validate the comparison scope, preferring an explicit override."""
    scope = override or str(config.get("contamination", {}).get("comparison_scope", SCOPE_PAIR))
    if scope not in COMPARISON_SCOPES:
        raise ValueError(
            f"unknown contamination comparison scope {scope!r}; "
            f"available: {list(COMPARISON_SCOPES)}"
        )
    return scope


def _group_key(scope: str, src_lang: str | None, tgt_lang: str | None) -> str:
    """The identity two rows must share before they may be compared at all."""
    source, target = make_pair_key(src_lang, tgt_lang).split("-", 1)
    if scope == SCOPE_PAIR:
        return f"{source}-{target}"
    if scope == SCOPE_SOURCE_LANGUAGE:
        return f"{source}-*"
    if scope == SCOPE_TARGET_LANGUAGE:
        return f"*-{target}"
    return "*"


def _side_thresholds(scope: str, contamination: dict[str, Any]) -> tuple[float, float]:
    """Cosine thresholds for the (source, target) sides under one scope.

    A side whose language the scope pins is a monolingual comparison and keeps the
    calibrated ``near_dup_cosine_threshold``.  A side the scope leaves free may
    compare two different languages, where LaBSE places an ordinary translation
    and its source around 0.85-0.95; that side needs the stricter cross-lingual
    threshold so equivalence is not mistaken for duplication.
    """
    same = float(contamination.get("near_dup_cosine_threshold", 0.92))
    cross = float(contamination.get("cross_lingual_cosine_threshold", 0.97))
    if scope == SCOPE_PAIR:
        return same, same
    if scope == SCOPE_SOURCE_LANGUAGE:
        return same, cross
    if scope == SCOPE_TARGET_LANGUAGE:
        return cross, same
    return cross, cross



@dataclass(frozen=True)
class SelectionResult:
    """Evaluation choices, leakage exclusions, and immutable row annotations."""

    reserved_ids: list[int]
    quarantined_ids: list[int]
    dev_ids: list[int]
    gold_ids: list[int]
    annotations: dict[int, dict[str, Any]]
    report: dict[str, Any]


@dataclass
class _ReferenceGroup:
    """Reserved rows that one comparison group may be checked against."""

    source_keys: set[str] = field(default_factory=set)
    target_keys: set[str] = field(default_factory=set)
    document_ids: set[str] = field(default_factory=set)
    source_shingles: set[str] = field(default_factory=set)
    target_shingles: set[str] = field(default_factory=set)
    source_texts: list[str] = field(default_factory=list)
    target_texts: list[str] = field(default_factory=list)
    source_embeddings: Any = None
    target_embeddings: Any = None


@dataclass
class ContaminationReference:
    """Selected evaluation rows prepared for a bounded full-corpus scan.

    Rows are bucketed by comparison group, so a scanned row is only ever compared
    with reserved rows it is allowed to be compared with. ``global_target_keys``
    sits outside the grouping: an identical target string is output the model
    would memorize regardless of which source language produced it, so exact
    target duplication is checked across every pair.
    """

    selected_ids: set[int]
    comparison_scope: str
    groups: dict[str, _ReferenceGroup]
    global_target_keys: set[str]
    source_encoder: Any = None
    target_encoder: Any = None


@dataclass(frozen=True)
class ContaminationScanResult:
    quarantined_ids: set[int]
    exact_or_document_rows: int
    semantic_prefilter_rows: int
    semantic_checked_rows: int
    exact_target_rows: int = 0



_MODEL_CACHE: dict[tuple[str, str], Any] = {}


def _emit(
    progress: SelectionProgress | None,
    stage: str,
    message: str,
    completed: int | None = None,
    total: int | None = None,
    elapsed: float | None = None,
) -> None:
    log.info("[%s] %s", stage, message)
    if progress:
        progress(stage, message, completed, total, elapsed)


@dataclass(frozen=True)
class _Row:
    sample_id: int
    source: str
    target: str
    domain: str
    document_id: str
    n_tokens: int
    length_bucket: str
    rare_term_score: float
    flags: dict[str, bool]
    src_lang: str
    tgt_lang: str
    pair_key: str
    profile: PairProfile
    source_key: str
    target_key: str


def _tokens(text: str, profile: PairProfile | None = None, *, side: str = "source") -> list[str]:
    """Tokenize with the language's own normalization.

    ``profile`` is optional so callers that genuinely have no language context
    (an ad-hoc frame missing ``src_lang``) still get the previous behaviour via
    the fallback profile rather than an error.
    """
    resolved = profile or resolve_pair(None, None)
    return resolved.source_tokens(text) if side == "source" else resolved.target_tokens(text)


def _document_id(row: dict[str, Any], separator: str, namespace: str = "source") -> str:
    """Namespaced document identity.

    Importers rarely emit globally unique document IDs, so a registry-wide scan
    would otherwise treat "doc:1" from two unrelated corpora as one document and
    hold out both. Namespacing by ``source_id`` keeps cross-batch holdout working
    when the same corpus is re-imported, which is the case the holdout exists for,
    while separating corpora that merely reused an ID.
    """
    value = row.get("document_id")
    if value in (None, ""):
        return f"sample:{row['sample_id']}"
    document = str(value).split(separator, 1)[0]
    if namespace == "source":
        return f"src{row.get('source_id')}:{document}"
    if namespace == "batch":
        return f"batch{row.get('batch_id')}:{document}"
    if namespace == "none":
        return document
    # Never fall through silently: an unrecognised namespace would quietly return
    # un-namespaced IDs and reintroduce cross-corpus document collisions.
    raise ValueError(
        f"unknown document namespace {namespace!r}; available: {list(DOCUMENT_NAMESPACES)}"
    )


def document_key(record: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    """Namespaced document identity, for callers measuring holdout cost.

    Selection keys documents by this, so anything sizing those documents has to
    key them the same way. Exposed rather than reimplemented in SQL, because a
    second implementation of an identity rule is a second thing to keep in step.
    """
    separator = str(config.get("input", {}).get("id_separator", ":"))
    return _document_id(dict(record), separator, _document_namespace(dict(config)))


def _document_namespace(config: dict[str, Any]) -> str:
    namespace = str(config.get("contamination", {}).get("document_namespace", "source"))
    if namespace not in DOCUMENT_NAMESPACES:
        raise ValueError(
            f"unknown document namespace {namespace!r}; available: {list(DOCUMENT_NAMESPACES)}"
        )
    return namespace


def _bucket(token_count: int, edges: list[int]) -> str:
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        if low <= token_count < high:
            return f"len_{low}_{high}"
    return f"len_{edges[-2]}_{edges[-1]}"


def _annotate(records: list[dict[str, Any]], config: dict[str, Any]) -> list[_Row]:
    """Annotate rows one language pair at a time.

    Rare-term scoring compares a token against the frequency of that token in the
    corpus, which is only meaningful inside one language: pooling Russian and
    English tokens into one table would make every Russian token look rare purely
    because English rows outnumber it. Length buckets are likewise per pair, since
    the edges count source tokens and languages differ in token density.
    """
    separator = str(config.get("input", {}).get("id_separator", ":"))
    namespace = _document_namespace(config)
    by_pair: dict[str, list[int]] = defaultdict(list)
    profiles: dict[str, PairProfile] = {}
    for index, record in enumerate(records):
        profile = _profile_for(record)
        profiles[profile.pair_key] = profile
        by_pair[profile.pair_key].append(index)

    annotated: list[_Row | None] = [None] * len(records)
    for key, indexes in by_pair.items():
        profile = profiles[key]
        policy = resolve_pair_policy(config, key)
        selection = policy["selection"]
        edges = [int(edge) for edge in selection["length_buckets"]]
        rare_max = int(policy["features"]["rare_token_max_count"])

        token_lists = [profile.source_tokens(str(records[index]["source_text"])) for index in indexes]
        frequency: Counter[str] = Counter()
        for tokens in token_lists:
            frequency.update(set(tokens))
        rare_scores = [
            sum(frequency[token] <= rare_max for token in tokens) / len(tokens) if tokens else 0.0
            for tokens in token_lists
        ]
        ordered = sorted(rare_scores)
        quantile = float(selection["rare_term_top_quantile"])
        rare_threshold = ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]

        for index, tokens, rare_score in zip(indexes, token_lists, rare_scores, strict=True):
            record = records[index]
            source = str(record["source_text"])
            target = str(record["target_text"])
            flags = profile.flags(source, target)
            flags[FEATURE_RARE_TERM] = rare_score >= rare_threshold
            annotated[index] = _Row(
                sample_id=int(record["sample_id"]),
                source=source,
                target=target,
                domain=str(record.get("domain") or "unknown"),
                document_id=_document_id(record, separator, namespace),
                n_tokens=len(tokens),
                length_bucket=_bucket(len(tokens), edges),
                rare_term_score=rare_score,
                flags=flags,
                src_lang=profile.src_lang,
                tgt_lang=profile.tgt_lang,
                pair_key=key,
                profile=profile,
                source_key=" ".join(tokens),
                target_key=profile.target_key(target),
            )
    # Callers index this list positionally against ``records``. A missing slot
    # would silently shift every later index onto the wrong sample, so refuse
    # rather than return a shorter list.
    if any(row is None for row in annotated):
        missing = [index for index, row in enumerate(annotated) if row is None]
        raise RuntimeError(
            f"annotation left {len(missing)} of {len(records)} rows unassigned "
            f"(first at index {missing[0]}); refusing to return a misaligned frame"
        )
    return [row for row in annotated if row is not None]


def _shingles(
    text: str,
    size: int = 3,
    profile: PairProfile | None = None,
    *,
    side: str = "source",
) -> set[str]:
    tokens = _tokens(text, profile, side=side)
    if len(tokens) < size:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}



def _require_evaluation_group() -> None:
    """Fail only when the heavy selector is used, not while the API boots."""
    try:
        import datasketch  # noqa: F401
        import numpy  # noqa: F401
        import sklearn  # noqa: F401
        import sentence_transformers  # noqa: F401
        import torch  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "contamination_safe requires the optional evaluation dependency group; "
            "run `uv sync --group evaluation` before importing with this selector"
        ) from exc


def _dedup_exact(rows: list[_Row], indexes: list[int]) -> tuple[list[int], set[int]]:
    """Drop repeated source sentences within a language pair.

    The key carries the pair so a batch holding several pairs cannot delete a
    valid ``en-de`` row because an ``en-fa`` row shares its English source. Those
    are two different training examples, not a duplicate.
    """
    seen: set[tuple[str, str]] = set()
    kept: list[int] = []
    dropped: set[int] = set()
    for index in indexes:
        row = rows[index]
        key = (row.pair_key, row.source_key)
        if row.source_key and key not in seen:
            seen.add(key)
            kept.append(index)
        else:
            dropped.add(index)
    return kept, dropped


def _dedup_minhash(
    rows: list[_Row],
    indexes: list[int],
    threshold: float,
    progress: SelectionProgress | None = None,
) -> tuple[list[int], set[int]]:
    from datasketch import MinHash, MinHashLSH

    # One index per pair: near-duplicate shingle overlap between two different
    # source languages is not duplication, and pooling them would let a frequent
    # pair evict rows from a smaller one.
    indexes_by_pair: dict[str, Any] = {}
    kept: list[int] = []
    dropped: set[int] = set()
    started = time.perf_counter()
    log.info("MinHash dedup: checking %s candidates (threshold=%s)", len(indexes), threshold)
    for position, index in enumerate(indexes, start=1):
        row = rows[index]
        lsh = indexes_by_pair.get(row.pair_key)
        if lsh is None:
            lsh = MinHashLSH(threshold=threshold, num_perm=128)
            indexes_by_pair[row.pair_key] = lsh
        signature = MinHash(num_perm=128)
        for shingle in _shingles(row.source, 3, row.profile):
            signature.update(shingle.encode("utf-8"))
        if lsh.query(signature):
            dropped.add(index)
        else:
            lsh.insert(str(index), signature)
            kept.append(index)
        if position % 1_000 == 0 or position == len(indexes):
            log.info(
                "MinHash dedup: checked %s/%s candidates; dropped %s",
                position,
                len(indexes),
                len(dropped),
            )
            _emit(
                progress,
                "minhash_dedup",
                f"MinHash checked {position:,}/{len(indexes):,} candidates",
                position,
                len(indexes),
                time.perf_counter() - started,
            )
    return kept, dropped


def _cache_paths(texts: list[str], config: dict[str, Any]) -> tuple[Path | None, str]:
    configured = config["embeddings"].get("cache_path")
    # The embedding backend is part of the identity of a cached vector: the same
    # texts encoded by LaBSE and by TF-IDF+SVD are different arrays of different
    # widths, and reusing one for the other silently corrupts every similarity.
    backend = json.dumps(config["embeddings"], sort_keys=True, default=str)
    digest = hashlib.sha256(f"{backend}\n{chr(10).join(texts)}".encode()).hexdigest()
    if not configured:
        return None, digest
    path = Path(str(configured))
    if not path.is_absolute():
        path = Path(settings.work_dir) / path
    return path, digest


def _normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    import numpy as np

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (embeddings / norms).astype(np.float32)


def _tfidf_embeddings(texts: list[str]) -> np.ndarray:
    import numpy as np
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=50_000)
    matrix = vectorizer.fit_transform(texts)
    dimensions = min(256, matrix.shape[1] - 1, len(texts) - 1)
    if dimensions < 1:
        return _normalize_embeddings(matrix.toarray().astype(np.float32))
    return _normalize_embeddings(
        TruncatedSVD(n_components=dimensions, random_state=0).fit_transform(matrix)
    )


def _embedding_model(config: dict[str, Any]):
    """Load one LaBSE instance per worker process and reuse it across shards."""
    from sentence_transformers import SentenceTransformer

    embedding_config = config["embeddings"]
    device = str(embedding_config["device"])
    if device == "auto":
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    key = (str(embedding_config["model"]), device)
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = SentenceTransformer(key[0], device=device)
        _MODEL_CACHE[key] = model
    return model


def _encode_labse(
    texts: list[str],
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
    *,
    stage: str = "labse_embeddings",
):
    import numpy as np

    started = time.perf_counter()
    model_name = str(config["embeddings"]["model"])
    log.info("LaBSE[%s]: encoding %s texts", stage, len(texts))
    _emit(progress, stage, f"Loading LaBSE model {model_name}", 0, len(texts), 0.0)
    model = _embedding_model(config)
    batch_size = int(config["embeddings"]["batch_size"])
    chunk_size = max(256, batch_size * 8)
    device = str(getattr(model, "device", config["embeddings"]["device"]))
    _emit(
        progress,
        stage,
        (
            f"LaBSE ready on {device}; encoding {len(texts):,} rows "
            f"in chunks of {chunk_size:,}"
        ),
        0,
        len(texts),
        time.perf_counter() - started,
    )
    chunks = []
    for start in range(0, len(texts), chunk_size):
        stop = min(start + chunk_size, len(texts))
        chunks.append(
            np.asarray(
                model.encode(
                    texts[start:stop],
                    batch_size=batch_size,
                    show_progress_bar=False,
                    normalize_embeddings=True,
                ),
                dtype=np.float32,
            )
        )
        log.info("LaBSE[%s]: encoded %s/%s rows", stage, stop, len(texts))
        _emit(
            progress,
            stage,
            f"LaBSE encoded {stop:,}/{len(texts):,} rows",
            stop,
            len(texts),
            time.perf_counter() - started,
        )
    result = np.vstack(chunks) if chunks else np.empty((0, 0), dtype=np.float32)
    if result.shape[0] != len(texts):
        log.warning(
            "LaBSE encoding produced %d embeddings for %d texts",
            result.shape[0],
            len(texts),
        )
    return result


def _compute_embeddings(
    rows: list[_Row],
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
) -> np.ndarray:
    import numpy as np

    texts = [row.source for row in rows]
    cache, digest = _cache_paths(texts, config)
    if cache:
        metadata = cache.with_suffix(".json")
        if cache.exists() and metadata.exists():
            try:
                if json.loads(metadata.read_text())["data_hash"] == digest:
                    embeddings = np.load(cache)
                    log.info("Embeddings: cache hit, loaded %s from %s", len(texts), cache)
                    _emit(
                        progress,
                        "labse_embeddings",
                        f"Loaded {len(texts):,} cached embeddings",
                        len(texts),
                        len(texts),
                        0.0,
                    )
                    return embeddings
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass

    embedding_config = config["embeddings"]
    if embedding_config["enabled"]:
        log.info("Embeddings: computing LaBSE for %s rows (no cache hit)", len(texts))
        embeddings = _encode_labse(texts, config, progress)
    else:
        log.info("Embeddings: computing TF-IDF for %s rows", len(texts))
        _emit(progress, "tfidf_embeddings", f"Computing TF-IDF for {len(texts):,} rows")
        embeddings = _tfidf_embeddings(texts)
        _emit(
            progress,
            "tfidf_embeddings",
            f"Computed TF-IDF for {len(texts):,} rows",
            len(texts),
            len(texts),
        )

    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, embeddings)
        cache.with_suffix(".json").write_text(json.dumps({"data_hash": digest}))
        log.info("Embeddings: wrote %s embeddings to cache %s", len(texts), cache)
    return embeddings


def prepare_contamination_reference(
    selected_rows: pl.DataFrame,
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
    *,
    scope: str | None = None,
) -> ContaminationReference:
    """Prepare reserved rows for streaming checks against the complete corpus.

    Both sides are embedded. The scan gates candidates on source *and* target
    shingle overlap, so resolving every candidate against source embeddings alone
    left target-gated rows compared on a side that could not confirm them: full
    transformer cost for a decision that was structurally unable to fire.
    """
    validate_policy(config)
    records = _records(selected_rows)
    separator = str(config.get("input", {}).get("id_separator", ":"))
    contamination = config["contamination"]
    namespace = _document_namespace(config)
    resolved_scope = comparison_scope(config, scope)
    prefilter_size = max(1, int(contamination.get("semantic_prefilter_shingle_size", 3)))
    hold_documents = bool(contamination["document_level_holdout"])

    selected_ids: set[int] = set()
    global_target_keys: set[str] = set()
    groups: dict[str, _ReferenceGroup] = defaultdict(_ReferenceGroup)
    for record in records:
        profile = _profile_for(record)
        source = str(record["source_text"])
        target = str(record["target_text"])
        selected_ids.add(int(record["sample_id"]))
        target_key = profile.target_key(target)
        global_target_keys.add(target_key)

        group = groups[_group_key(resolved_scope, record.get("src_lang"), record.get("tgt_lang"))]
        group.source_keys.add(profile.source_key(source))
        group.target_keys.add(target_key)
        if hold_documents:
            group.document_ids.add(_document_id(record, separator, namespace))
        group.source_shingles.update(_shingles(source, prefilter_size, profile))
        group.target_shingles.update(_shingles(target, prefilter_size, profile, side="target"))
        group.source_texts.append(source)
        group.target_texts.append(target)

    source_encoder = None
    target_encoder = None
    if config["embeddings"]["enabled"]:
        for key, group in groups.items():
            group.source_embeddings = _encode_labse(
                group.source_texts, config, progress, stage=f"reference_embeddings[{key}]"
            )
            group.target_embeddings = _encode_labse(
                group.target_texts, config, progress, stage=f"reference_target_embeddings[{key}]"
            )
    else:
        from sklearn.feature_extraction.text import TfidfVectorizer

        # One vectorizer across all groups: a scanned row must be projected into
        # the same vocabulary as the reserved rows it is compared with.
        source_encoder = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(2, 4), max_features=50_000
        )
        target_encoder = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(2, 4), max_features=50_000
        )
        all_sources = [text for group in groups.values() for text in group.source_texts]
        all_targets = [text for group in groups.values() for text in group.target_texts]
        source_encoder.fit(all_sources or [""])
        target_encoder.fit(all_targets or [""])
        for group in groups.values():
            group.source_embeddings = source_encoder.transform(group.source_texts)
            group.target_embeddings = target_encoder.transform(group.target_texts)

    for group in groups.values():
        group.source_texts.clear()
        group.target_texts.clear()

    log.info(
        "Contamination reference: %s reserved rows in %s comparison group(s), scope=%s",
        len(selected_ids),
        len(groups),
        resolved_scope,
    )
    return ContaminationReference(
        selected_ids=selected_ids,
        comparison_scope=resolved_scope,
        groups=dict(groups),
        global_target_keys=global_target_keys,
        source_encoder=source_encoder,
        target_encoder=target_encoder,
    )


def _similarity_hits(
    candidates: list[dict[str, Any]],
    texts: list[str],
    reference_matrix: Any,
    threshold: float,
    config: dict[str, Any],
    encoder: Any,
    progress: SelectionProgress | None,
    stage: str,
) -> set[int]:
    """Sample ids whose embedding reaches ``threshold`` against the reference matrix."""
    import numpy as np

    if not candidates or reference_matrix is None:
        return set()
    if config["embeddings"]["enabled"]:
        vectors = _encode_labse(texts, config, progress, stage=stage)
        dense = True
    else:
        vectors = encoder.transform(texts)
        dense = False
    if vectors.shape[0] != len(candidates):
        log.warning(
            "%s produced %d vectors for %d texts; trailing candidates skipped",
            stage,
            vectors.shape[0],
            len(candidates),
        )
        candidates = candidates[: vectors.shape[0]]

    hits: set[int] = set()
    for start in range(0, len(candidates), 512):
        block = vectors[start : start + 512] @ reference_matrix.T
        maximum = block.max(axis=1) if dense else np.asarray(block.toarray().max(axis=1)).ravel()
        hits.update(
            int(record["sample_id"])
            for record, similarity in zip(candidates[start : start + 512], maximum, strict=True)
            if similarity >= threshold
        )
    return hits


def scan_full_corpus_contamination(
    rows: pl.DataFrame,
    reference: ContaminationReference,
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
) -> ContaminationScanResult:
    """Find train/evaluation leakage in one bounded frame.

    Three checks, in increasing cost. An exact target duplicate is quarantined
    across every language pair, because a training row that produces a reserved
    row's exact output leaks that output whatever its source language. Exact
    source duplication and selected-document membership are then checked within
    the row's comparison group. Finally, rows sharing enough token shingles with
    the group's reserved pool are verified by embedding similarity — on the same
    side that admitted them, so a target-gated row is decided on its target.

    Memory stays proportional to one ingestion frame plus the selected set.
    """
    records = _records(rows)
    separator = str(config.get("input", {}).get("id_separator", ":"))
    contamination = config["contamination"]
    namespace = _document_namespace(config)
    contaminated: set[int] = set()
    source_candidates: list[dict[str, Any]] = []
    target_candidates: list[dict[str, Any]] = []
    candidate_groups: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = defaultdict(
        lambda: ([], [])
    )
    prefilter_size = max(1, int(contamination.get("semantic_prefilter_shingle_size", 3)))
    prefilter_minimum = max(1, int(contamination.get("semantic_prefilter_min_shared_shingles", 2)))
    check_exact_target = bool(contamination.get("exact_target_duplication", True))
    exact_target_rows = 0
    started = time.perf_counter()
    log.info(
        "Full corpus scan: checking %s rows against %s selected in %s group(s); "
        "scope=%s, prefilter shingle size %s, minimum shared %s",
        len(records),
        len(reference.selected_ids),
        len(reference.groups),
        reference.comparison_scope,
        prefilter_size,
        prefilter_minimum,
    )
    for position, record in enumerate(records, start=1):
        sample_id = int(record["sample_id"])
        if sample_id in reference.selected_ids:
            continue
        profile = _profile_for(record)
        source = str(record["source_text"])
        target = str(record["target_text"])

        if check_exact_target and profile.target_key(target) in reference.global_target_keys:
            contaminated.add(sample_id)
            exact_target_rows += 1
            continue

        key = _group_key(
            reference.comparison_scope, record.get("src_lang"), record.get("tgt_lang")
        )
        group = reference.groups.get(key)
        # No reserved rows this row may be compared with: pair isolation, not an
        # error. The row simply has nothing to leak into.
        if group is None:
            continue

        if (
            profile.source_key(source) in group.source_keys
            or _document_id(record, separator, namespace) in group.document_ids
        ):
            contaminated.add(sample_id)
            continue

        if (
            len(_shingles(source, prefilter_size, profile) & group.source_shingles)
            >= prefilter_minimum
        ):
            candidate_groups[key][0].append(record)
            source_candidates.append(record)
        elif (
            len(
                _shingles(target, prefilter_size, profile, side="target") & group.target_shingles
            )
            >= prefilter_minimum
        ):
            candidate_groups[key][1].append(record)
            target_candidates.append(record)

        if position % 5_000 == 0 or position == len(records):
            log.info(
                "Full corpus scan: prefiltered %s/%s rows; "
                "%s contaminated, %s source and %s target semantic candidates",
                position,
                len(records),
                len(contaminated),
                len(source_candidates),
                len(target_candidates),
            )
            _emit(
                progress,
                "full_corpus_prefilter",
                (
                    f"Prefiltered {position:,}/{len(records):,} shard rows; "
                    f"{len(source_candidates) + len(target_candidates):,} require "
                    "semantic verification"
                ),
                position,
                len(records),
                time.perf_counter() - started,
            )

    prefiltered = len(source_candidates) + len(target_candidates)
    exact_or_document_rows = len(contaminated)
    if not contamination["near_dup_check_enabled"] or not prefiltered:
        return ContaminationScanResult(
            contaminated,
            exact_or_document_rows=exact_or_document_rows,
            semantic_prefilter_rows=prefiltered,
            semantic_checked_rows=0,
            exact_target_rows=exact_target_rows,
        )

    source_threshold, target_threshold = _side_thresholds(
        reference.comparison_scope, contamination
    )
    checked = 0
    for key, (by_source, by_target) in candidate_groups.items():
        group = reference.groups[key]
        if by_source:
            contaminated.update(
                _similarity_hits(
                    by_source,
                    [str(record["source_text"]) for record in by_source],
                    group.source_embeddings,
                    source_threshold,
                    config,
                    reference.source_encoder,
                    progress,
                    stage=f"full_corpus_source_embeddings[{key}]",
                )
            )
            checked += len(by_source)
        if by_target:
            contaminated.update(
                _similarity_hits(
                    by_target,
                    [str(record["target_text"]) for record in by_target],
                    group.target_embeddings,
                    target_threshold,
                    config,
                    reference.target_encoder,
                    progress,
                    stage=f"full_corpus_target_embeddings[{key}]",
                )
            )
            checked += len(by_target)

    return ContaminationScanResult(
        contaminated,
        exact_or_document_rows=exact_or_document_rows,
        semantic_prefilter_rows=prefiltered,
        semantic_checked_rows=checked,
        exact_target_rows=exact_target_rows,
    )


def _dedup_embeddings(
    rows: list[_Row],
    indexes: list[int],
    embeddings: np.ndarray,
    threshold: float,
    seed: int,
    progress: SelectionProgress | None = None,
) -> tuple[list[int], np.ndarray, set[int]]:
    """Deduplicate high-similarity embeddings with deterministic LSH buckets.

    The previous exact nearest-neighbor call compared every candidate with the
    complete candidate matrix. At 50,000 rows that is 2.5 billion cosine pairs.
    Random-hyperplane bands retain high recall around the configured 0.95
    threshold while keeping the candidate comparisons bounded.

    Buckets are keyed by ``(pair_key, code)``. LaBSE is cross-lingual, so without
    the pair in the key a Russian sentence and its English translation can land in
    the same bucket and clear a threshold meant for same-language duplicates.
    """
    import numpy as np

    if len(indexes) < 2:
        return indexes, embeddings, set()

    started = time.perf_counter()
    band_count = 12
    log.info("Embedding dedup: checking %s candidates (threshold=%s)", len(indexes), threshold)
    bits_per_band = 12
    rng = np.random.default_rng(seed)
    projections = rng.standard_normal(
        (embeddings.shape[1], band_count * bits_per_band), dtype=np.float32
    )
    signatures = embeddings @ projections >= 0
    powers = (1 << np.arange(bits_per_band, dtype=np.uint16)).reshape(1, -1)
    buckets: list[dict[tuple[str, int], list[int]]] = [
        defaultdict(list) for _ in range(band_count)
    ]
    dropped_positions: set[int] = set()

    for position in range(len(indexes)):
        pair = rows[indexes[position]].pair_key
        candidates: set[int] = set()
        codes: list[tuple[str, int]] = []
        for band in range(band_count):
            start = band * bits_per_band
            code = (pair, int((signatures[position, start : start + bits_per_band] * powers).sum()))
            codes.append(code)
            candidates.update(buckets[band].get(code, ()))

        if candidates:
            candidate_positions = np.fromiter(candidates, dtype=np.int64)
            similarities = embeddings[candidate_positions] @ embeddings[position]
            if bool(np.any(similarities >= threshold)):
                dropped_positions.add(position)
        if position not in dropped_positions:
            for band, code in enumerate(codes):
                buckets[band][code].append(position)

        completed = position + 1
        if completed % 1_000 == 0 or completed == len(indexes):
            if completed == len(indexes):
                log.info(
                    "Embedding dedup: finished %s candidates; removed %s",
                    len(indexes),
                    len(dropped_positions),
                )
            _emit(
                progress,
                "embedding_dedup",
                (
                    f"Embedding dedup checked {completed:,}/{len(indexes):,} candidates; "
                    f"removed {len(dropped_positions):,}"
                ),
                completed,
                len(indexes),
                time.perf_counter() - started,
            )

    kept_positions = [
        position for position in range(len(indexes)) if position not in dropped_positions
    ]
    return (
        [indexes[position] for position in kept_positions],
        embeddings[kept_positions],
        {indexes[position] for position in dropped_positions},
    )


def holdout_budget(config: Mapping[str, Any], corpus_rows: int | None) -> int | None:
    """Rows this policy may spend on document holdout, or None for unbounded.

    Holding out a document removes *every* chunk of it from training, so the cost
    of a benchmark is the size of the documents it touches, not the size of the
    benchmark. A cap on document *count* cannot express that: thirty documents
    cost 300 rows or 300,000 depending entirely on which thirty.
    """
    selection = config["selection"]
    budgets: list[int] = []
    absolute = selection.get("max_holdout_rows")
    if absolute:
        budgets.append(int(absolute))
    share = selection.get("max_holdout_share")
    if share and corpus_rows:
        budgets.append(int(float(share) * int(corpus_rows)))
    if not budgets:
        # Deployment-owned policy files predate these keys, so this is a warning
        # rather than a validation failure — but an unbounded holdout is how a
        # 399-row benchmark cost 241,525 rows, and it should never be silent.
        log.warning(
            "Document holdout is unbounded: neither selection.max_holdout_rows nor "
            "selection.max_holdout_share (with a known corpus size) is set. Holdout "
            "cost is limited only by max_test_documents, which does not bound rows."
        )
        return None
    return min(budgets)


def _document_costs(
    rows: list[_Row],
    by_document: Mapping[str, list[int]],
    document_sizes: Mapping[str, int] | None,
) -> dict[str, int]:
    """Rows each document would cost if held out.

    ``document_sizes`` counts the document across the whole corpus the holdout
    will be applied to. Without it the only available number is how often the
    document appears in the candidate pool, which understates the cost by the
    pool's sampling ratio — the failure that made a 10,000-row pool quarantine
    241,525 rows. The caller is expected to supply real sizes; the fallback keeps
    a partial ordering rather than pretending the cost is zero.
    """
    if document_sizes:
        return {
            document_id: int(document_sizes.get(document_id, len(indexes)))
            for document_id, indexes in by_document.items()
        }
    return {document_id: len(indexes) for document_id, indexes in by_document.items()}


def _candidate_documents(
    rows: list[_Row],
    indexes: list[int],
    config: dict[str, Any],
    rng: random.Random,
    document_sizes: Mapping[str, int] | None = None,
    corpus_rows: int | None = None,
) -> tuple[list[int], dict[str, Any]]:
    """Choose which documents a benchmark may draw from, within a row budget.

    Two bounds apply. ``max_test_documents`` caps how many documents a benchmark
    spans, which is about diversity. ``max_holdout_share`` / ``max_holdout_rows``
    cap how many rows holding them out will cost, which is about not destroying
    the training corpus. The row budget is the binding one in practice.

    Applied across pairs the caps become a race: whichever pair has more
    documents consumes the allowance and the other is left with none. Each pair
    gets its own, taken from that pair's merged policy.
    """
    if not indexes:
        return indexes, {}
    keys = {rows[index].pair_key for index in indexes}
    if len(keys) > 1:
        by_pair: dict[str, list[int]] = defaultdict(list)
        for index in indexes:
            by_pair[rows[index].pair_key].append(index)
        kept: set[int] = set()
        pair_reports: dict[str, Any] = {}
        for key, pair_indexes in by_pair.items():
            pair_kept, pair_report = _candidate_documents(
                rows,
                pair_indexes,
                resolve_pair_policy(config, key),
                rng,
                document_sizes,
                corpus_rows,
            )
            kept.update(pair_kept)
            pair_reports[key] = pair_report
        return [index for index in indexes if index in kept], {"by_pair": pair_reports}

    config = resolve_pair_policy(config, next(iter(keys)))
    maximum = config["selection"].get("max_test_documents")
    budget = holdout_budget(config, corpus_rows)
    report: dict[str, Any] = {
        "budget_rows": budget,
        "corpus_rows": corpus_rows,
        "document_sizes": "corpus" if document_sizes else "candidate_pool",
    }
    if not maximum and budget is None:
        return indexes, {**report, "applied": False, "reason": "no document cap or budget"}

    # Missing document IDs are represented as one synthetic document per row.
    # A document cap must not turn a requested 1,000-row evaluation set into
    # only 30 rows when the import did not provide document metadata.
    if all(rows[index].document_id.startswith("sample:") for index in indexes):
        return indexes, {**report, "applied": False, "reason": "no document metadata"}

    by_document: dict[str, list[int]] = defaultdict(list)
    for index in indexes:
        by_document[rows[index].document_id].append(index)
    costs = _document_costs(rows, by_document, document_sizes)
    report["documents_available"] = len(by_document)

    total_cost = sum(costs.values())
    if (not maximum or int(maximum) >= len(by_document)) and (
        budget is None or total_cost <= budget
    ):
        return indexes, {
            **report,
            "applied": False,
            "reason": "every candidate document fits",
            "documents_held": len(by_document),
            "estimated_rows": total_cost,
        }

    cap = int(maximum) if maximum else len(by_document)
    by_domain: dict[str, list[str]] = defaultdict(list)
    for document_id, document_indexes in by_document.items():
        by_domain[rows[document_indexes[0]].domain].append(document_id)
    domain_rows = Counter(rows[index].domain for index in indexes)
    slots = {
        domain: max(1, round(count / len(indexes) * cap)) for domain, count in domain_rows.items()
    }
    while sum(slots.values()) > cap:
        slots[max(slots, key=slots.get)] -= 1
    while sum(slots.values()) < cap:
        domain = max(by_domain, key=lambda item: len(by_domain[item]) - slots.get(item, 0))
        if len(by_domain[domain]) <= slots[domain]:
            break
        slots[domain] += 1

    def value(document_id: str) -> float:
        """How much benchmark signal this document offers."""
        document_rows = [rows[index] for index in by_document[document_id]]
        coverage = len({row.length_bucket for row in document_rows})
        hard_density = sum(sum(row.flags.values()) for row in document_rows) / len(document_rows)
        return coverage * 2 + hard_density

    def rank(document_id: str) -> tuple[float, float]:
        # Value per row spent, so a document that offers the same coverage for a
        # tenth of the corpus wins. Ties break deterministically on the seeded rng.
        return value(document_id) / max(costs[document_id], 1), rng.random() * 1e-6

    ranked = {
        domain: sorted(documents, key=rank, reverse=True)
        for domain, documents in by_domain.items()
    }
    # Round-robin so the budget is shared between domains instead of being
    # consumed by whichever one happens to be iterated first.
    positions = dict.fromkeys(ranked, 0)
    selected_documents: set[str] = set()
    spent = 0
    exhausted: set[str] = set()
    while len(selected_documents) < cap and len(exhausted) < len(ranked):
        for domain in sorted(ranked):
            if domain in exhausted or len(selected_documents) >= cap:
                continue
            position = positions[domain]
            documents = ranked[domain]
            if position >= len(documents) or len(
                [d for d in selected_documents if d in by_domain[domain]]
            ) >= slots.get(domain, 0):
                exhausted.add(domain)
                continue
            document_id = documents[position]
            positions[domain] = position + 1
            if budget is not None and spent + costs[document_id] > budget:
                # Skip this document but keep looking: a smaller one may still fit.
                if all(costs[d] + spent > budget for d in documents[position + 1 :]):
                    exhausted.add(domain)
                continue
            selected_documents.add(document_id)
            spent += costs[document_id]

    log.info(
        "Candidate documents: held %s of %s documents, ~%s rows (budget %s)",
        len(selected_documents),
        len(by_document),
        spent,
        budget,
    )
    return [index for index in indexes if rows[index].document_id in selected_documents], {
        **report,
        "applied": True,
        "documents_held": len(selected_documents),
        "estimated_rows": spent,
    }


def _proportional_targets(counts: Counter[str], total: int) -> dict[str, int]:
    """Split ``total`` across keys in proportion to their row counts.

    Used to give every language pair its own slice of the benchmark before
    domains compete. Without it, a large English batch consumes a combined
    en-fa + ru-fa reservation simply by outnumbering the Russian rows.
    """
    population = sum(counts.values())
    if not population or total <= 0:
        return dict.fromkeys(counts, 0)
    targets = {key: min(count, round(count / population * total)) for key, count in counts.items()}
    difference = total - sum(targets.values())
    order = sorted(counts, key=lambda key: (-counts[key], key))
    while difference and order:
        progressed = False
        for key in order:
            if difference > 0 and targets[key] < counts[key]:
                targets[key] += 1
                difference -= 1
                progressed = True
            elif difference < 0 and targets[key] > 0:
                targets[key] -= 1
                difference += 1
                progressed = True
            if difference == 0:
                break
        if not progressed:
            break
    return targets


def _domain_targets(rows: list[_Row], total: int, config: dict[str, Any]) -> dict[str, int]:
    counts = Counter(row.domain for row in rows)
    if not counts:
        return {}
    selection = config["selection"]
    if selection["domain_allocation"] == "flattened":
        alpha = float(selection["flatten_alpha"])
        shares = {
            domain: (1 - alpha) * count / len(rows) + alpha / len(counts)
            for domain, count in counts.items()
        }
    else:
        shares = {domain: count / len(rows) for domain, count in counts.items()}
    # The configured floor is per domain within one pair. Applied literally to a
    # small pair it would demand more rows than the pair's whole target, so it is
    # clamped to an even split of what this pair actually has to give.
    floor = min(int(selection["min_per_domain"]), max(1, total // len(counts)))
    targets = {
        domain: min(count, max(floor, round(shares[domain] * total)))
        for domain, count in counts.items()
    }
    difference = total - sum(targets.values())
    order = sorted(counts, key=lambda domain: (-counts[domain], domain))
    while difference and order:
        progressed = False
        for domain in order:
            if difference > 0 and targets[domain] < counts[domain]:
                targets[domain] += 1
                difference -= 1
                progressed = True
            elif difference < 0 and targets[domain] > 0:
                targets[domain] -= 1
                difference += 1
                progressed = True
            if difference == 0:
                break
        if not progressed:
            break
    return targets


def pair_targets(rows: list[_Row], total: int) -> dict[str, int]:
    """How much of the benchmark each language pair receives."""
    return _proportional_targets(Counter(row.pair_key for row in rows), total)


def _quotas(
    rows: list[_Row], total: int, config: dict[str, Any]
) -> dict[tuple[str, str, str], int]:
    """Allocate the benchmark hierarchically: language pair, then domain, then length.

    Each pair's slice is computed first and everything below it is resolved inside
    that slice, using that pair's own merged policy. A pair therefore cannot lose
    its representation to another pair's domain distribution or token density.
    """
    by_pair: dict[str, list[_Row]] = defaultdict(list)
    for row in rows:
        by_pair[row.pair_key].append(row)
    targets_by_pair = pair_targets(rows, total)

    quota: dict[tuple[str, str, str], int] = {}
    for key, pair_rows in by_pair.items():
        pair_total = targets_by_pair.get(key, 0)
        if pair_total <= 0:
            continue
        policy = resolve_pair_policy(config, key)
        selection = policy["selection"]
        shares = [float(value) for value in selection["length_bucket_shares"]]
        edges = selection["length_buckets"]
        labels = [f"len_{low}_{high}" for low, high in zip(edges[:-1], edges[1:], strict=True)]
        for domain, target in _domain_targets(pair_rows, pair_total, policy).items():
            available = Counter(row.length_bucket for row in pair_rows if row.domain == domain)
            wanted = {
                label: min(available[label], round(target * share))
                for label, share in zip(labels, shares, strict=True)
            }
            remaining = target - sum(wanted.values())
            while remaining:
                progressed = False
                for label in labels:
                    if wanted[label] < available[label]:
                        wanted[label] += 1
                        remaining -= 1
                        progressed = True
                    if remaining == 0:
                        break
                if not progressed:
                    break
            quota.update(
                {(key, domain, label): count for label, count in wanted.items() if count}
            )
    return quota


def _kcenter(
    indexes: list[int],
    vectors: dict[int, np.ndarray],
    count: int,
    rng: random.Random,
    dimensions: int,
    progress: SelectionProgress | None = None,
    label: str = "cell",
) -> list[int]:
    import numpy as np

    if count >= len(indexes):
        return indexes
    started = time.perf_counter()
    embeddings = np.vstack([vectors[index] for index in indexes])
    log.info("k-center '%s': selecting %s from %s", label, count, len(indexes))
    if dimensions > 0 and embeddings.shape[1] > dimensions:
        projection_rng = np.random.default_rng(rng.randrange(2**32))
        projection = projection_rng.standard_normal(
            (embeddings.shape[1], dimensions), dtype=np.float32
        )
        embeddings = embeddings @ projection
        embeddings = _normalize_embeddings(embeddings)
    start = rng.randrange(len(indexes))
    selected_positions = [start]
    minimum_distance = 1.0 - embeddings @ embeddings[start]
    report_every = max(1, count // 20)
    for iteration in range(count - 1):
        position = int(np.argmax(minimum_distance))
        selected_positions.append(position)
        minimum_distance = np.minimum(minimum_distance, 1.0 - embeddings @ embeddings[position])
        completed = iteration + 2
        if completed % report_every == 0 or completed == count:
            _emit(
                progress,
                "kcenter_diversity",
                f"k-center {label}: selected {completed:,}/{count:,}",
                completed,
                count,
                time.perf_counter() - started,
            )
    return [indexes[position] for position in selected_positions]


def _select(
    rows: list[_Row],
    vectors: dict[int, np.ndarray],
    candidates: list[int],
    total: int,
    config: dict[str, Any],
    rng: random.Random,
    progress: SelectionProgress | None = None,
) -> tuple[list[int], dict[tuple[str, str, str], int]]:
    quotas = _quotas([rows[index] for index in candidates], total, config)
    log.info(
        "Selection: computed %s pair/domain/bucket quotas for total %s", len(quotas), total
    )
    selected: list[int] = []
    for (key, domain, bucket), quota in quotas.items():
        cell = [
            index
            for index in candidates
            if rows[index].pair_key == key
            and rows[index].domain == domain
            and rows[index].length_bucket == bucket
        ]
        if config["diversity"]["strategy"] == "random":
            selected.extend(rng.sample(cell, min(quota, len(cell))))
        else:
            selected.extend(
                _kcenter(
                    cell,
                    vectors,
                    min(quota, len(cell)),
                    rng,
                    int(config["diversity"].get("projection_dimensions", 128)),
                    progress,
                    label=f"{key}/{domain}/{bucket}",
                )
            )
    return selected, quotas


def _feature_shares(config: dict[str, Any]) -> dict[str, float]:
    selection = config["selection"]
    shares = dict(selection["hard_phenomena_min_share"])
    shares[FEATURE_RARE_TERM] = selection["rare_term_min_share"]
    return shares


def unsupported_features(rows: list[_Row], config: dict[str, Any]) -> dict[str, list[str]]:
    """Quota features each pair present in ``rows`` cannot honestly measure."""
    shares = _feature_shares(config)
    report: dict[str, list[str]] = {}
    for row in rows:
        if row.pair_key in report:
            continue
        missing = sorted(
            flag
            for flag in shares
            if flag in ALL_FEATURES and not row.profile.supports(flag)
        )
        if missing:
            report[row.pair_key] = missing
    return report


def _top_up_hard_phenomena(
    rows: list[_Row],
    vectors: dict[int, np.ndarray],
    candidates: list[int],
    selected: list[int],
    total: int,
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
) -> list[int]:
    """Raise hard-phenomena coverage, once per feature, within the pairs that have it.

    Two constraints beyond the original stage. A feature is only pursued inside
    language pairs whose profile can measure it: an acronym quota applied to a
    caseless source language would evict good rows to chase a count that the
    detector can never reach. And both the incoming and the evicted rows come from
    the same restricted set, so topping up one pair cannot spend another pair's
    allocation.
    """
    import numpy as np

    shares = _feature_shares(config)
    chosen = set(selected)
    started = time.perf_counter()
    log.info("Hard-phenomena top-up: selected=%s, flags=%s", len(chosen), ", ".join(shares))
    for flag, share in shares.items():
        supporting = {
            rows[index].pair_key for index in chosen if rows[index].profile.supports(flag)
        }
        if not supporting:
            log.info("Top-up %s: unsupported by every selected language pair; skipped", flag)
            _emit(
                progress,
                "hard_phenomena_topup",
                f"Top-up {flag}: not measurable for any selected language pair; skipped",
            )
            continue

        scope = [index for index in chosen if rows[index].pair_key in supporting]
        required = math.ceil(float(share) * len(scope))
        current = sum(rows[index].flags[flag] for index in scope)
        deficit = max(0, required - current)
        options = [
            index
            for index in candidates
            if index not in chosen
            and rows[index].flags[flag]
            and rows[index].pair_key in supporting
        ]
        replacement_count = min(deficit, len(options), len(scope))
        if replacement_count:
            scope_list = sorted(scope)
            scope_vectors = np.vstack([vectors[index] for index in scope_list])
            option_scores: list[float] = []
            for start in range(0, len(options), 1_024):
                block = options[start : start + 1_024]
                block_vectors = np.vstack([vectors[index] for index in block])
                option_scores.extend((block_vectors @ scope_vectors.T).max(axis=1).tolist())
            incoming = [
                index
                for _, index in sorted(zip(option_scores, options, strict=True))[
                    :replacement_count
                ]
            ]

            similarities = scope_vectors @ scope_vectors.T
            np.fill_diagonal(similarities, -np.inf)
            redundancy = similarities.max(axis=1)
            removable = [
                index
                for _, _, index in sorted(
                    (
                        any(rows[index].flags.values()),
                        -float(redundancy[position]),
                        index,
                    )
                    for position, index in enumerate(scope_list)
                )[:replacement_count]
            ]
            chosen.difference_update(removable)
            chosen.update(incoming)
        log.info(
            "Top-up %s: pairs=%s required=%s had=%s replaced=%s",
            flag,
            ",".join(sorted(supporting)),
            required,
            current,
            replacement_count,
        )
        _emit(
            progress,
            "hard_phenomena_topup",
            (
                f"Top-up {flag}: required {required:,}, had {current:,}, "
                f"replaced {replacement_count:,}"
            ),
            min(required, current + replacement_count),
            required,
            time.perf_counter() - started,
        )
    return sorted(chosen)



def _split_dev_test(
    rows: list[_Row], selected: list[int], config: dict[str, Any], rng: random.Random
) -> set[int]:
    if not config["splits"]["dev_test_split"]:
        return set()
    # Stratified by pair as well as domain and length, so a small pair does not
    # land entirely in dev or entirely in test.
    by_cell: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index in selected:
        row = rows[index]
        by_cell[(row.pair_key, row.domain, row.length_bucket)].append(index)
    dev: set[int] = set()
    for indexes in by_cell.values():
        count = round(len(indexes) * float(config["splits"]["dev_fraction"]))
        if count:
            dev.update(rng.sample(indexes, count))
    return dev


def _gold_subset(
    rows: list[_Row],
    selected: list[int],
    config: dict[str, Any],
    rng: random.Random,
) -> set[int]:
    if not config["gold_subset"]["enabled"] or not selected:
        return set()
    # Human verification budget is split across (pair, domain) cells so every
    # represented pair receives some manually verified rows.
    by_cell: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index in selected:
        by_cell[(rows[index].pair_key, rows[index].domain)].append(index)
    per_cell = max(1, int(config["gold_subset"]["size"]) // len(by_cell))
    gold: list[int] = []
    for indexes in by_cell.values():
        gold.extend(rng.sample(indexes, min(per_cell, len(indexes))))
    return set(gold[: int(config["gold_subset"]["size"])])


def _selection_hash(rows: list[_Row], indexes: set[int]) -> str:
    ids = ",".join(str(rows[index].sample_id) for index in sorted(indexes))
    return hashlib.sha256(ids.encode("utf-8")).hexdigest()


def select(
    rows: pl.DataFrame,
    target: int,
    seed: int,
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
    *,
    document_sizes: Mapping[str, int] | None = None,
    corpus_rows: int | None = None,
) -> SelectionResult:
    """Run every source-pipeline stage and translate its outputs to registry state."""
    log.info("Starting Contamination Safe 'select' process...")
    selection_started = time.perf_counter()
    # Before the model loads and before a single row is embedded: a bad policy
    # must not be discovered hours into an import.
    validate_policy(config)
    records = _records(rows)
    if target <= 0 or not records:
        return SelectionResult(
            [], [], [], [], {}, {"strategy": "contamination_safe", "selected": 0}
        )
    _require_evaluation_group()
    import numpy as np

    stage_started = time.perf_counter()
    log.info(f"Annotating {len(records):,} candidate rows")
    _emit(progress, "annotating", f"Annotating {len(records):,} candidate rows", 0, len(records))
    annotated = _annotate(records, config)
    log.info(f"Annotated {len(records):,} candidate rows")
    _emit(
        progress,
        "annotating",
        f"Annotated {len(records):,} candidate rows",
        len(records),
        len(records),
        time.perf_counter() - stage_started,
    )
    active = list(range(len(annotated)))
    quarantined: set[int] = set()
    dedup_report: dict[str, int] = {"exact": 0, "minhash": 0, "embedding": 0}
    dedup = config["dedup"]
    log.info(f"Dedup is {'enabled' if dedup['enabled'] else 'disabled'}")
    if dedup["enabled"]:
        stage_started = time.perf_counter()
        log.info(f"Exact-deduplicating {len(active):,} candidates")
        _emit(progress, "exact_dedup", f"Exact-deduplicating {len(active):,} candidates")
        active, removed = _dedup_exact(annotated, active)
        quarantined.update(removed)
        dedup_report["exact"] = len(removed)
        _emit(
            progress,
            "exact_dedup",
            f"Exact dedup kept {len(active):,}; removed {len(removed):,}",
            len(active),
            len(records),
            time.perf_counter() - stage_started,
        )
        if dedup["minhash_enabled"]:
            active, removed = _dedup_minhash(
                annotated,
                active,
                float(dedup["minhash_threshold"]),
                progress,
            )
            quarantined.update(removed)
            dedup_report["minhash"] = len(removed)

    embeddings = _compute_embeddings(
        [annotated[index] for index in active],
        config,
        progress,
    )
    if embeddings.shape[0] != len(active):
        log.warning(
            "LaBSE produced %d embeddings for %d active rows; "
            "trailing rows will be excluded from selection",
            embeddings.shape[0],
            len(active),
        )
        active = active[: embeddings.shape[0]]
    if dedup["enabled"] and dedup["embedding_dedup_enabled"]:
        active, embeddings, removed = _dedup_embeddings(
            annotated,
            active,
            embeddings,
            float(dedup["embedding_cosine_threshold"]),
            seed,
            progress,
        )
        quarantined.update(removed)
        dedup_report["embedding"] = len(removed)
    vectors = {index: embeddings[position] for position, index in enumerate(active)}

    rng = random.Random(seed)
    stage_started = time.perf_counter()
    _emit(progress, "candidate_documents", "Applying candidate-document limits")
    candidates, holdout_report = _candidate_documents(
        annotated, active, config, rng, document_sizes, corpus_rows
    )
    _emit(
        progress,
        "candidate_documents",
        f"Document filtering kept {len(candidates):,}/{len(active):,} candidates",
        len(candidates),
        len(active),
        time.perf_counter() - stage_started,
    )
    selection_target = min(target, len(candidates))
    stage_started = time.perf_counter()
    _emit(
        progress,
        "diversity_selection",
        f"Selecting {selection_target:,} diverse evaluation rows",
        0,
        selection_target,
    )
    selected, quotas = _select(
        annotated,
        vectors,
        candidates,
        selection_target,
        config,
        rng,
        progress,
    )
    selected = _top_up_hard_phenomena(
        annotated,
        vectors,
        candidates,
        selected,
        selection_target,
        config,
        progress,
    )
    _emit(
        progress,
        "diversity_selection",
        f"Selected {len(selected):,} evaluation rows",
        len(selected),
        selection_target,
        time.perf_counter() - stage_started,
    )
    selected_set = set(selected)

    contamination = config["contamination"]
    held_documents: set[str] = set()
    if contamination["document_level_holdout"]:
        held_documents = {annotated[index].document_id for index in selected_set}
        quarantined.update(
            index
            for index, row in enumerate(annotated)
            if index not in selected_set and row.document_id in held_documents
        )
    if contamination["near_dup_check_enabled"] and selected_set:
        stage_started = time.perf_counter()
        threshold = float(contamination["near_dup_cosine_threshold"])
        # Both sides of this comparison are source embeddings, so it stays within
        # one pair: the threshold is calibrated for same-language near-duplicates,
        # and LaBSE would otherwise score a Russian candidate against its English
        # translation high enough to quarantine a perfectly good training row.
        selected_by_pair: dict[str, list[int]] = defaultdict(list)
        for index in selected_set:
            selected_by_pair[annotated[index].pair_key].append(index)
        pool_by_pair: dict[str, list[int]] = defaultdict(list)
        for index in active:
            if index not in selected_set and index not in quarantined:
                pool_by_pair[annotated[index].pair_key].append(index)

        checked = 0
        pool_total = sum(len(pool) for pool in pool_by_pair.values())
        for key, pool in pool_by_pair.items():
            reserved = selected_by_pair.get(key)
            if not reserved:
                continue
            selected_embeddings = np.vstack([vectors[index] for index in reserved])
            log.info(
                "Candidate scan [%s]: %s pool rows vs %s selected (threshold=%s)",
                key,
                len(pool),
                len(reserved),
                threshold,
            )
            for start in range(0, len(pool), 2048):
                block = pool[start : start + 2048]
                similarities = (
                    np.vstack([vectors[index] for index in block]) @ selected_embeddings.T
                )
                quarantined.update(
                    index
                    for index, similarity in zip(
                        block, similarities.max(axis=1), strict=True
                    )
                    if similarity >= threshold
                )
                checked += len(block)
                _emit(
                    progress,
                    "candidate_contamination_scan",
                    (
                        f"Checked {checked:,}/{pool_total:,} candidates against "
                        "their own pair's selected set"
                    ),
                    checked,
                    pool_total,
                    time.perf_counter() - stage_started,
                )

    dev = _split_dev_test(annotated, selected, config, rng)
    gold = _gold_subset(annotated, selected, config, rng)
    log.info(
        "Selection done: reserved=%s quarantined=%s dev=%s gold=%s",
        len(selected_set),
        len(quarantined),
        len(dev),
        len(gold),
    )
    flags = {flag: sum(annotated[index].flags[flag] for index in selected_set) for flag in ALL_FEATURES}
    selected_rows = [annotated[index] for index in selected_set]
    candidate_rows = [annotated[index] for index in candidates]
    unsupported = unsupported_features(selected_rows, config)
    report = {
        "strategy": "contamination_safe",
        "config": config,
        "config_fingerprint": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "selected": len(selected_set),
        "quarantined": len(quarantined),
        "candidate_rows": len(candidates),
        "held_documents": len(held_documents),
        # What holding those documents actually costs the training corpus. The
        # count of held documents says nothing on its own: thirty documents cost
        # 300 rows or 300,000 depending on which thirty.
        "holdout": {
            **holdout_report,
            "actual_rows": (
                sum(int(document_sizes.get(document, 0)) for document in held_documents)
                if document_sizes
                else None
            ),
            "rows_per_reserved_row": (
                round(
                    sum(int(document_sizes.get(document, 0)) for document in held_documents)
                    / len(selected_set),
                    1,
                )
                if document_sizes and selected_set
                else None
            ),
        },
        "dedup_removed": dedup_report,
        "embedding_dedup_method": "random_hyperplane_lsh",
        "embeddings": {
            "backend": "labse" if config["embeddings"]["enabled"] else "tfidf_svd",
            "model": config["embeddings"]["model"] if config["embeddings"]["enabled"] else None,
        },
        "features": flags,
        # Features a pair's language profile cannot measure are omitted from its
        # quotas rather than reported as a satisfied zero.
        "unsupported_features": unsupported,
        "quotas": {
            f"{key}|{domain}:{bucket}": count for (key, domain, bucket), count in quotas.items()
        },
        "language_pairs": dict(Counter(row.pair_key for row in selected_rows)),
        "pair_targets": pair_targets(candidate_rows, selection_target),
        "document_namespace": _document_namespace(config),
        "domains": dict(Counter(row.domain for row in selected_rows)),
        "length_buckets": dict(Counter(row.length_bucket for row in selected_rows)),
        "splits": {"dev": len(dev), "test": len(selected_set - dev)},
        "human_verify": len(gold),
        "manifest": {"selected_ids_sha256": _selection_hash(annotated, selected_set)},
        "elapsed_seconds": round(time.perf_counter() - selection_started, 3),
    }
    _emit(
        progress,
        "selection_complete",
        (
            f"Selection completed in {time.perf_counter() - selection_started:.1f}s; "
            f"reserved {len(selected_set):,}, quarantined {len(quarantined):,}"
        ),
        len(selected_set),
        selection_target,
        time.perf_counter() - selection_started,
    )
    return SelectionResult(
        reserved_ids=[annotated[index].sample_id for index in sorted(selected_set)],
        quarantined_ids=[annotated[index].sample_id for index in sorted(quarantined)],
        dev_ids=[annotated[index].sample_id for index in sorted(dev)],
        gold_ids=[annotated[index].sample_id for index in sorted(gold)],
        annotations={
            row.sample_id: {
                "n_tokens": row.n_tokens,
                "length_bucket": row.length_bucket,
                "has_math": row.flags["has_math"],
                "has_numbers_units": row.flags["has_numbers_units"],
                "has_acronyms": row.flags["has_acronyms"],
                "has_mixed_script": row.flags["has_mixed_script"],
                "rare_term_score": row.rare_term_score,
                "is_rare_term": row.flags["is_rare_term"],
            }
            for row in annotated
        },
        report=report,
    )
