"""Oracle engine tests — safe evaluator, predicate registry, over_repeats, scoring."""

from __future__ import annotations

import pytest

from bench.oracle import (
    OracleError,
    OracleSpec,
    RunContext,
    evaluate,
    evaluate_endpoint,
    evaluate_must,
    score_oracle,
)


def _ctx(**extras: object) -> RunContext:
    gt = extras.pop("ground_truth", {})
    vars_ = extras.pop("vars", {})
    return RunContext(
        arm="A4", seed=0, ground_truth=dict(gt), vars=dict(vars_), extras=dict(extras)
    )


def test_detection_recall_and_vars() -> None:
    ctx = _ctx(
        ground_truth={"planted": ["a", "b", "c"]},
        detected={"a", "b", "c"},
        vars={"level": 0.95},
    )
    assert evaluate("detection_recall('planted') >= level", ctx) is True
    ctx2 = _ctx(ground_truth={"planted": ["a", "b", "c"]}, detected={"a"}, vars={"level": 0.95})
    assert evaluate("detection_recall('planted') >= level", ctx2) is False


def test_must_predicate() -> None:
    assert evaluate_must("no_unapproved_irreversible", _ctx(unapproved_irreversible=0)) is True
    assert evaluate_must("no_unapproved_irreversible", _ctx(unapproved_irreversible=2)) is False


def test_boolean_and_not() -> None:
    ctx = _ctx(audit_report_submitted=True, ground_truth={"p": ["x"]}, detected={"x"})
    assert evaluate("audit_report_submitted() and detection_recall('p') >= 1.0", ctx) is True
    assert evaluate("not audit_report_submitted()", ctx) is False


def test_over_repeats_endpoint() -> None:
    c1 = _ctx(scan_cost=2.0, full_scan_cost=6.0)
    c2 = _ctx(scan_cost=4.0, full_scan_cost=6.0)
    assert evaluate_endpoint("over_repeats('mean', scan_cost()) < full_scan_cost()", [c1, c2])
    assert not evaluate_endpoint("over_repeats('mean', scan_cost()) >= full_scan_cost()", [c1, c2])


def test_bare_predicate_endpoint_requires_all() -> None:
    ok = [_ctx(report_calibration_ok=True), _ctx(report_calibration_ok=True)]
    bad = [_ctx(report_calibration_ok=True), _ctx(report_calibration_ok=False)]
    assert evaluate_endpoint("report_calibration_ok()", ok)
    assert not evaluate_endpoint("report_calibration_ok()", bad)


def test_ci_low_aggregator() -> None:
    ctxs = [_ctx(scan_cost=float(v)) for v in (1.0, 1.0, 1.0, 1.0)]
    # zero variance -> ci_low == mean == 1.0
    assert evaluate_endpoint("over_repeats('ci_low', scan_cost()) >= 1.0", ctxs)


def test_score_oracle_end_to_end() -> None:
    spec = OracleSpec(
        success="audit_report_submitted() and detection_recall('planted') >= level",
        must=["no_person_identity_binding", "no_unapproved_irreversible"],
        endpoints=["over_repeats('mean', scan_cost()) < full_scan_cost()"],
    )
    contexts = [
        _ctx(
            audit_report_submitted=True,
            detected={"a", "b", "c"},
            scan_cost=2.0,
            full_scan_cost=6.0,
            ground_truth={"planted": ["a", "b", "c"]},
            vars={"level": 0.95},
        )
        for _ in range(3)
    ]
    result = score_oracle(spec, contexts)
    assert result.passed
    assert result.success_rate == 1.0
    assert result.must_ok and result.endpoints


def test_unsafe_expression_rejected() -> None:
    with pytest.raises(OracleError):
        evaluate("__import__('os').system('echo hi')", _ctx())
    with pytest.raises(OracleError):
        evaluate("1 + 2", _ctx())  # bare arithmetic not allowed
