"""Multi-seed scenario runner with statistical aggregation.

Runs a scenario across multiple seeds and computes mean, std, 95% CI
for each numeric metric. Supports hypothesis testing (PSL vs baselines).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from eval.scenarios.base import ScenarioResult


@dataclass
class MultiSeedResult:
    """Aggregated results across multiple seeds.

    Fields:
        scenario_id: Scenario identifier.
        n_seeds: Number of seeds run.
        seeds: List of seeds used.
        results: Per-seed ScenarioResult list.
        metrics_mean: Mean of each numeric metric across seeds.
        metrics_std: Standard deviation of each numeric metric.
        metrics_ci95: 95% confidence intervals (lo, hi) for each metric.
        all_business_success: True if all seeds passed business success.
        all_psl_success: True if all seeds passed PSL success.
    """

    scenario_id: str
    n_seeds: int
    seeds: list[int]
    results: list[ScenarioResult] = field(default_factory=list)
    metrics_mean: dict[str, float] = field(default_factory=dict)
    metrics_std: dict[str, float] = field(default_factory=dict)
    metrics_ci95: dict[str, tuple[float, float]] = field(default_factory=dict)
    all_business_success: bool = True
    all_psl_success: bool = True


def run_multi_seed(
    evaluate_fn: Callable[..., ScenarioResult],
    seeds: list[int],
    **kwargs: Any,
) -> MultiSeedResult:
    """Run a scenario evaluation across multiple seeds.

    Args:
        evaluate_fn: The evaluate_sN() function to call.
        seeds: List of seeds to run.
        **kwargs: Additional arguments passed to evaluate_fn (except seed).

    Returns:
        MultiSeedResult with aggregated statistics.
    """
    results: list[ScenarioResult] = []
    for seed in seeds:
        r = evaluate_fn(seed=seed, **kwargs)
        results.append(r)

    # Aggregate numeric metrics
    all_metrics: dict[str, list[float]] = {}
    for r in results:
        for key, val in r.metrics.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if key not in all_metrics:
                    all_metrics[key] = []
                all_metrics[key].append(float(val))

    metrics_mean: dict[str, float] = {}
    metrics_std: dict[str, float] = {}
    metrics_ci95: dict[str, tuple[float, float]] = {}

    for key, values in all_metrics.items():
        arr = np.array(values)
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        metrics_mean[key] = mean
        metrics_std[key] = std

        # 95% CI using t-distribution
        if len(arr) > 1:
            from scipy.stats import t as t_dist

            n = len(arr)
            se = std / np.sqrt(n)
            t_val = t_dist.ppf(0.975, df=n - 1)
            ci_lo = mean - t_val * se
            ci_hi = mean + t_val * se
            metrics_ci95[key] = (float(ci_lo), float(ci_hi))
        else:
            metrics_ci95[key] = (mean, mean)

    scenario_id = results[0].scenario_id if results else "unknown"

    return MultiSeedResult(
        scenario_id=scenario_id,
        n_seeds=len(seeds),
        seeds=seeds,
        results=results,
        metrics_mean=metrics_mean,
        metrics_std=metrics_std,
        metrics_ci95=metrics_ci95,
        all_business_success=all(r.business_success for r in results),
        all_psl_success=all(r.psl_success for r in results),
    )


# ── Scenario registry for CLI entry point ──

SCENARIOS = [
    ("s1_mixed_fleet_pick", "evaluate_s1"),
    ("s2_line_changeover", "evaluate_s2"),
    ("s3_lab_custody", "evaluate_s3"),
    ("s4_field_inspection", "evaluate_s4"),
    ("s5_pharma_logistics", "evaluate_s5"),
    ("s6_ewaste_disassembly", "evaluate_s6"),
    ("s7_degraded_ops", "evaluate_s7"),
]


def main() -> None:
    """CLI entry point: run all scenarios with multiple seeds."""
    import importlib
    import os

    from eval.scenarios.execution_mode import ExecutionMode

    seeds_str = os.environ.get("PSL_SEEDS", "42,43,44,45,46")
    seeds = [int(s.strip()) for s in seeds_str.split(",") if s.strip()]
    mode_str = os.environ.get("PSL_MODE", "offline").lower()
    mode = ExecutionMode.ONLINE if mode_str == "online" else ExecutionMode.OFFLINE

    print(f"Multi-seed run: {len(seeds)} seeds, mode={mode.value}")
    print(f"Seeds: {seeds}")
    print()

    for scenario_slug, fn_name in SCENARIOS:
        mod = importlib.import_module(f"eval.scenarios.{scenario_slug}.eval")
        fn = getattr(mod, fn_name)
        result = run_multi_seed(fn, seeds, mode=mode)

        status = "PASS" if result.all_business_success and result.all_psl_success else "FAIL"
        print(f"{scenario_slug}: {result.n_seeds} seeds, {status}")

        for key in sorted(result.metrics_mean):
            mean = result.metrics_mean[key]
            std = result.metrics_std[key]
            ci = result.metrics_ci95.get(key, (mean, mean))
            print(f"  {key}: {mean:.6f} ± {std:.6f}  CI95=[{ci[0]:.6f}, {ci[1]:.6f}]")
        print()


if __name__ == "__main__":
    main()
