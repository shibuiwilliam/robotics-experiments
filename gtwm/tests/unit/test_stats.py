"""`eval/stats.py` の手計算できる小さな例（.claude/rules/experiments.md）。"""

from __future__ import annotations

import pytest

from gtwm.eval import stats

pytestmark = pytest.mark.unit


def test_paired_bootstrap_ci_constant_diff() -> None:
    # 全ペアの差が常に1.0 -> どうリサンプルしても差は1.0のまま。
    result = stats.paired_bootstrap_ci([2.0, 2.0, 2.0], [1.0, 1.0, 1.0], n_resamples=100, seed=0)
    assert result["point_estimate"] == pytest.approx(1.0)
    assert result["ci_low"] == pytest.approx(1.0)
    assert result["ci_high"] == pytest.approx(1.0)
    assert result["n"] == 3.0


def test_paired_bootstrap_ci_length_mismatch() -> None:
    with pytest.raises(ValueError):
        stats.paired_bootstrap_ci([1.0, 2.0], [1.0])


def test_paired_bootstrap_ci_brackets_true_mean_diff() -> None:
    a = [3.0, 5.0, 4.0, 6.0, 5.0, 4.0, 7.0, 3.0]
    b = [1.0, 2.0, 2.0, 3.0, 2.0, 1.0, 3.0, 1.0]
    result = stats.paired_bootstrap_ci(a, b, n_resamples=2000, seed=42)
    true_mean_diff = sum(x - y for x, y in zip(a, b, strict=True)) / len(a)
    assert result["point_estimate"] == pytest.approx(true_mean_diff)
    assert result["ci_low"] <= result["point_estimate"] <= result["ci_high"]


def test_wilson_interval_matches_known_reference_value() -> None:
    # 9/10 の95%Wilson区間は標準的な参照値でおよそ [0.596, 0.982]（手計算で検証済み）。
    result = stats.wilson_interval(successes=9, trials=10, ci=0.95)
    assert result["point_estimate"] == pytest.approx(0.9)
    assert result["ci_low"] == pytest.approx(0.596, abs=1e-3)
    assert result["ci_high"] == pytest.approx(0.982, abs=1e-3)


def test_wilson_interval_requires_positive_trials() -> None:
    with pytest.raises(ValueError):
        stats.wilson_interval(0, 0)
