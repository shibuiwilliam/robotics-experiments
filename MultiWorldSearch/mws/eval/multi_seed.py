"""Multi-seed runner — run scenarios with multiple seeds and compute CI.

Addresses PROJECT.md 10.2: "multiple seeds x multiple worlds with confidence
intervals." Produces mean and 95% CI for key metrics.
"""

from __future__ import annotations

import math
from typing import Any

from mws.core.config import MWSSettings
from mws.core.logging import get_logger
from mws.scenarios.registry import get_scenario

logger = get_logger(__name__)


def run_multi_seed(
    scenario_name: str,
    seeds: list[int] | None = None,
    settings: MWSSettings | None = None,
) -> dict[str, Any]:
    """Run a scenario with multiple seeds and aggregate metrics.

    Args:
        scenario_name: Registered scenario name.
        seeds: List of seeds to run. Default: [0, 1, 2, 3, 4].
        settings: MWS settings override.

    Returns:
        {
            "scenario": str,
            "n_seeds": int,
            "seeds": list[int],
            "per_seed": {seed: metrics_dict},
            "summary": {metric_name: {mean, std, ci95_low, ci95_high}},
        }
    """
    if seeds is None:
        seeds = [0, 1, 2, 3, 4]

    per_seed: dict[int, dict[str, Any]] = {}

    for seed in seeds:
        logger.info(f"Multi-seed run: {scenario_name} seed={seed}")
        scenario = get_scenario(scenario_name)
        result = scenario.run(seed=seed, settings=settings)
        per_seed[seed] = result.get("metrics", {})

    # Collect numeric metrics across seeds
    metric_values: dict[str, list[float]] = {}
    for seed_metrics in per_seed.values():
        _collect_numeric(seed_metrics, "", metric_values)

    # Compute summary statistics
    summary: dict[str, dict[str, float]] = {}
    for metric_name, values in metric_values.items():
        if len(values) >= 2:
            summary[metric_name] = _compute_ci(values)

    return {
        "scenario": scenario_name,
        "n_seeds": len(seeds),
        "seeds": seeds,
        "per_seed": per_seed,
        "summary": summary,
    }


def run_multi_seed_suite(
    scenario_names: list[str],
    seeds: list[int] | None = None,
    settings: MWSSettings | None = None,
) -> dict[str, Any]:
    """Run several scenarios across multiple seeds and aggregate CIs per scenario.

    Returns {"seeds": [...], "scenarios": {name: {summary: {...}}}}. Used by the
    `eval multi-seed` CLI command / `make scenario-multi-seed-all` to report
    confidence intervals instead of single-seed point estimates (PROJECT.md
    §10.2, CLAUDE.md §9).
    """
    if seeds is None:
        seeds = [0, 1, 2, 3, 4]
    out: dict[str, Any] = {"seeds": seeds, "scenarios": {}}
    for name in scenario_names:
        res = run_multi_seed(name, seeds=seeds, settings=settings)
        out["scenarios"][name] = {"summary": res["summary"]}
    return out


def _collect_numeric(d: dict[str, Any], prefix: str, out: dict[str, list[float]]) -> None:
    """Recursively collect numeric values from nested dicts."""
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.setdefault(key, []).append(float(v))
        elif isinstance(v, dict):
            _collect_numeric(v, key, out)


def _compute_ci(values: list[float]) -> dict[str, float]:
    """Compute mean, std, and 95% CI (t-distribution for small N)."""
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return {"mean": mean, "std": 0.0, "ci95_low": mean, "ci95_high": mean, "n": float(n)}

    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    std = math.sqrt(variance)
    # t-critical values for 95% CI (two-tailed) with n-1 degrees of freedom
    t_crit = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365}
    t = t_crit.get(n - 1, 1.96)  # fall back to z for large n
    margin = t * std / math.sqrt(n)

    return {
        "mean": mean,
        "std": std,
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
        "n": float(n),
    }
