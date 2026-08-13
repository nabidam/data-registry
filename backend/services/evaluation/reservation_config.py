"""Read and validate the deployment-owned contamination-safe reservation policy.

Validation lives here rather than at each use site because the expensive stages —
LaBSE encoding, k-center diversity, the full-corpus scan — run long before a bad
policy value would otherwise be noticed.  A typo in a per-pair length bucket used
to surface as a ``zip() argument 2 is longer`` deep inside quota allocation, hours
into an import.  ``validate_policy`` turns every such mistake into one up-front
error listing all of them at once.
"""

import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from services.evaluation.language_profiles import ALL_FEATURES

_DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "evaluation_reservation.yaml"

# Declared here rather than in contamination_safe so validation can reference them
# without importing the heavy selector. contamination_safe re-exports both.
COMPARISON_SCOPES = ("pair", "source_language", "target_language", "any")
DOCUMENT_NAMESPACES = ("source", "batch", "none")
DOMAIN_ALLOCATIONS = ("flattened", "proportional")


def _config_path() -> Path:
    """Return the configured policy path without adding an app-wide setting."""
    return Path(os.environ.get("EVALUATION_RESERVATION_CONFIG", _DEFAULT_CONFIG_PATH))


@lru_cache
def _read_config(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
    except OSError as exc:
        raise ValueError(f"could not read evaluation reservation config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid evaluation reservation config {path}: {exc}") from exc

    if not isinstance(config, Mapping):
        raise ValueError(f"evaluation reservation config {path} must be a mapping")
    if not isinstance(config.get("description"), str):
        raise ValueError(f"evaluation reservation config {path} requires a description")
    return dict(config)


def load_contamination_safe_config() -> dict[str, Any]:
    """Load an isolated contamination-safe policy mapping for callers."""
    return deepcopy(_read_config(_config_path()))


def _merge(base: Any, override: Any) -> Any:
    """Deep-merge mappings; any other value is replaced outright.

    Lists are replaced rather than concatenated because every list in this policy
    is an ordered definition (bucket edges, bucket shares) where appending would
    produce a silently invalid policy instead of an override.
    """
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _merge(merged.get(key), value) if key in merged else deepcopy(value)
        return merged
    return deepcopy(override)


def resolve_pair_policy(config: Mapping[str, Any], pair_key: str) -> dict[str, Any]:
    """Merge ``config['pairs'][pair_key]`` over the base policy.

    Length buckets and hard-phenomena shares are calibrated against a specific
    language's token density, so a deployment holding several pairs needs a way
    to say "ru-fa measures length differently" without forking the whole policy.
    A config with no ``pairs`` section resolves to the base policy unchanged,
    which is what keeps older config files working.
    """
    overrides = config.get("pairs") or {}
    if not isinstance(overrides, Mapping):
        raise ValueError("evaluation reservation config 'pairs' must be a mapping")
    override = overrides.get(pair_key)
    if not override:
        return deepcopy(dict(config))
    if not isinstance(override, Mapping):
        raise ValueError(f"evaluation reservation config pairs.{pair_key} must be a mapping")
    return _merge(dict(config), override)


def _check_selection(selection: Any, where: str, problems: list[str]) -> None:
    """Validate one resolved ``selection`` block: base policy or a merged pair."""
    if not isinstance(selection, Mapping):
        problems.append(f"{where}.selection must be a mapping")
        return

    edges = selection.get("length_buckets")
    shares = selection.get("length_bucket_shares")
    if not isinstance(edges, Sequence) or isinstance(edges, str) or len(edges) < 2:
        problems.append(f"{where}.selection.length_buckets must list at least two edges")
        edges = None
    elif any(not isinstance(edge, int) for edge in edges):
        problems.append(f"{where}.selection.length_buckets must contain integers")
        edges = None
    elif any(low >= high for low, high in zip(edges[:-1], edges[1:], strict=True)):
        problems.append(f"{where}.selection.length_buckets must increase strictly: {list(edges)}")
        edges = None

    if not isinstance(shares, Sequence) or isinstance(shares, str):
        problems.append(f"{where}.selection.length_bucket_shares must be a list")
    elif any(not isinstance(share, int | float) or share < 0 for share in shares):
        problems.append(f"{where}.selection.length_bucket_shares must be non-negative numbers")
    elif edges is not None and len(shares) != len(edges) - 1:
        # The stage that used to fail on this ran after LaBSE encoding.
        problems.append(
            f"{where}.selection.length_bucket_shares has {len(shares)} entries but "
            f"length_buckets defines {len(edges) - 1} buckets"
        )

    share = selection.get("max_holdout_share")
    if share is not None and not (isinstance(share, int | float) and 0.0 < float(share) <= 1.0):
        problems.append(
            f"{where}.selection.max_holdout_share must be a fraction in (0, 1]; got {share!r}"
        )

    for key in ("max_holdout_rows", "max_test_documents"):
        value = selection.get(key)
        if value is not None and not (isinstance(value, int) and value > 0):
            problems.append(f"{where}.selection.{key} must be a positive integer or null")

    allocation = selection.get("domain_allocation")
    if allocation not in DOMAIN_ALLOCATIONS:
        problems.append(
            f"{where}.selection.domain_allocation must be one of {list(DOMAIN_ALLOCATIONS)}; "
            f"got {allocation!r}"
        )

    phenomena = selection.get("hard_phenomena_min_share")
    if not isinstance(phenomena, Mapping):
        problems.append(f"{where}.selection.hard_phenomena_min_share must be a mapping")
    else:
        unknown = sorted(set(phenomena) - set(ALL_FEATURES))
        if unknown:
            problems.append(
                f"{where}.selection.hard_phenomena_min_share names unknown features {unknown}; "
                f"available: {list(ALL_FEATURES)}"
            )


def validate_policy(config: Mapping[str, Any]) -> None:
    """Raise once, listing every problem, before any expensive stage runs.

    Called at the start of selection and when a reservation is requested, so an
    operator sees a bad policy immediately instead of after a long import.
    """
    problems: list[str] = []

    contamination = config.get("contamination")
    if not isinstance(contamination, Mapping):
        problems.append("contamination must be a mapping")
        contamination = {}

    scope = contamination.get("comparison_scope", "pair")
    if scope not in COMPARISON_SCOPES:
        problems.append(
            f"contamination.comparison_scope must be one of {list(COMPARISON_SCOPES)}; "
            f"got {scope!r}"
        )

    namespace = contamination.get("document_namespace", "source")
    if namespace not in DOCUMENT_NAMESPACES:
        # Silently falling through to un-namespaced document IDs would reintroduce
        # the cross-corpus "doc:1" collision with no signal at all.
        problems.append(
            f"contamination.document_namespace must be one of {list(DOCUMENT_NAMESPACES)}; "
            f"got {namespace!r}"
        )

    for key in ("near_dup_cosine_threshold", "cross_lingual_cosine_threshold"):
        value = contamination.get(key)
        if value is not None and not (isinstance(value, int | float) and 0.0 <= value <= 1.0):
            problems.append(f"contamination.{key} must be a cosine value in [0, 1]; got {value!r}")

    _check_selection(config.get("selection"), "policy", problems)

    overrides = config.get("pairs") or {}
    if not isinstance(overrides, Mapping):
        problems.append("pairs must be a mapping of '<src>-<tgt>' to policy overrides")
    else:
        for pair_key, override in overrides.items():
            if not isinstance(override, Mapping):
                problems.append(f"pairs.{pair_key} must be a mapping")
                continue
            # Validate the merged result: an override may legally supply only the
            # keys it changes, and it is the merge that has to stay coherent.
            _check_selection(
                _merge(dict(config), override).get("selection"), f"pairs.{pair_key}", problems
            )

    if problems:
        raise ValueError(
            "invalid contamination-safe reservation policy:\n- " + "\n- ".join(problems)
        )
