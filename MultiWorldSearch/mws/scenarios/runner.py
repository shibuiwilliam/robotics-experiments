"""Scenario runner — dispatches via the scenario registry."""

from __future__ import annotations

from pathlib import Path

import yaml

from mws.core.logging import get_logger

logger = get_logger(__name__)

# Default configs per scenario name
_DEFAULT_CONFIGS: dict[str, str] = {
    "maintenance_handoff": "configs/scenarios/maintenance_handoff.yaml",
    "physical_record_reconciliation": "configs/scenarios/physical_record_reconciliation.yaml",
    "counterfactual_safety": "configs/scenarios/counterfactual_safety.yaml",
    "collective_weak_signal": "configs/scenarios/collective_weak_signal.yaml",
    "new_sku_rampup": "configs/scenarios/new_sku_rampup.yaml",
}


def _load_scenario_config(config_path: str) -> dict:
    """Load a scenario YAML config, returning empty dict if not found."""
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            config = yaml.safe_load(f) or {}
        logger.info("Loaded scenario config", path=config_path)
        return config
    logger.debug("Scenario config not found, using defaults", path=config_path)
    return {}


def run_scenario(name: str, seed: int = 0, config_path: str | None = None) -> dict:
    """Run a named scenario via the registry; return its result dict (G4)."""
    from mws.scenarios.registry import run_scenario as registry_run

    # Resolve config
    effective_config = config_path or _DEFAULT_CONFIGS.get(name, "")
    config = _load_scenario_config(effective_config) if effective_config else {}

    return registry_run(name=name, seed=seed, config=config)
