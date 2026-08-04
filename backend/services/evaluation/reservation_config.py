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
