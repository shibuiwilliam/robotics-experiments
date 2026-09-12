"""統計補助（C14）：対応ありのブートストラップ信頼区間、割合のWilson区間
（.claude/rules/experiments.md「実行規則」）。

比較実験（EXP-03のアブレーション等）は同一入力に対する対応ありの比較にし、
`paired_bootstrap_ci()` を使う。割合は `wilson_interval()` を使う。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def paired_bootstrap_ci(
    a: Sequence[float],
    b: Sequence[float],
    n_resamples: int = 2000,
    ci: float = 0.95,
    seed: int = 0,
    statistic: str = "mean_diff",
) -> dict[str, float]:
    """対応ありの2群 `a`（例：条件つき）と `b`（例：条件なし）の差の
    ブートストラップ信頼区間。

    `a[i]` と `b[i]` は同一の評価対象（同一時刻・同一エピソード等）に対する値である
    こと（対応あり比較）。ペアをまとめてリサンプルする（各リサンプルで同じインデックス
    集合を a・b の両方に適用する）ことで対応関係を保つ。

    戻り値：`{"point_estimate", "ci_low", "ci_high", "n"}`。`point_estimate` は
    観測データそのものでの差の平均（`statistic="mean_diff"` の場合 mean(a - b)）。

    例：a=[2,2,2], b=[1,1,1] なら差は常に1 -> point_estimate=1.0, ci_low=ci_high=1.0。
    """
    if len(a) != len(b):
        raise ValueError("a と b は同じ長さ（対応あり）である必要があります")
    if len(a) == 0:
        raise ValueError("a/b が空です")
    if statistic != "mean_diff":
        raise ValueError(f"未対応の statistic: {statistic}")

    a_arr = np.asarray(a, dtype=float)
    b_arr = np.asarray(b, dtype=float)
    n = len(a_arr)
    diffs = a_arr - b_arr
    point_estimate = float(diffs.mean())

    rng = np.random.default_rng(seed)
    resample_means = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        resample_means[i] = diffs[idx].mean()

    alpha = 1.0 - ci
    lo = float(np.quantile(resample_means, alpha / 2))
    hi = float(np.quantile(resample_means, 1.0 - alpha / 2))
    return {"point_estimate": point_estimate, "ci_low": lo, "ci_high": hi, "n": float(n)}


def wilson_interval(successes: int, trials: int, ci: float = 0.95) -> dict[str, float]:
    """割合（検知率など）に対する Wilson score interval（正規近似より小標本で頑健）。

    戻り値：`{"point_estimate", "ci_low", "ci_high"}`。

    例：wilson_interval(successes=9, trials=10, ci=0.95) の point_estimate == 0.9
        （区間は 1.0 未満のやや広めの範囲になる。小標本ゆえ単純な正規近似より安全）。
    """
    if trials <= 0:
        raise ValueError("trials は正である必要があります")
    if not (0.0 < ci < 1.0):
        raise ValueError("ci は (0,1) の範囲である必要があります")

    p_hat = successes / trials
    z = _normal_quantile(1.0 - (1.0 - ci) / 2.0)
    denom = 1.0 + z**2 / trials
    center = p_hat + z**2 / (2 * trials)
    margin = z * math.sqrt(p_hat * (1 - p_hat) / trials + z**2 / (4 * trials**2))
    lo = (center - margin) / denom
    hi = (center + margin) / denom
    return {
        "point_estimate": p_hat,
        "ci_low": max(0.0, lo),
        "ci_high": min(1.0, hi),
    }


def _normal_quantile(p: float) -> float:
    """標準正規分布の分位点（逆累積分布関数）。Acklam の有理近似（scipy 非依存）。"""
    if not (0.0 < p < 1.0):
        raise ValueError("p は (0,1) の範囲である必要があります")
    # Peter Acklam のアルゴリズム（広く使われる有理近似、絶対誤差 < 1.15e-9）。
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]
    p_low = 0.02425
    p_high = 1 - p_low
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        )
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
    )


__all__ = ["paired_bootstrap_ci", "wilson_interval"]
