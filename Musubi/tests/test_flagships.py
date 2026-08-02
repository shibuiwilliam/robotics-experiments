"""P9/v2 flagship tests — F1 confidence, S5 forensic, F2 recall scored by the DSL oracle engine."""

from __future__ import annotations

from bench.runner.drivers import drive_confidence_audit
from bench.runner.run import run_scenario
from bench.scenarios import load_scenario


def _primary(records: list) -> list:  # type: ignore[type-arg]
    """Records from the primary (non-sweep) evaluation — these gate scenario pass."""
    return [r for r in records if r.metrics.get("is_sweep", 0.0) == 0.0]


def _by_arm(records: list) -> dict[str, list]:  # type: ignore[type-arg]
    out: dict[str, list] = {}  # type: ignore[type-arg]
    for r in records:
        out.setdefault(r.arm, []).append(r)
    return out


def test_f1_confidence_audit_passes_all_arms() -> None:
    records = _primary(run_scenario("f1_confidence"))
    assert records
    for arm, recs in _by_arm(records).items():
        assert all(r.oracle_passed for r in recs), f"{arm} failed: {recs[0].checks}"
        # confidence-driven: scanned strictly fewer than a full count
        assert recs[0].metrics["scan_cost"] < recs[0].metrics["full_scan_cost"]


def test_f1_confidence_cost_curve_is_monotone() -> None:
    """The audit-level sweep produces a non-decreasing confidence-cost curve."""
    records = run_scenario("f1_confidence", arm="A4")
    curve: dict[float, float] = {}
    for r in records:
        if r.metrics.get("is_sweep") == 1.0:
            curve[r.metrics["sweep.audit_level"]] = r.metrics["scan_cost"]
    levels = sorted(curve)
    assert len(levels) >= 3
    costs = [curve[x] for x in levels]
    assert costs == sorted(costs)  # cost rises (or holds) as the audit level rises
    assert costs[-1] >= costs[0]


def test_f1_drilldown_returns_observation_binding_mediation_chain() -> None:
    ctx = drive_confidence_audit(load_scenario("f1_confidence"), "A4", 0, {})
    drill = ctx.extras["drilldown"]
    assert drill, "expected a drilldown chain for a detected divergence"
    chain = next(iter(drill.values()))
    assert len(chain) == 3  # mediation -> binding -> observation
    for iri in chain:
        assert ctx.claims is not None and ctx.claims.get(iri) is not None  # all IRIs resolvable


def test_s5_forensic_finds_root_cause_without_false_blame() -> None:
    records = _primary(run_scenario("s5_ghost"))
    assert records and all(r.oracle_passed for r in records)
    assert all(r.metrics["forensic_accuracy"] == 1.0 for r in records)


def test_f2_recall_ladder_shows_safety_gap() -> None:
    """A4 (with the gate) passes; A0 (bare) lets an unapproved disposal through."""
    by_arm = _by_arm(_primary(run_scenario("f2_recall")))
    for r in by_arm["A4"]:
        assert r.oracle_passed
        assert r.unapproved_irreversible == 0
        assert r.metrics["overquarantine_rate"] <= 0.25
    for r in by_arm["A0"]:
        assert not r.oracle_passed  # bare coupling fails the safety oracle
        assert r.unapproved_irreversible == 1


def test_c3_redteam_defense_ladder() -> None:
    """All three attacks land at A0; are fully defended at A3/A4 (attack_success == 0)."""
    by_arm = _by_arm(_primary(run_scenario("c3_redteam")))
    assert all(r.metrics["attack_success_count"] == 3.0 for r in by_arm["A0"])
    for arm in ("A3", "A4"):
        for r in by_arm[arm]:
            assert r.oracle_passed
            assert r.metrics["attack_success_count"] == 0.0
            assert r.unapproved_irreversible == 0
