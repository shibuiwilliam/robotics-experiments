from pathlib import Path

import pytest

from gtwm.grounding.exp10_harness import (
    CONDITIONS,
    Exp10Harness,
    compute_sus_score,
    generate_mock_cases,
)

pytestmark = pytest.mark.unit


def test_generate_mock_cases_returns_ten_deterministic_cases() -> None:
    cases1 = generate_mock_cases()
    cases2 = generate_mock_cases()
    assert len(cases1) == 10
    assert [c.case_id for c in cases1] == [c.case_id for c in cases2]
    # H4の5型のうち複数が10件に含まれる（1種類に偏らない）。
    assert len({c.discrepancy_type for c in cases1}) >= 3


def test_start_and_finish_case_records_duration_and_correctness(tmp_path: Path) -> None:
    harness = Exp10Harness(tmp_path / "exp10.sqlite")
    case = generate_mock_cases()[0]
    rid = harness.start_case("evaluator:1", case.case_id, "dashboard", order_index=0)
    elapsed = harness.finish_case(
        rid, resolution_given=case.correct_resolution, case=case, correction_reflected=True
    )
    assert elapsed >= 0.0
    summary = harness.summary()
    assert summary["dashboard_n"] == 1.0
    assert summary["dashboard_correct_rate"] == 1.0
    assert summary["dashboard_correction_reflected_rate"] == 1.0
    harness.close()


def test_finish_case_scores_incorrect_resolution(tmp_path: Path) -> None:
    harness = Exp10Harness(tmp_path / "exp10.sqlite")
    case = generate_mock_cases()[1]
    rid = harness.start_case("evaluator:1", case.case_id, "traditional", order_index=0)
    harness.finish_case(
        rid, resolution_given="明らかに違う答え", case=case, correction_reflected=False
    )
    summary = harness.summary()
    assert summary["traditional_correct_rate"] == 0.0
    assert summary["traditional_correction_reflected_rate"] == 0.0
    harness.close()


def test_finish_case_requires_prior_start(tmp_path: Path) -> None:
    harness = Exp10Harness(tmp_path / "exp10.sqlite")
    case = generate_mock_cases()[0]
    with pytest.raises(KeyError):
        harness.finish_case(
            "nonexistent", resolution_given="x", case=case, correction_reflected=True
        )
    harness.close()


def test_start_case_rejects_unknown_condition(tmp_path: Path) -> None:
    harness = Exp10Harness(tmp_path / "exp10.sqlite")
    with pytest.raises(ValueError):
        harness.start_case("evaluator:1", "exp10_case_01", "not_a_condition", order_index=0)
    harness.close()


def test_both_conditions_supported(tmp_path: Path) -> None:
    assert set(CONDITIONS) == {"traditional", "dashboard"}


def test_compute_sus_score_all_best_answers_gives_100() -> None:
    # 奇数問(肯定的)=5, 偶数問(否定的)=1 が最も好意的な回答パターン。
    answers = [5, 1, 5, 1, 5, 1, 5, 1, 5, 1]
    assert compute_sus_score(answers) == 100.0


def test_compute_sus_score_all_worst_answers_gives_zero() -> None:
    answers = [1, 5, 1, 5, 1, 5, 1, 5, 1, 5]
    assert compute_sus_score(answers) == 0.0


def test_compute_sus_score_neutral_gives_fifty() -> None:
    assert compute_sus_score([3] * 10) == 50.0


def test_compute_sus_score_rejects_wrong_length() -> None:
    with pytest.raises(ValueError):
        compute_sus_score([3] * 9)


def test_compute_sus_score_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        compute_sus_score([6] + [3] * 9)


def test_record_and_retrieve_sus_score(tmp_path: Path) -> None:
    harness = Exp10Harness(tmp_path / "exp10.sqlite")
    score = harness.record_sus("evaluator:1", [5, 1, 5, 1, 5, 1, 5, 1, 5, 1])
    assert score == 100.0
    harness.close()
