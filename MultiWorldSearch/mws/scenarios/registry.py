"""Scenario registry — register and dispatch scenarios by name."""

from __future__ import annotations

from typing import Any

from mws.core.logging import get_logger
from mws.scenarios.base import BaseScenario

logger = get_logger(__name__)

# Global registry: scenario_key -> scenario class
_REGISTRY: dict[str, type[BaseScenario]] = {}


def register_scenario(cls: type[BaseScenario]) -> type[BaseScenario]:
    """Decorator to register a scenario class."""
    if not cls.name:
        raise ValueError(f"Scenario class {cls.__name__} has no 'name' set")
    _REGISTRY[cls.name] = cls
    return cls


def get_scenario(name: str) -> BaseScenario:
    """Get a scenario instance by name."""
    # Ensure all scenarios are imported
    _ensure_loaded()

    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(f"Unknown scenario '{name}'. Available: {available}")
    return _REGISTRY[name]()


def list_scenarios() -> list[str]:
    """List registered scenario names."""
    _ensure_loaded()
    return sorted(_REGISTRY.keys())


_LOADED = False


def _ensure_loaded() -> None:
    """Import scenario modules to trigger registration."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    # Import all scenario modules so @register_scenario decorators fire.
    # Python caches imports so this is idempotent.
    import mws.scenarios.s1_maintenance_handoff.scenario
    import mws.scenarios.s2_physical_record_reconciliation.scenario
    import mws.scenarios.s3_collective_weak_signal.scenario
    import mws.scenarios.s4_new_sku_rampup.scenario
    import mws.scenarios.s5_incident_response.scenario
    import mws.scenarios.s6_counterfactual_safety.scenario
    import mws.scenarios.s7_order_to_fulfillment.scenario  # noqa: F401


def run_scenario(name: str, seed: int = 0, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convenience: get scenario by name and run it."""
    scenario = get_scenario(name)
    return scenario.run(seed=seed, config=config)
