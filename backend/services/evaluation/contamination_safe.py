"""Full contamination-safe MT evaluation reservation pipeline.

This ports the selection stages from the supplied ``build_test_set.py`` into
immutable batch ingestion.  Rather than emitting duplicate train/dev/test CSVs,
the registry retains split and gold annotations in batch Parquet, reserves only
evaluation rows, and quarantines every row excluded to prevent leakage.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from datasketch import MinHash, MinHashLSH
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors

from core.config import settings

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
    rows: list[_Row], indexes: list[int], threshold: float
) -> tuple[list[int], set[int]]:
    lsh = MinHashLSH(threshold=threshold, num_perm=128)
    kept: list[int] = []
    dropped: set[int] = set()
    for index in indexes:
        signature = MinHash(num_perm=128)
        for shingle in _shingles(rows[index].source):
            signature.update(shingle.encode("utf-8"))
        if lsh.query(signature):
            dropped.add(index)
        else:
            lsh.insert(str(index), signature)
            kept.append(index)
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
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (embeddings / norms).astype(np.float32)


def _tfidf_embeddings(texts: list[str]) -> np.ndarray:
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=50_000)
    matrix = vectorizer.fit_transform(texts)
    dimensions = min(256, matrix.shape[1] - 1, len(texts) - 1)
    if dimensions < 1:
        return _normalize_embeddings(matrix.toarray().astype(np.float32))
    return _normalize_embeddings(
        TruncatedSVD(n_components=dimensions, random_state=0).fit_transform(matrix)
    )


def _compute_embeddings(rows: list[_Row], config: dict[str, Any]) -> np.ndarray:
    texts = [row.source for row in rows]
    cache, digest = _cache_paths(texts, config)
    if cache:
        metadata = cache.with_suffix(".json")
        if cache.exists() and metadata.exists():
            try:
                if json.loads(metadata.read_text())["data_hash"] == digest:
                    return np.load(cache)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass

    embedding_config = config["embeddings"]
    if embedding_config["enabled"]:
        from sentence_transformers import SentenceTransformer

        device = embedding_config["device"]
        if device == "auto":
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        model = SentenceTransformer(embedding_config["model"], device=device)
        embeddings = np.asarray(
            model.encode(
                texts,
                batch_size=int(embedding_config["batch_size"]),
                show_progress_bar=False,
                normalize_embeddings=True,
            ),
            dtype=np.float32,
        )
    else:
        embeddings = _tfidf_embeddings(texts)

    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, embeddings)
        cache.with_suffix(".json").write_text(json.dumps({"data_hash": digest}))
    return embeddings


def _dedup_embeddings(
    indexes: list[int], embeddings: np.ndarray, threshold: float
) -> tuple[list[int], np.ndarray, set[int]]:
    if len(indexes) < 2:
        return indexes, embeddings, set()
    neighbors = NearestNeighbors(n_neighbors=min(6, len(indexes)), metric="cosine").fit(embeddings)
    distances, nearest = neighbors.kneighbors(embeddings)
    dropped_positions: set[int] = set()
    for position in range(len(indexes)):
        if position in dropped_positions:
            continue
        for neighbor, distance in zip(nearest[position][1:], distances[position][1:], strict=True):
            if 1.0 - distance >= threshold and neighbor not in dropped_positions and neighbor > position:
                dropped_positions.add(int(neighbor))
    kept_positions = [position for position in range(len(indexes)) if position not in dropped_positions]
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
            hard_density = sum(sum(row.flags.values()) for row in document_rows) / len(document_rows)
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


def _kcenter(indexes: list[int], vectors: dict[int, np.ndarray], count: int, rng: random.Random) -> list[int]:
    if count >= len(indexes):
        return indexes
    embeddings = np.vstack([vectors[index] for index in indexes])
    start = rng.randrange(len(indexes))
    selected_positions = [start]
    minimum_distance = 1.0 - embeddings @ embeddings[start]
    for _ in range(count - 1):
        position = int(np.argmax(minimum_distance))
        selected_positions.append(position)
        minimum_distance = np.minimum(minimum_distance, 1.0 - embeddings @ embeddings[position])
    return [indexes[position] for position in selected_positions]


def _select(
    rows: list[_Row],
    vectors: dict[int, np.ndarray],
    candidates: list[int],
    total: int,
    config: dict[str, Any],
    rng: random.Random,
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
            selected.extend(_kcenter(cell, vectors, min(quota, len(cell)), rng))
    return selected, quotas


def _top_up_hard_phenomena(
    rows: list[_Row],
    vectors: dict[int, np.ndarray],
    candidates: list[int],
    selected: list[int],
    total: int,
    config: dict[str, Any],
) -> list[int]:
    shares = dict(config["selection"]["hard_phenomena_min_share"])
    shares["is_rare_term"] = config["selection"]["rare_term_min_share"]
    chosen = set(selected)
    for flag, share in shares.items():
        required = math.ceil(float(share) * total)
        while sum(rows[index].flags[flag] for index in chosen) < required:
            options = [index for index in candidates if index not in chosen and rows[index].flags[flag]]
            if not options:
                break
            incoming = min(
                options,
                key=lambda index: max(
                    (float(vectors[index] @ vectors[item]) for item in chosen), default=0.0
                ),
            )
            removable = sorted(
                chosen,
                key=lambda index: (
                    any(rows[index].flags.values()),
                    -max(
                        (float(vectors[index] @ vectors[item]) for item in chosen if item != index),
                        default=0.0,
                    ),
                    index,
                ),
            )
            if not removable:
                break
            chosen.remove(removable[0])
            chosen.add(incoming)
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


def _gold_subset(rows: list[_Row], selected: list[int], config: dict[str, Any], rng: random.Random) -> set[int]:
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


def select(rows: pl.DataFrame, target: int, seed: int, config: dict[str, Any]) -> SelectionResult:
    """Run every source-pipeline stage and translate its outputs to registry state."""
    records = rows.select(
        "sample_id", "source_text", "target_text", "domain", "document_id"
    ).to_dicts()
    if target <= 0 or not records:
        return SelectionResult([], [], [], [], {}, {"strategy": "contamination_safe", "selected": 0})

    annotated = _annotate(records, config)
    active = list(range(len(annotated)))
    quarantined: set[int] = set()
    dedup_report: dict[str, int] = {"exact": 0, "minhash": 0, "embedding": 0}
    dedup = config["dedup"]
    if dedup["enabled"]:
        active, removed = _dedup_exact(annotated, active)
        quarantined.update(removed)
        dedup_report["exact"] = len(removed)
        if dedup["minhash_enabled"]:
            active, removed = _dedup_minhash(annotated, active, float(dedup["minhash_threshold"]))
            quarantined.update(removed)
            dedup_report["minhash"] = len(removed)

    embeddings = _compute_embeddings([annotated[index] for index in active], config)
    if dedup["enabled"] and dedup["embedding_dedup_enabled"]:
        active, embeddings, removed = _dedup_embeddings(
            active, embeddings, float(dedup["embedding_cosine_threshold"])
        )
        quarantined.update(removed)
        dedup_report["embedding"] = len(removed)
    vectors = {index: embeddings[position] for position, index in enumerate(active)}

    rng = random.Random(seed)
    candidates = _candidate_documents(annotated, active, config, rng)
    selection_target = min(target, len(candidates))
    selected, quotas = _select(
        annotated, vectors, candidates, selection_target, config, rng
    )
    selected = _top_up_hard_phenomena(
        annotated, vectors, candidates, selected, selection_target, config
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
        threshold = float(contamination["near_dup_cosine_threshold"])
        pool = [index for index in active if index not in selected_set and index not in quarantined]
        selected_embeddings = np.vstack([vectors[index] for index in selected_set])
        for start in range(0, len(pool), 2048):
            block = pool[start : start + 2048]
            similarities = np.vstack([vectors[index] for index in block]) @ selected_embeddings.T
            quarantined.update(
                index for index, similarity in zip(block, similarities.max(axis=1), strict=True) if similarity >= threshold
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
    }
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
