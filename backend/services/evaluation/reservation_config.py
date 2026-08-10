"""Read the deployment-owned contamination-safe reservation policy."""

import os
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "evaluation_reservation.yaml"


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
