"""Run driver — expand a scenario across arms × seeds, score the oracle, collect records.

The record is the unit the scoreboard ingests. Success (oracle) is judged from god-view truth,
never the system's beliefs.
"""

from __future__ import annotations

from bench.oracle import OracleReport
from bench.runner.assemble import build_from_scenario
from bench.runner.flagships import is_flagship, run_flagship
from bench.runner.record import RunRecord
from bench.scenarios.loader import Scenario, list_scenarios, load_scenario


def run_scenario(
    name: str, arm: str | None = None, seeds: list[int] | None = None
) -> list[RunRecord]:
    """Run one scenario across its arms × seeds (or a single arm/seed subset). Scores the oracle."""
    scenario = load_scenario(name)
    arms = [arm] if arm else scenario.arms
    seed_list = seeds if seeds is not None else scenario.seeds
    records: list[RunRecord] = []
    for arm_name in arms:
        for seed in seed_list:
            if is_flagship(scenario):
                records.append(run_flagship(scenario, arm_name, seed))
                continue
            episode, goal, world, _perturbations = build_from_scenario(scenario, arm_name, seed)
            result = episode.run(goal)
            oracle = OracleReport.evaluate(scenario.oracle, result, world)
            records.append(
                RunRecord(
                    scenario=scenario.name,
                    experiment=scenario.experiment,
                    arm=arm_name,
                    seed=seed,
                    oracle_passed=oracle.passed,
                    success=result.success,
                    unapproved_irreversible=result.unapproved_irreversible,
                    trace_completeness=result.trace_completeness,
                    claims=result.claims,
                    bus_events=result.bus_events,
                    api_calls=result.api_calls,
                    plan_steps=result.plan_steps,
                    executed=result.executed,
                    reason=result.reason,
                    checks=oracle.checks,
                )
            )
    return records


def scenarios_for_experiment(experiment: str) -> list[str]:
    out = []
    for name in list_scenarios():
        try:
            sc: Scenario = load_scenario(name)
        except Exception:
            continue
        if sc.experiment == experiment:
            out.append(name)
    return out


def run_experiment(experiment: str) -> list[RunRecord]:
    """Run every scenario mapped to an experiment (E0..E7)."""
    records: list[RunRecord] = []
    for name in scenarios_for_experiment(experiment):
        records.extend(run_scenario(name))
    return records
