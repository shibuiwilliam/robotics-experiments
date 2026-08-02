"""Run driver v2 — expand a scenario across arms × repeats (× sweep), score the DSL oracle.

For each arm, every seed's driver produces a RunContext; the oracle engine scores success/must/
acceptable_world per repeat and endpoints across repeats (SCENARIOS.md §3). One RunRecord is emitted
per (arm, seed) carrying the arm-level oracle verdict + per-repeat metrics for the scoreboard.
"""

from __future__ import annotations

from typing import Any

from bench.oracle import OracleResult, RunContext, evaluate, score_oracle
from bench.runner.drivers import get_driver
from bench.runner.record import RunRecord
from bench.scenarios.loader import Scenario, list_scenarios, load_scenario


def _sweep_points(scenario: Scenario) -> list[tuple[dict[str, Any], bool]]:
    """The primary point (default vars, gates pass) plus any sweep points (curve only)."""
    points: list[tuple[dict[str, Any], bool]] = [({}, False)]
    if scenario.sweep:
        param = str(scenario.sweep["param"])
        points += [({param: v}, True) for v in scenario.sweep["values"]]
    return points


def _success(scenario: Scenario, ctx: RunContext) -> bool:
    if not scenario.oracle.success:
        return True
    try:
        return bool(evaluate(scenario.oracle.success, ctx))
    except Exception:
        return False


def _num_metrics(ctx: RunContext) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in ctx.extras.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v)
    return out


def _records_for_arm(
    scenario: Scenario, arm: str, seeds: list[int], variables: dict[str, Any], is_sweep: bool
) -> list[RunRecord]:
    driver = get_driver(scenario)
    contexts = [driver(scenario, arm, seed, variables) for seed in seeds]
    result: OracleResult = score_oracle(scenario.oracle, contexts)
    checks = {
        "must_ok": result.must_ok,
        "acceptable": result.acceptable_ok,
        **{f"endpoint[{i}]": ok for i, ok in enumerate(result.endpoints.values())},
    }
    records: list[RunRecord] = []
    for seed, ctx in zip(seeds, contexts, strict=True):
        metrics = _num_metrics(ctx)
        metrics["is_sweep"] = 1.0 if is_sweep else 0.0
        metrics.update(
            {f"sweep.{k}": float(v) for k, v in variables.items() if isinstance(v, (int, float))}
        )
        records.append(
            RunRecord(
                scenario=scenario.name,
                experiment=scenario.experiment,
                arm=arm,
                seed=seed,
                oracle_passed=result.passed,
                success=_success(scenario, ctx),
                unapproved_irreversible=int(ctx.extras.get("unapproved_irreversible", 0)),
                trace_completeness=float(ctx.extras.get("trace_completeness", 1.0)),
                claims=ctx.claims.count() if ctx.claims else 0,
                bus_events=len(ctx.events),
                api_calls=ctx.api_calls,
                plan_steps=int(ctx.extras.get("plan_steps", 0)),
                executed=int(ctx.extras.get("executed", 0)),
                reason="ok" if result.passed else f"oracle_failed:{result.must_failures}",
                checks=checks,
                metrics=metrics,
            )
        )
    return records


def run_scenario(
    name: str, arm: str | None = None, seeds: list[int] | None = None
) -> list[RunRecord]:
    """Run a scenario across its arms × seeds (× sweep). Scores the DSL oracle per arm."""
    scenario = load_scenario(name)
    arms = [arm] if arm else scenario.arms
    seed_list = seeds if seeds is not None else scenario.seeds
    records: list[RunRecord] = []
    for arm_name in arms:
        for variables, is_sweep in _sweep_points(scenario):
            records.extend(_records_for_arm(scenario, arm_name, seed_list, variables, is_sweep))
    return records


def scenarios_for_experiment(experiment: str) -> list[str]:
    out = []
    for name in list_scenarios():
        try:
            sc = load_scenario(name)
        except Exception:
            continue
        if sc.experiment == experiment:
            out.append(name)
    return out


def run_experiment(experiment: str) -> list[RunRecord]:
    records: list[RunRecord] = []
    for name in scenarios_for_experiment(experiment):
        records.extend(run_scenario(name))
    return records
