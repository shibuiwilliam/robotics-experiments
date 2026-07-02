"""C10 統計 — 対応のある比較（PROJECT.md §8.3）。

反実仮想リプレイにより条件間で入力が同一になるため、対応のある検定を使う:
成否は McNemar（厳密二項）、連続指標は Wilcoxon 符号順位、区間はブートストラップ。
"""

from __future__ import annotations

import numpy as np
from scipy import stats as sps

from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree


class PairedComparison(StrictModel):
    """2条件の対応のある比較結果。"""

    condition_a: str
    condition_b: str
    n: int
    success_rate_a: float
    success_rate_b: float
    discordant_a_only: int  # Aのみ成功
    discordant_b_only: int  # Bのみ成功
    mcnemar_p: float
    diff_ci_low: float  # 成功率差 (A-B) の95%ブートストラップCI
    diff_ci_high: float
    wilcoxon_metric: str | None = None
    wilcoxon_p: float | None = None


def mcnemar_exact(a_only: int, b_only: int) -> float:
    """McNemar 厳密検定（不一致ペアの二項検定、両側）。"""
    n = a_only + b_only
    if n == 0:
        return 1.0
    return float(sps.binomtest(min(a_only, b_only), n, 0.5, alternative="two-sided").pvalue)


def wilcoxon_signed(xs: list[float], ys: list[float]) -> float:
    """Wilcoxon 符号順位検定（対応あり、両側）。全差ゼロなら p=1.0。"""
    if len(xs) != len(ys):
        raise ValueError("対応のある系列の長さが一致しません")
    diffs = [x - y for x, y in zip(xs, ys, strict=True)]
    if all(abs(d) < 1e-12 for d in diffs):
        return 1.0
    result = sps.wilcoxon(xs, ys)
    return float(result.pvalue)


def bootstrap_diff_ci(
    successes_a: list[bool],
    successes_b: list[bool],
    rng: np.random.Generator,
    n_resamples: int = 10_000,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """対応のあるブートストラップによる成功率差 (A−B) の信頼区間。"""
    if len(successes_a) != len(successes_b):
        raise ValueError("対応のある系列の長さが一致しません")
    n = len(successes_a)
    if n == 0:
        return 0.0, 0.0
    a = np.asarray(successes_a, dtype=float)
    b = np.asarray(successes_b, dtype=float)
    idx = rng.integers(0, n, size=(n_resamples, n))
    diffs = (a[idx] - b[idx]).mean(axis=1)
    lo, hi = np.quantile(diffs, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def paired_comparisons(
    primary: str,
    baselines: list[str],
    success_by_cond: dict[str, list[bool]],
    metric_by_cond: dict[str, list[float]],
    metric_name: str,
    seed: int,
) -> list[PairedComparison]:
    """primary vs 各 baseline の対比較を一括生成する（シナリオ runner 共通, R-3d）。

    success_by_cond / metric_by_cond は per-seed のベクトル（条件→[seed毎値]）。
    ブートストラップRNG は seed から決定的に派生し、結果の再現性を保つ。
    """
    rng = SeedTree(seed).child("scenario-bootstrap").rng()
    out: list[PairedComparison] = []
    for b in baselines:
        out.append(
            compare_conditions(
                primary,
                b,
                success_by_cond[primary],
                success_by_cond[b],
                rng,
                metric_a=metric_by_cond[primary],
                metric_b=metric_by_cond[b],
                metric_name=metric_name,
            )
        )
    return out


def compare_conditions(
    condition_a: str,
    condition_b: str,
    successes_a: list[bool],
    successes_b: list[bool],
    rng: np.random.Generator,
    metric_a: list[float] | None = None,
    metric_b: list[float] | None = None,
    metric_name: str | None = None,
) -> PairedComparison:
    """成否のMcNemar＋成功率差CI（任意で連続指標のWilcoxon）をまとめる。"""
    n = len(successes_a)
    a_only = sum(1 for x, y in zip(successes_a, successes_b, strict=True) if x and not y)
    b_only = sum(1 for x, y in zip(successes_a, successes_b, strict=True) if y and not x)
    ci_low, ci_high = bootstrap_diff_ci(successes_a, successes_b, rng)
    wilcoxon_p = None
    if metric_a is not None and metric_b is not None:
        wilcoxon_p = wilcoxon_signed(metric_a, metric_b)
    return PairedComparison(
        condition_a=condition_a,
        condition_b=condition_b,
        n=n,
        success_rate_a=sum(successes_a) / n if n else 0.0,
        success_rate_b=sum(successes_b) / n if n else 0.0,
        discordant_a_only=a_only,
        discordant_b_only=b_only,
        mcnemar_p=mcnemar_exact(a_only, b_only),
        diff_ci_low=ci_low,
        diff_ci_high=ci_high,
        wilcoxon_metric=metric_name,
        wilcoxon_p=wilcoxon_p,
    )
