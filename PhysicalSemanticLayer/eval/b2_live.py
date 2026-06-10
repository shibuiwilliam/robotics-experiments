"""B2-Live experiment driver — real Claude LLM schema-translation baseline.

Runs the :class:`~eval.baselines.BaselineB2Live` against the same heterogeneity
doses used everywhere else in PSL-Bench and reports, with real measured data:

    * RMSE vs the B2 *simulation* and vs PSL,
    * latency (compared to PSL's ~125 µs),
    * cost per translation,
    * non-determinism (output spread for an identical input),
    * failure modes (parse failures, unit confusion).

This is a billed experiment (it calls the Anthropic API). Configuration follows
the repository convention of environment variables (CLAUDE.md §10):

    PSL_SEED              random seed for the base state (default 42)
    PSL_B2LIVE_MODEL      Claude model id (default claude-haiku-4-5)
    PSL_B2LIVE_NONDET     repetitions for the non-determinism probe (default 5)

Usage::

    python -m eval.b2_live            # full run (smoke + dose-response + non-det)
    python -m eval.b2_live smoke      # single call only (~$0.001)

Results are written to ``experiments/runs/b2_live_results.json`` (stable path)
and to a timestamped run directory with a reproducibility manifest.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from eval.baselines import BaselineB2, BaselineB2Live, run_baseline_comparison
from eval.metrics.contract import round_trip_information_loss
from eval.metrics.se3 import joint_rmse
from eval.runner.manifest import create_run_directory, write_manifest
from sim.schema_gen.generator import (
    HETEROGENEITY_DOSES,
    SchemaTransform,
    apply_schema_transform,
)
from sim.wrapper import MuJoCoSim

# Approx cost per translation call (USD) for pre-flight estimates. Measured
# empirically at ~$0.03 — higher than a raw token count would suggest because
# the Agent SDK's query() injects a sizeable system prompt even with no tools.
EST_COST_PER_CALL_USD = 0.03

# The S1 transform (radians → milliradians) — the canonical smoke case.
S1_TRANSFORM = SchemaTransform(unit_scale=1000.0, unit_name="mrad")


def make_base_state(seed: int) -> dict[str, object]:
    """Build a canonical base state from MuJoCo (matches the baseline tests)."""
    sim = MuJoCoSim(seed=seed)
    sim.step(200)
    return {
        "joint_positions": sim.get_joint_positions(),
        "joint_velocities": sim.get_joint_velocities(),
        "ee_position": sim.get_ee_pose()[0],
        "ee_quaternion": sim.get_ee_pose()[1],
        "time": sim.time,
    }


def _print_estimate(n_calls: int) -> None:
    print(
        f"  [cost] ~{n_calls} API call(s) ≈ ${n_calls * EST_COST_PER_CALL_USD:.4f} "
        f"(Haiku, pre-flight estimate)"
    )


def _require_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY", ""):
        raise SystemExit(
            "ANTHROPIC_API_KEY not set. B2-Live makes real, billed Claude API "
            "calls — set the key (e.g. in .env) before running."
        )


async def run_smoke(base_state: dict[str, object], model: str) -> dict[str, Any]:
    """Single B2-Live call on the S1 transform — connectivity + sanity check."""
    print("\n=== B2-Live smoke (1 call, S1 transform: rad→mrad) ===")
    _print_estimate(1)
    rng = np.random.default_rng(0)
    hetero = apply_schema_transform(base_state, S1_TRANSFORM, rng=rng)
    original = np.asarray(base_state["joint_positions"], dtype=np.float64)

    b2_live = BaselineB2Live(S1_TRANSFORM, model=model)
    result = await b2_live.translate(hetero)
    pred = np.asarray(result["joint_positions"], dtype=np.float64)

    rmse = joint_rmse(pred, original)
    rec = b2_live.records[0]
    print(f"  model={model}")
    print(f"  rmse={rmse:.6e}  latency={rec.latency_ms:.1f}ms  cost=${rec.cost_usd:.5f}")
    print(f"  parsed={rec.parsed}  failure_mode={rec.failure_mode}")
    return {
        "model": model,
        "joint_rmse": rmse,
        "latency_ms": rec.latency_ms,
        "cost_usd": rec.cost_usd,
        "parsed": rec.parsed,
        "failure_mode": rec.failure_mode,
    }


async def run_dose_response(
    base_state: dict[str, object], model: str, seed: int
) -> list[dict[str, Any]]:
    """One B2-Live call per heterogeneity dose, alongside B2-sim and PSL."""
    print(f"\n=== B2-Live dose-response ({len(HETEROGENEITY_DOSES)} doses) ===")
    _print_estimate(len(HETEROGENEITY_DOSES))
    original = np.asarray(base_state["joint_positions"], dtype=np.float64)
    rows: list[dict[str, Any]] = []

    for i, dose in enumerate(HETEROGENEITY_DOSES):
        # Offline baselines (B0/B1/B2-sim/PSL) on the same input, same seed.
        offline = run_baseline_comparison(base_state, dose, np.random.default_rng(seed), seed=seed)

        # Live translation on the identical heterogeneous state.
        hetero = apply_schema_transform(base_state, dose, rng=np.random.default_rng(seed))
        b2_live = BaselineB2Live(dose, model=model)
        result = await b2_live.translate(hetero)
        pred = np.asarray(result["joint_positions"], dtype=np.float64)
        rec = b2_live.records[0]

        row = {
            "dose": i,
            "unit_scale": dose.unit_scale,
            "frame_rotation_z_rad": dose.frame_rotation_z_rad,
            "sensor_noise_std": dose.sensor_noise_std,
            "b2_sim_rmse": offline["B2"]["joint_rmse"],
            "psl_rmse": offline["PSL"]["joint_rmse"],
            "psl_latency_ms": offline["PSL"]["latency_ms"],
            "b2_live_rmse": joint_rmse(pred, original),
            "b2_live_info_loss": round_trip_information_loss(original, pred),
            "b2_live_latency_ms": rec.latency_ms,
            "b2_live_cost_usd": rec.cost_usd,
            "b2_live_parsed": rec.parsed,
            "b2_live_failure_mode": rec.failure_mode,
        }
        rows.append(row)
        print(
            f"  dose {i}: live_rmse={row['b2_live_rmse']:.4e} "
            f"sim_rmse={row['b2_sim_rmse']:.4e} psl_rmse={row['psl_rmse']:.4e} "
            f"live_lat={rec.latency_ms:.0f}ms psl_lat={row['psl_latency_ms']:.4f}ms "
            f"fail={rec.failure_mode}"
        )
    return rows


async def run_nondeterminism(
    base_state: dict[str, object], model: str, seed: int, n: int
) -> dict[str, Any]:
    """Translate an identical input ``n`` times; quantify the output spread."""
    print(f"\n=== B2-Live non-determinism ({n} repeats, identical input) ===")
    _print_estimate(n)
    hetero = apply_schema_transform(base_state, S1_TRANSFORM, rng=np.random.default_rng(seed))

    b2_live = BaselineB2Live(S1_TRANSFORM, model=model)
    outputs: list[list[float]] = []
    for _ in range(n):
        result = await b2_live.translate(hetero)
        outputs.append(np.asarray(result["joint_positions"], dtype=np.float64).tolist())

    arr = np.asarray(outputs, dtype=np.float64)  # (n, n_joints)
    per_elem_std = np.std(arr, axis=0)
    max_spread = float(np.max(np.ptp(arr, axis=0)))
    identical = bool(np.allclose(arr, arr[0]))
    print(
        f"  identical_across_runs={identical}  "
        f"max_elem_std={float(np.max(per_elem_std)):.3e}  max_spread={max_spread:.3e}"
    )
    return {
        "n": n,
        "identical_across_runs": identical,
        "max_element_std": float(np.max(per_elem_std)),
        "mean_element_std": float(np.mean(per_elem_std)),
        "max_spread": max_spread,
        "per_call_cost_usd": [r.cost_usd for r in b2_live.records],
        "total_cost_usd": b2_live.total_cost,
    }


def summarize_failures(*record_sources: BaselineB2Live) -> dict[str, Any]:
    """Aggregate failure modes across one or more B2-Live instances."""
    modes: dict[str, int] = {}
    total = 0
    for src in record_sources:
        for rec in src.records:
            total += 1
            key = rec.failure_mode or "clean"
            modes[key] = modes.get(key, 0) + 1
    return {"total_calls": total, "by_mode": modes}


def save_results(results: dict[str, Any], seed: int, model: str) -> Path:
    """Write the stable JSON + a timestamped reproducibility manifest."""
    runs_dir = Path("experiments/runs")
    runs_dir.mkdir(parents=True, exist_ok=True)

    stable = runs_dir / "b2_live_results.json"
    stable.write_text(json.dumps(results, indent=2, default=str))

    run_dir = create_run_directory(runs_dir, "b2_live")
    write_manifest(
        run_dir,
        config={"experiment": "b2_live", "model": model, "seed": seed},
        seed=seed,
        metrics=results,
    )
    print(f"\nSaved: {stable}")
    print(f"Manifest: {run_dir / 'manifest.json'}")
    return stable


async def _amain(argv: list[str]) -> None:
    _require_key()
    seed = int(os.environ.get("PSL_SEED", "42"))
    model = os.environ.get("PSL_B2LIVE_MODEL", BaselineB2Live.DEFAULT_MODEL)
    n_nondet = int(os.environ.get("PSL_B2LIVE_NONDET", "5"))
    smoke_only = len(argv) > 1 and argv[1] == "smoke"

    print("=== B2-Live: real Claude LLM translation baseline ===")
    print(f"  model={model}  seed={seed}  smoke_only={smoke_only}")

    base_state = make_base_state(seed)
    t0 = time.perf_counter()

    smoke = await run_smoke(base_state, model)
    if smoke_only:
        save_results({"mode": "smoke", "smoke": smoke}, seed, model)
        return

    doses = await run_dose_response(base_state, model, seed)
    nondet = await run_nondeterminism(base_state, model, seed, n_nondet)

    # B2-sim baseline numbers for the same doses (free), for the comparison table.
    sim_doses = []
    original = np.asarray(base_state["joint_positions"], dtype=np.float64)
    for i, dose in enumerate(HETEROGENEITY_DOSES):
        b2 = BaselineB2(dose, seed=seed)
        pred = np.asarray(
            b2.translate(
                apply_schema_transform(base_state, dose, rng=np.random.default_rng(seed))
            )["joint_positions"],
            dtype=np.float64,
        )
        sim_doses.append({"dose": i, "b2_sim_rmse": joint_rmse(pred, original)})

    total_cost = sum(r["b2_live_cost_usd"] for r in doses)
    total_cost += smoke["cost_usd"] + nondet["total_cost_usd"]
    total_calls = 1 + len(doses) + nondet["n"]

    results = {
        "mode": "full",
        "model": model,
        "seed": seed,
        "smoke": smoke,
        "dose_response": doses,
        "b2_sim_dose_response": sim_doses,
        "non_determinism": nondet,
        "totals": {
            "api_calls": total_calls,
            "cost_usd": total_cost,
            "wall_clock_s": time.perf_counter() - t0,
        },
    }
    save_results(results, seed, model)

    print("\n=== Summary ===")
    print(f"  total API calls: {total_calls}   total cost: ${total_cost:.4f}")
    live_lat = float(np.mean([r["b2_live_latency_ms"] for r in doses]))
    psl_lat = float(np.mean([r["psl_latency_ms"] for r in doses]))
    print(f"  mean B2-Live latency: {live_lat:.0f} ms   mean PSL latency: {psl_lat:.4f} ms")
    print(f"  latency ratio (B2-Live / PSL): {live_lat / psl_lat:,.0f}x" if psl_lat else "")
    print(
        "  non-determinism: identical_across_runs="
        f"{nondet['identical_across_runs']} max_spread={nondet['max_spread']:.3e}"
    )


def main(argv: list[str] | None = None) -> None:
    asyncio.run(_amain(argv if argv is not None else sys.argv))


if __name__ == "__main__":
    main()
