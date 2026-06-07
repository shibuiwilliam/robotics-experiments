"""Generate multi-seed statistical report for all scenarios.

Runs each scenario with 5 seeds in OFFLINE mode, computes statistics,
and writes a JSON report to experiments/runs/multi_seed_report.json.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from eval.scenarios.execution_mode import ExecutionMode
from eval.scenarios.multi_seed import SCENARIOS, run_multi_seed


def main() -> None:
    """Run all scenarios with multiple seeds and write a report."""
    seeds = [42, 123, 456, 789, 1024]
    report: dict[str, object] = {"seeds": seeds, "mode": "offline", "scenarios": {}}

    for scenario_slug, fn_name in SCENARIOS:
        print(f"Running {scenario_slug} with {len(seeds)} seeds...")
        mod = importlib.import_module(f"eval.scenarios.{scenario_slug}.eval")
        fn = getattr(mod, fn_name)
        result = run_multi_seed(fn, seeds, mode=ExecutionMode.OFFLINE)

        scenario_report = {
            "all_business_success": result.all_business_success,
            "all_psl_success": result.all_psl_success,
            "n_seeds": result.n_seeds,
            "metrics_mean": result.metrics_mean,
            "metrics_std": result.metrics_std,
            "metrics_ci95": {k: list(v) for k, v in result.metrics_ci95.items()},
        }
        scenarios = report["scenarios"]
        if isinstance(scenarios, dict):
            scenarios[scenario_slug] = scenario_report

        status = "PASS" if result.all_business_success and result.all_psl_success else "FAIL"
        print(f"  {scenario_slug}: {status}")
        for key in sorted(result.metrics_mean):
            m = result.metrics_mean[key]
            s = result.metrics_std[key]
            print(f"    {key}: {m:.6f} +/- {s:.6f}")

    out = Path("experiments/runs/multi_seed_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved to {out}")


if __name__ == "__main__":
    main()
