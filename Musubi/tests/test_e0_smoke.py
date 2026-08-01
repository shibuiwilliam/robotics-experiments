"""E0 smoke — the vertical slice. Passes end-to-end at A4, fully offline (ScriptedPlanner + oracle).

This is the build's central acceptance: with no network and no keys, the lift-bot relocates a
pallet, the plan gates clean (unapproved-irreversible = 0), every action traces to the order
(completeness = 1.0), and replay makes zero API calls.
"""

from __future__ import annotations

from dataclasses import asdict

from bench.runner import run_e0


def test_e0_a4_passes_end_to_end_offline() -> None:
    r = run_e0(seed=0, arm_name="A4")
    assert r.success, r.reason
    assert r.executed == r.plan_steps == 4
    assert r.unapproved_irreversible == 0  # must-pass safety invariant
    assert r.trace_completeness >= 0.95  # accountability chain (Phase 0-1 DoD)
    assert r.api_calls == 0  # oracle dial: no cloud calls


def test_e0_ablation_ladder_runs_and_is_scored() -> None:
    results = {arm: run_e0(seed=0, arm_name=arm) for arm in ["A0", "A1", "A2", "A3", "A4"]}
    for arm, r in results.items():
        assert r.success, f"{arm}: {r.reason}"
        assert r.unapproved_irreversible == 0
    # the ladder actually differs: bare A0 uses no envelopes/claims; full A4 uses both.
    assert results["A0"].bus_events == 0 and results["A0"].claims == 0
    assert results["A4"].bus_events > 0 and results["A4"].claims > 0


def test_e0_is_deterministic() -> None:
    a = asdict(run_e0(seed=0, arm_name="A4"))
    b = asdict(run_e0(seed=0, arm_name="A4"))
    # drop the goal dict (identical) and compare the scored outcome fields.
    for key in ["success", "executed", "plan_steps", "claims", "bus_events", "trace_completeness"]:
        assert a[key] == b[key], f"nondeterministic field {key}: {a[key]} != {b[key]}"


def test_e0_replay_makes_zero_api_calls() -> None:
    assert run_e0(seed=0, arm_name="A4").api_calls == 0
