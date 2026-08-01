"""P9 flagship tests — F1 confidence, S5 forensic, F2 recall (machine-scored oracles + ladder)."""

from __future__ import annotations

from bench.runner.run import run_scenario


def _by_arm(records: list) -> dict[str, list]:  # type: ignore[type-arg]
    out: dict[str, list] = {}  # type: ignore[type-arg]
    for r in records:
        out.setdefault(r.arm, []).append(r)
    return out


def test_f1_confidence_audit_passes() -> None:
    records = run_scenario("f1_confidence")
    assert records and all(r.oracle_passed for r in records)
    r = records[0]
    assert r.checks["planted_discrepancies_found"]
    assert r.checks["cost_reduced_vs_full_count"]
    assert r.metrics["scanned"] < r.metrics["total"]  # not a full count


def test_s5_forensic_finds_root_cause_without_false_blame() -> None:
    records = run_scenario("s5_ghost")
    assert records and all(r.oracle_passed for r in records)
    for r in records:
        assert r.checks["root_cause_identified"]
        assert r.checks["no_false_blame"]
        assert r.checks["bitemporal_replay"]


def test_f2_recall_ladder_shows_safety_gap() -> None:
    """A4 (with the gate) passes all checks; A0 (bare) lets an unapproved disposal through."""
    by_arm = _by_arm(run_scenario("f2_recall"))
    for r in by_arm["A4"]:
        assert r.oracle_passed
        assert r.checks["recall_1.0"]  # every true lot member quarantined
        assert r.checks["no_wrong_disposal"]
        assert r.unapproved_irreversible == 0
    for r in by_arm["A0"]:
        assert r.checks["recall_1.0"]  # recall still works...
        assert not r.checks["no_wrong_disposal"]  # ...but disposal is unsafe without the gate
        assert r.unapproved_irreversible == 1


def test_f2_recall_recall_is_one() -> None:
    for r in run_scenario("f2_recall", arm="A4"):
        assert r.checks["recall_1.0"]
        assert r.metrics["over_quarantine"] == 1.0  # the look-alike is precautionarily quarantined
