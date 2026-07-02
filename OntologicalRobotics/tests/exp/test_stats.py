"""統計モジュールのテスト（既知データで scipy 手計算と一致）。"""

import numpy as np
import pytest
from scipy import stats as sps

from orx.exp.stats import bootstrap_diff_ci, compare_conditions, mcnemar_exact, wilcoxon_signed


def test_mcnemar_matches_binomtest() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(10, 0) == pytest.approx(sps.binomtest(0, 10, 0.5).pvalue)
    assert mcnemar_exact(8, 2) == pytest.approx(sps.binomtest(2, 10, 0.5).pvalue)


def test_wilcoxon_all_equal_is_one() -> None:
    assert wilcoxon_signed([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_wilcoxon_matches_scipy() -> None:
    xs = [0.9, 0.8, 0.95, 0.7, 0.85, 0.9]
    ys = [0.5, 0.6, 0.55, 0.4, 0.5, 0.45]
    assert wilcoxon_signed(xs, ys) == pytest.approx(float(sps.wilcoxon(xs, ys).pvalue))


def test_bootstrap_ci_deterministic_and_sane() -> None:
    rng1, rng2 = np.random.default_rng(0), np.random.default_rng(0)
    a = [True] * 18 + [False] * 2
    b = [False] * 15 + [True] * 5
    ci1 = bootstrap_diff_ci(a, b, rng1)
    ci2 = bootstrap_diff_ci(a, b, rng2)
    assert ci1 == ci2  # シード決定的
    assert 0.0 < ci1[0] < ci1[1] <= 1.0  # 明確な差 → CIは0を跨がない


def test_compare_conditions_summary() -> None:
    a = [True] * 18 + [False] * 2
    b = [False] * 15 + [True, True, True, True, True]
    result = compare_conditions(
        "OR-full",
        "OR-no-identity",
        a,
        b,
        np.random.default_rng(0),
        metric_a=[0.9] * 20,
        metric_b=[0.3] * 20,
        metric_name="identity_f1",
    )
    assert result.n == 20
    assert result.success_rate_a == 0.9
    assert result.mcnemar_p < 0.05
    assert result.wilcoxon_p is not None and result.wilcoxon_p < 0.05
