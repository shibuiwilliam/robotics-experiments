"""Scenario config loader — reads YAML configs and provides typed access.

Each scenario's thresholds, sweep axes, agent settings, and seeds come
from its YAML config file. No hardcoded values in Python.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIGS_DIR = Path(__file__).parents[2] / "experiments" / "scenarios"


def load_scenario_config(scenario_slug: str) -> dict[str, Any]:
    """Load a scenario's YAML config.

    Args:
        scenario_slug: e.g. "s1_mixed_fleet_pick".

    Returns:
        Parsed config dict with all scenario settings.
    """
    config_path = CONFIGS_DIR / f"{scenario_slug}.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def get_threshold(config: dict[str, Any], key: str, default: float) -> float:
    """Get a threshold value from config, with fallback default.

    Args:
        config: Scenario config dict.
        key: Threshold key (e.g. "calibration_nll_max").
        default: Fallback if key is not in config.

    Returns:
        Threshold value.
    """
    thresholds = config.get("thresholds", {})
    if isinstance(thresholds, dict) and key in thresholds:
        return float(thresholds[key])
    return default


def get_seeds(config: dict[str, Any], default: list[int] | None = None) -> list[int]:
    """Get seed list from config or environment.

    Args:
        config: Scenario config dict.
        default: Fallback seeds.

    Returns:
        List of seeds.
    """
    import os

    env_seeds = os.environ.get("PSL_SEEDS", "")
    if env_seeds:
        return [int(s.strip()) for s in env_seeds.split(",") if s.strip()]

    seed = config.get("seed", 42)
    if isinstance(seed, list):
        return [int(s) for s in seed]
    return default or [int(seed)]
