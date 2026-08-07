"""Full contamination-safe MT evaluation reservation pipeline.

This ports the selection stages from the supplied ``build_test_set.py`` into
immutable batch ingestion.  Rather than emitting duplicate train/dev/test CSVs,
the registry retains split and gold annotations in batch Parquet, reserves only
evaluation rows, and quarantines every row excluded to prevent leakage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from core.config import settings

SelectionProgress = Callable[[str, str, int | None, int | None, float | None], None]
log = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"\w+", re.UNICODE)
MATH_RE = re.compile(
    r"(\$[^$]+\$|\\\(|\\\[|\\frac|\\sum|\\int|\\alpha|\\beta|\\gamma|\\lambda|\\sigma"
    r"|[=<>≤≥±∓×÷√∞∈∉⊂⊆∪∩∑∏∫∂∇Δ∆]|\b[a-zA-Z]\s*\^\s*[0-9n]|\b[xyz]\s*=)"
)
NUM_UNIT_RE = re.compile(
    r"\b\d+(\.\d+)?\s*(%|mm|cm|km|kg|mg|g|ml|l|s|ms|hz|khz|mhz|ghz|k|°c|°f|"
    r"kpa|mpa|mol|ppm|db|nm|µm|um|ev|kev|mev|gev|w|kw|mw|v|mv|a|ma)\b",
    re.IGNORECASE,
)
STAT_RE = re.compile(r"\b[pP]\s*[<>=]\s*0?\.\d+|\bn\s*=\s*\d+|\bCI\b|\br\s*=\s*[-0.]|\bF\(\d")
ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}s?\b")
PERSIAN_CHAR_RE = re.compile(r"[\u0600-\u06FF]")
LATIN_CHAR_RE = re.compile(r"[A-Za-z]")


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
class ContaminationReference:
    """Selected evaluation rows prepared for a bounded full-corpus scan."""

    selected_ids: set[int]
    source_keys: set[str]
    document_ids: set[str]
    embeddings: Any
    near_duplicate_shingles: set[str]
    encoder: Any = None


@dataclass(frozen=True)
class ContaminationScanResult:
    quarantined_ids: set[int]
    exact_or_document_rows: int
    semantic_prefilter_rows: int
    semantic_checked_rows: int


_MODEL_CACHE: dict[tuple[str, str], Any] = {}


def _emit(
    progress: SelectionProgress | None,
    stage: str,
    message: str,
    completed: int | None = None,
    total: int | None = None,
    elapsed: float | None = None,
) -> None:
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


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(unicodedata.normalize("NFKC", text).lower())


def _document_id(row: dict[str, Any], separator: str) -> str:
    value = row.get("document_id")
    if value in (None, ""):
        return f"sample:{row['sample_id']}"
    return str(value).split(separator, 1)[0]


def _bucket(token_count: int, edges: list[int]) -> str:
    for low, high in zip(edges, edges[1:], strict=True):
        if low <= token_count < high:
            return f"len_{low}_{high}"
    return f"len_{edges[-2]}_{edges[-1]}"


def _annotate(records: list[dict[str, Any]], config: dict[str, Any]) -> list[_Row]:
    selection = config["selection"]
    token_lists = [_tokens(str(record["source_text"])) for record in records]
    frequency: Counter[str] = Counter()
    for tokens in token_lists:
        frequency.update(set(tokens))
    rare_max = int(config["features"]["rare_token_max_count"])
    rare_scores = [
        sum(frequency[token] <= rare_max for token in tokens) / len(tokens) if tokens else 0.0
        for tokens in token_lists
    ]
    ordered = sorted(rare_scores)
    quantile = float(selection["rare_term_top_quantile"])
    rare_threshold = ordered[min(len(ordered) - 1, int(quantile * len(ordered)))]
    edges = [int(edge) for edge in selection["length_buckets"]]
    separator = str(config.get("input", {}).get("id_separator", ":"))

    annotated: list[_Row] = []
    for record, tokens, rare_score in zip(records, token_lists, rare_scores, strict=True):
        source = str(record["source_text"])
        target = str(record["target_text"])
        annotated.append(
            _Row(
                sample_id=int(record["sample_id"]),
                source=source,
                target=target,
                domain=str(record.get("domain") or "unknown"),
                document_id=_document_id(record, separator),
                n_tokens=len(tokens),
                length_bucket=_bucket(len(tokens), edges),
                rare_term_score=rare_score,
                flags={
                    "has_math": bool(MATH_RE.search(source)),
                    "has_numbers_units": bool(
                        NUM_UNIT_RE.search(source) or STAT_RE.search(source)
                    ),
                    "has_acronyms": bool(ACRONYM_RE.search(source)),
                    "has_mixed_script": bool(
                        LATIN_CHAR_RE.search(target) or PERSIAN_CHAR_RE.search(source)
                    ),
                    "is_rare_term": rare_score >= rare_threshold,
                },
            )
        )
    return annotated


def _shingles(text: str, size: int = 3) -> set[str]:
    tokens = _tokens(text)
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
    seen: set[str] = set()
    kept: list[int] = []
    dropped: set[int] = set()
    for index in indexes:
        key = " ".join(_tokens(rows[index].source))
        if key and key not in seen:
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

    lsh = MinHashLSH(threshold=threshold, num_perm=128)
    kept: list[int] = []
    dropped: set[int] = set()
    started = time.perf_counter()
    for position, index in enumerate(indexes, start=1):
        signature = MinHash(num_perm=128)
        for shingle in _shingles(rows[index].source):
            signature.update(shingle.encode("utf-8"))
        if lsh.query(signature):
            dropped.add(index)
        else:
            lsh.insert(str(index), signature)
            kept.append(index)
        if position % 1_000 == 0 or position == len(indexes):
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
    digest = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()
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
        embeddings = _encode_labse(texts, config, progress)
    else:
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
    return embeddings


def prepare_contamination_reference(
    selected_rows: pl.DataFrame,
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
) -> ContaminationReference:
    """Prepare reserved rows for streaming checks against the complete corpus."""
    records = selected_rows.select("sample_id", "source_text", "document_id").to_dicts()
    separator = str(config.get("input", {}).get("id_separator", ":"))
    selected_ids = {int(record["sample_id"]) for record in records}
    source_keys = {" ".join(_tokens(str(record["source_text"]))) for record in records}
    document_ids = (
        {_document_id(record, separator) for record in records}
        if config["contamination"]["document_level_holdout"]
        else set()
    )
    texts = [str(record["source_text"]) for record in records]
    prefilter_size = int(config["contamination"].get("semantic_prefilter_shingle_size", 5))
    near_duplicate_shingles = {
        shingle for text in texts for shingle in _shingles(text, prefilter_size)
    }

    if config["embeddings"]["enabled"]:
        embeddings = _encode_labse(
            texts,
            config,
            progress,
            stage="reference_embeddings",
        )
        encoder = None
    else:
        from sklearn.feature_extraction.text import TfidfVectorizer

        encoder = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=50_000)
        embeddings = encoder.fit_transform(texts)

    return ContaminationReference(
        selected_ids=selected_ids,
        source_keys=source_keys,
        document_ids=document_ids,
        embeddings=embeddings,
        near_duplicate_shingles=near_duplicate_shingles,
        encoder=encoder,
    )


def scan_full_corpus_contamination(
    rows: pl.DataFrame,
    reference: ContaminationReference,
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
) -> ContaminationScanResult:
    """Find train/evaluation leakage in one bounded frame.

    Every source row is checked for selected-document membership and exact
    source duplication. Rows sharing a lexical shingle with reserved text are
    then verified by embedding similarity. Memory stays proportional to one
    ingestion frame and the selected set.
    """
    import numpy as np

    records = rows.select("sample_id", "source_text", "document_id").to_dicts()
    separator = str(config.get("input", {}).get("id_separator", ":"))
    contamination = config["contamination"]
    contaminated: set[int] = set()
    candidates: list[dict[str, Any]] = []
    prefilter_size = int(contamination.get("semantic_prefilter_shingle_size", 5))
    started = time.perf_counter()
    for position, record in enumerate(records, start=1):
        sample_id = int(record["sample_id"])
        if sample_id in reference.selected_ids:
            continue
        source_key = " ".join(_tokens(str(record["source_text"])))
        if (
            source_key in reference.source_keys
            or _document_id(record, separator) in reference.document_ids
        ):
            contaminated.add(sample_id)
        elif (
            _shingles(str(record["source_text"]), prefilter_size)
            & reference.near_duplicate_shingles
        ):
            candidates.append(record)
        if position % 5_000 == 0 or position == len(records):
            _emit(
                progress,
                "full_corpus_prefilter",
                (
                    f"Prefiltered {position:,}/{len(records):,} shard rows; "
                    f"{len(candidates):,} require semantic verification"
                ),
                position,
                len(records),
                time.perf_counter() - started,
            )

    if not contamination["near_dup_check_enabled"] or not candidates:
        return ContaminationScanResult(
            contaminated,
            exact_or_document_rows=len(contaminated),
            semantic_prefilter_rows=len(candidates),
            semantic_checked_rows=0,
        )

    texts = [str(record["source_text"]) for record in candidates]
    exact_or_document_rows = len(contaminated)
    if config["embeddings"]["enabled"]:
        vectors = _encode_labse(
            texts,
            config,
            progress,
            stage="full_corpus_embeddings",
        )
        selected = reference.embeddings
        if vectors.shape[0] != len(candidates):
            log.warning(
                "LaBSE produced %d embeddings for %d texts; trailing candidates skipped",
                vectors.shape[0],
                len(candidates),
            )
            candidates = candidates[: vectors.shape[0]]
        for start in range(0, len(candidates), 512):
            similarities = vectors[start : start + 512] @ selected.T
            maximum = similarities.max(axis=1)
            contaminated.update(
                int(record["sample_id"])
                for record, similarity in zip(
                    candidates[start : start + 512], maximum, strict=True
                )
                if similarity >= float(contamination["near_dup_cosine_threshold"])
            )
    else:
        vectors = reference.encoder.transform(texts)
        if vectors.shape[0] != len(candidates):
            log.warning(
                "TF-IDF produced %d vectors for %d texts; trailing candidates skipped",
                vectors.shape[0],
                len(candidates),
            )
            candidates = candidates[: vectors.shape[0]]
        for start in range(0, len(candidates), 512):
            similarities = (vectors[start : start + 512] @ reference.embeddings.T).toarray()
            maximum = np.asarray(similarities.max(axis=1)).ravel()
            contaminated.update(
                int(record["sample_id"])
                for record, similarity in zip(
                    candidates[start : start + 512], maximum, strict=True
                )
                if similarity >= float(contamination["near_dup_cosine_threshold"])
            )
    return ContaminationScanResult(
        contaminated,
        exact_or_document_rows=exact_or_document_rows,
        semantic_prefilter_rows=len(candidates),
        semantic_checked_rows=len(candidates),
    )


def _dedup_embeddings(
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
    """
    import numpy as np

    if len(indexes) < 2:
        return indexes, embeddings, set()

    started = time.perf_counter()
    band_count = 12
    bits_per_band = 12
    rng = np.random.default_rng(seed)
    projections = rng.standard_normal(
        (embeddings.shape[1], band_count * bits_per_band), dtype=np.float32
    )
    signatures = embeddings @ projections >= 0
    powers = (1 << np.arange(bits_per_band, dtype=np.uint16)).reshape(1, -1)
    buckets: list[dict[int, list[int]]] = [defaultdict(list) for _ in range(band_count)]
    dropped_positions: set[int] = set()

    for position in range(len(indexes)):
        candidates: set[int] = set()
        codes: list[int] = []
        for band in range(band_count):
            start = band * bits_per_band
            code = int((signatures[position, start : start + bits_per_band] * powers).sum())
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


def _candidate_documents(
    rows: list[_Row], indexes: list[int], config: dict[str, Any], rng: random.Random
) -> list[int]:
    maximum = config["selection"].get("max_test_documents")
    if not maximum:
        return indexes
    # Missing document IDs are represented as one synthetic document per row.
    # A document cap must not turn a requested 1,000-row evaluation set into
    # only 30 rows when the import did not provide document metadata.
    if all(rows[index].document_id.startswith("sample:") for index in indexes):
        return indexes
    by_document: dict[str, list[int]] = defaultdict(list)
    for index in indexes:
        by_document[rows[index].document_id].append(index)
    if int(maximum) >= len(by_document):
        return indexes

    by_domain: dict[str, list[str]] = defaultdict(list)
    for document_id, document_indexes in by_document.items():
        by_domain[rows[document_indexes[0]].domain].append(document_id)
    domain_rows = Counter(rows[index].domain for index in indexes)
    slots = {
        domain: max(1, round(count / len(indexes) * int(maximum)))
        for domain, count in domain_rows.items()
    }
    while sum(slots.values()) > int(maximum):
        slots[max(slots, key=slots.get)] -= 1
    while sum(slots.values()) < int(maximum):
        domain = max(by_domain, key=lambda item: len(by_domain[item]) - slots.get(item, 0))
        if len(by_domain[domain]) <= slots[domain]:
            break
        slots[domain] += 1

    selected_documents: set[str] = set()
    for domain, documents in by_domain.items():
        def score(document_id: str) -> tuple[float, float]:
            document_rows = [rows[index] for index in by_document[document_id]]
            coverage = len({row.length_bucket for row in document_rows})
            hard_density = (
                sum(sum(row.flags.values()) for row in document_rows) / len(document_rows)
            )
            return coverage * 2 + hard_density, rng.random() * 1e-6

        selected_documents.update(
            sorted(documents, key=score, reverse=True)[: slots.get(domain, 0)]
        )
    return [index for index in indexes if rows[index].document_id in selected_documents]


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
    targets = {
        domain: min(count, max(int(selection["min_per_domain"]), round(shares[domain] * total)))
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


def _quotas(rows: list[_Row], total: int, config: dict[str, Any]) -> dict[tuple[str, str], int]:
    targets = _domain_targets(rows, total, config)
    shares = [float(value) for value in config["selection"]["length_bucket_shares"]]
    edges = config["selection"]["length_buckets"]
    labels = [f"len_{low}_{high}" for low, high in zip(edges, edges[1:], strict=True)]
    quota: dict[tuple[str, str], int] = {}
    for domain, target in targets.items():
        available = Counter(row.length_bucket for row in rows if row.domain == domain)
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
        quota.update({(domain, label): count for label, count in wanted.items() if count})
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
) -> tuple[list[int], dict[tuple[str, str], int]]:
    quotas = _quotas([rows[index] for index in candidates], total, config)
    selected: list[int] = []
    for (domain, bucket), quota in quotas.items():
        cell = [
            index
            for index in candidates
            if rows[index].domain == domain and rows[index].length_bucket == bucket
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
                    label=f"{domain}/{bucket}",
                )
            )
    return selected, quotas


def _top_up_hard_phenomena(
    rows: list[_Row],
    vectors: dict[int, np.ndarray],
    candidates: list[int],
    selected: list[int],
    total: int,
    config: dict[str, Any],
    progress: SelectionProgress | None = None,
) -> list[int]:
    import numpy as np

    shares = dict(config["selection"]["hard_phenomena_min_share"])
    shares["is_rare_term"] = config["selection"]["rare_term_min_share"]
    chosen = set(selected)
    started = time.perf_counter()
    for flag, share in shares.items():
        required = math.ceil(float(share) * total)
        current = sum(rows[index].flags[flag] for index in chosen)
        deficit = max(0, required - current)
        options = [
            index
            for index in candidates
            if index not in chosen and rows[index].flags[flag]
        ]
        replacement_count = min(deficit, len(options), len(chosen))
        if replacement_count:
            chosen_list = sorted(chosen)
            chosen_vectors = np.vstack([vectors[index] for index in chosen_list])
            option_scores: list[float] = []
            for start in range(0, len(options), 1_024):
                block = options[start : start + 1_024]
                block_vectors = np.vstack([vectors[index] for index in block])
                option_scores.extend((block_vectors @ chosen_vectors.T).max(axis=1).tolist())
            incoming = [
                index
                for _, index in sorted(zip(option_scores, options, strict=True))[
                    :replacement_count
                ]
            ]

            similarities = chosen_vectors @ chosen_vectors.T
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
                    for position, index in enumerate(chosen_list)
                )[:replacement_count]
            ]
            chosen.difference_update(removable)
            chosen.update(incoming)
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
    by_cell: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index in selected:
        by_cell[(rows[index].domain, rows[index].length_bucket)].append(index)
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
    by_domain: dict[str, list[int]] = defaultdict(list)
    for index in selected:
        by_domain[rows[index].domain].append(index)
    per_domain = max(1, int(config["gold_subset"]["size"]) // len(by_domain))
    gold: list[int] = []
    for indexes in by_domain.values():
        gold.extend(rng.sample(indexes, min(per_domain, len(indexes))))
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
) -> SelectionResult:
    """Run every source-pipeline stage and translate its outputs to registry state."""
    selection_started = time.perf_counter()
    records = rows.select(
        "sample_id", "source_text", "target_text", "domain", "document_id"
    ).to_dicts()
    if target <= 0 or not records:
        return SelectionResult(
            [], [], [], [], {}, {"strategy": "contamination_safe", "selected": 0}
        )
    _require_evaluation_group()
    import numpy as np

    stage_started = time.perf_counter()
    _emit(progress, "annotating", f"Annotating {len(records):,} candidate rows", 0, len(records))
    annotated = _annotate(records, config)
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
    if dedup["enabled"]:
        stage_started = time.perf_counter()
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
    candidates = _candidate_documents(annotated, active, config, rng)
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
        pool = [index for index in active if index not in selected_set and index not in quarantined]
        selected_embeddings = np.vstack([vectors[index] for index in selected_set])
        for start in range(0, len(pool), 2048):
            block = pool[start : start + 2048]
            similarities = np.vstack([vectors[index] for index in block]) @ selected_embeddings.T
            quarantined.update(
                index
                for index, similarity in zip(
                    block, similarities.max(axis=1), strict=True
                )
                if similarity >= threshold
            )
            completed = min(start + 2048, len(pool))
            _emit(
                progress,
                "candidate_contamination_scan",
                f"Checked {completed:,}/{len(pool):,} candidates against the selected set",
                completed,
                len(pool),
                time.perf_counter() - stage_started,
            )

    dev = _split_dev_test(annotated, selected, config, rng)
    gold = _gold_subset(annotated, selected, config, rng)
    flags = {
        flag: sum(annotated[index].flags[flag] for index in selected_set)
        for flag in (
            "has_math",
            "has_numbers_units",
            "has_acronyms",
            "has_mixed_script",
            "is_rare_term",
        )
    }
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
        "dedup_removed": dedup_report,
        "embedding_dedup_method": "random_hyperplane_lsh",
        "embeddings": {
            "backend": "labse" if config["embeddings"]["enabled"] else "tfidf_svd",
            "model": config["embeddings"]["model"] if config["embeddings"]["enabled"] else None,
        },
        "features": flags,
        "quotas": {f"{domain}:{bucket}": count for (domain, bucket), count in quotas.items()},
        "domains": dict(Counter(annotated[index].domain for index in selected_set)),
        "length_buckets": dict(Counter(annotated[index].length_bucket for index in selected_set)),
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
