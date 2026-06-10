"""Unified Phase 4 stress test report.

Runs schema fuzzing, clock skew, and grounding generalization,
then writes a JSON report with all results.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from eval.stress.clock_skew import sweep_clock_skew
from eval.stress.grounding_generalization import (
    HOLDOUT_OBJECTS,
    KNOWN_OBJECTS,
    compute_generalization_metrics,
    run_generalization,
)
from eval.stress.schema_fuzzing import find_breaking_point, fuzz_schema
from psl.adapters.robots.panda.adapter import PandaAdapter
from sim.wrapper import MuJoCoSim


def run_stress_suite(seed: int = 42, n_fuzz: int = 50) -> dict[str, object]:
    """Run the full Phase 4 stress test suite.

    Args:
        seed: Random seed.
        n_fuzz: Number of schema fuzz samples.

    Returns:
        Combined report dict with all stress test results.
    """
    print("=== Phase 4 Stress Test Suite ===")
    print()

    # 1. Schema fuzzing
    print(f"[1/3] Schema fuzzing ({n_fuzz} samples)...")
    sim = MuJoCoSim(seed=seed)
    sim.step(100)
    adapter = PandaAdapter()
    fuzz_results = fuzz_schema(n_fuzz, seed, sim, adapter)
    breaking = find_breaking_point(fuzz_results)
    n_accepted = sum(1 for r in fuzz_results if r.gate_accepted)
    print(f"  {n_accepted}/{n_fuzz} accepted by gate")
    print(f"  Max RMSE: {breaking['max_rmse']:.6f}")
    print(f"  Max info loss: {breaking['max_info_loss']:.6f}")
    print()

    # 2. Clock skew (5ms NTP-level uncertainty)
    print("[2/3] Clock skew sweep (clock_uncertainty=5ms)...")
    skew_results = sweep_clock_skew(seed=seed, clock_uncertainty=0.005)
    for sr in skew_results:
        status = "OK" if sr.gate_rejections == 0 else "REJECTED"
        print(
            f"  skew={sr.skew_seconds:.3f}s -> {status} "
            f"(rejections={sr.gate_rejections}/{sr.n_transitions}, "
            f"rate={sr.rejection_rate:.0%})"
        )
    print()

    # 3. Grounding generalization
    print(f"[3/3] Grounding generalization ({len(HOLDOUT_OBJECTS)} holdout objects)...")
    gen_results = run_generalization(HOLDOUT_OBJECTS, KNOWN_OBJECTS, seed)
    gen_metrics = compute_generalization_metrics(gen_results)
    print(f"  Embedding coverage: {gen_metrics['embedding_coverage']:.1%}")
    print(f"  Affordance coverage: {gen_metrics['affordance_coverage']:.1%}")
    print(f"  Material diversity: {gen_metrics['material_diversity']} classes")
    print(f"  Mean nearest cosine: {gen_metrics['mean_nearest_cosine']:.4f}")
    print()

    report: dict[str, object] = {
        "seed": seed,
        "schema_fuzzing": {
            "n_samples": n_fuzz,
            "n_accepted": n_accepted,
            "breaking_points": breaking,
            "results": [asdict(r) for r in fuzz_results],
        },
        "clock_skew": {
            "results": [asdict(r) for r in skew_results],
        },
        "grounding_generalization": {
            "n_holdout": len(HOLDOUT_OBJECTS),
            "n_known": len(KNOWN_OBJECTS),
            "metrics": gen_metrics,
            "results": [asdict(r) for r in gen_results],
        },
    }

    return report


def main() -> None:
    """Run full stress suite and save report."""
    report = run_stress_suite(seed=42, n_fuzz=50)

    out = Path("experiments/runs/stress_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"Report saved to {out}")


if __name__ == "__main__":
    main()
