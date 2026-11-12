"""ベイズ的オンライン変化点検出（BOCPD、設計書 §5.3）。

プローブ出力の列からフルーエントの変化候補を見つける。bridge-worldmodel ルールにより
**自前実装**とし、``ruptures`` はオフライン評価にだけ使う（オンライン検出を外部ライブラリに
依存させない。レイテンシ階層 L1 < 1 s を自分で保証できる必要がある）。

実装は Adams & MacKay (2007) の run-length 事後分布を、ベルヌーイ観測（プローブの出力を
二値化したもの）と共役事前（Beta）で特殊化したもの。各時刻で「いまの区間が何ステップ続いて
いるか」の事後分布を保ち、run-length が 1 にリセットされる確率を変化点の確率とする。

**なぜオンラインか**: 設計書 §4.7 のレイテンシ階層で事象昇格は L1（拠点、< 1 s）に属する。
全系列を見てから切るオフライン手法では、観測が届くたびに過去をやり直すことになり間に合わない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from sbp.core.errors import BridgeError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = ["BocpdDetector", "ChangePoint", "detect_change_points"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChangePoint:
    """検出した変化点。

    Attributes:
        index: 変化が起きた標本の添字。
        probability: 変化点である事後確率。
        direction: 変化の向き（``rise`` = 偽→真、``fall`` = 真→偽）。
        run_length_before: 変化前の区間が続いていた長さ（標本数）。
    """

    index: int
    probability: float
    direction: str
    run_length_before: int


class BocpdDetector:
    """ベルヌーイ観測に対する BOCPD。

    ``hazard`` は「どの時点でも変化が起きる事前確率」。値が小さいほど変化に保守的になる。
    既定の ``1/120`` は、10 秒刻みで 20 分に 1 回程度の変化を事前に見込むことに相当する
    （設備の起動停止の頻度として妥当な範囲）。
    """

    def __init__(
        self,
        *,
        hazard: float = 1.0 / 120.0,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
        max_run_length: int = 500,
    ) -> None:
        """Args:
        hazard: 変化点の事前確率（ハザード率）。
        prior_alpha: Beta 事前の α（成立側の擬似観測数）。
        prior_beta: Beta 事前の β（不成立側の擬似観測数）。
        max_run_length: 保持する run-length の上限（計算量を抑えるため）。
        """
        if not 0.0 < hazard < 1.0:
            raise ValueError(f"hazard must be in (0, 1), got {hazard}")
        if prior_alpha <= 0.0 or prior_beta <= 0.0:
            raise ValueError("Beta prior parameters must be positive")
        self._hazard = hazard
        self._alpha0 = prior_alpha
        self._beta0 = prior_beta
        self._max_run = max_run_length

    def run_length_posterior(self, observations: NDArray[np.bool_]) -> NDArray[np.float64]:
        """各時刻の run-length 事後分布を返す（``(時刻数, run-length)``）。

        Args:
            observations: 二値観測の列。

        Raises:
            BridgeError: 観測が空の場合。
        """
        if len(observations) == 0:
            raise BridgeError("BOCPD needs at least one observation")

        n = len(observations)
        width = min(n + 1, self._max_run)
        # R[t, r] = P(run length = r at time t | observations up to t)
        posterior = np.zeros((n + 1, width), dtype=np.float64)
        posterior[0, 0] = 1.0

        # 各 run-length ごとの Beta 事後パラメータ
        alpha = np.full(width, self._alpha0, dtype=np.float64)
        beta = np.full(width, self._beta0, dtype=np.float64)

        # 新しい区間の予測確率（事前分布から。run-length 0 のときの値）
        prior_predictive_true = self._alpha0 / (self._alpha0 + self._beta0)

        for t in range(n):
            observation = bool(observations[t])
            # 継続する区間の予測確率（Beta-Bernoulli の事後予測）
            predictive = alpha / (alpha + beta) if observation else beta / (alpha + beta)
            # 新しい区間の予測確率は事前分布から取る。**ここが要点**で、変化点項に継続中の
            # run の予測確率を使うと、正規化で予測確率が打ち消されて変化点確率が
            # ハザード率一定になってしまう（データを一切見なくなる）。新しい区間は
            # 過去のデータを持たないので、その最初の観測は事前予測に従う。
            new_segment_predictive = (
                prior_predictive_true if observation else 1.0 - prior_predictive_true
            )

            growth = posterior[t, : width - 1] * predictive[: width - 1] * (1.0 - self._hazard)
            change = float(np.sum(posterior[t, :]) * self._hazard * new_segment_predictive)

            posterior[t + 1, 1:] = growth
            posterior[t + 1, 0] = change
            total = float(posterior[t + 1, :].sum())
            if total <= 0.0:  # pragma: no cover — 数値的に起きにくい
                posterior[t + 1, 0] = 1.0
            else:
                posterior[t + 1, :] /= total

            # Beta パラメータを進める（run-length が増えた分だけずらして更新）
            new_alpha = np.full(width, self._alpha0, dtype=np.float64)
            new_beta = np.full(width, self._beta0, dtype=np.float64)
            if observation:
                new_alpha[1:] = alpha[: width - 1] + 1.0
                new_beta[1:] = beta[: width - 1]
            else:
                new_alpha[1:] = alpha[: width - 1]
                new_beta[1:] = beta[: width - 1] + 1.0
            alpha, beta = new_alpha, new_beta

        return posterior

    def detect(
        self, observations: NDArray[np.bool_], *, min_bayes_factor: float = 5.0
    ) -> list[ChangePoint]:
        """変化点を検出する。

        判定は**ベイズ因子**（事後の変化点確率 ÷ 事前のハザード率）で行う。絶対閾値を使わない
        のは、適切な値がハザード率に依存してしまうためである。ハザード 1/120 なら変化点の
        事後確率は 0.15 程度にしかならないが、平常時の 0.004 に対しては 35 倍であり、
        「データが変化を支持している」ことは明白である。比で見れば、ハザード率を変えても
        同じ判定基準が使える。

        Args:
            observations: 二値観測の列。
            min_bayes_factor: 変化点とみなすベイズ因子の下限。5.0 は「変化を支持する証拠が
                事前の 5 倍以上」という意味で、Jeffreys の尺度では「positive」に相当する。

        Returns:
            検出した変化点（時刻順）。
        """
        if min_bayes_factor <= 1.0:
            raise ValueError(
                f"min_bayes_factor must exceed 1.0 (otherwise the prior alone passes), "
                f"got {min_bayes_factor}"
            )
        posterior = self.run_length_posterior(observations)
        # 各時刻の「run-length が 0（＝直前で変化した）」確率
        change_probability = posterior[1:, 0]

        out: list[ChangePoint] = []
        last_change = 0
        for index in range(1, len(observations)):
            probability = float(change_probability[index])
            if probability / self._hazard < min_bayes_factor:
                continue
            before = bool(observations[index - 1])
            after = bool(observations[index])
            if before == after:
                # 値が変わっていない箇所は変化点として報告しない（確率が立っても
                # フルーエントの変化ではないため、事象昇格の対象にならない）。
                continue
            out.append(
                ChangePoint(
                    index=index,
                    probability=probability,
                    direction="rise" if after else "fall",
                    run_length_before=index - last_change,
                )
            )
            last_change = index

        log.debug(
            "bridge.change_points_detected",
            count=len(out),
            n_observations=len(observations),
            min_bayes_factor=min_bayes_factor,
        )
        return out


def detect_change_points(
    probabilities: NDArray[np.float64],
    *,
    high: float = 0.7,
    low: float = 0.3,
    hazard: float = 1.0 / 120.0,
    min_bayes_factor: float = 5.0,
) -> list[ChangePoint]:
    """プローブの確率列から変化点を検出する（ヒステリシス込み）。

    ヒステリシス（設計書 §5.3 の「上昇 0.7 / 下降 0.3」）を先に適用して二値化し、
    その列に BOCPD をかける。ヒステリシスを先に置くのは、閾値付近の往復（ちらつき）を
    変化点の候補に上げない方が後段の重複抑制が軽くなるためである。

    Args:
        probabilities: プローブの較正済み確率。
        high: 上昇側の閾値（これを超えたら成立とみなす）。
        low: 下降側の閾値（これを下回ったら不成立とみなす）。
        hazard: BOCPD のハザード率。
        min_bayes_factor: 変化点とみなすベイズ因子の下限。

    Returns:
        検出した変化点。

    Raises:
        BridgeError: ``high <= low`` の場合（ヒステリシスとして機能しない）。
    """
    if high <= low:
        raise BridgeError(
            f"hysteresis needs high > low, got high={high} low={low}; "
            "equal thresholds would not suppress flicker at all"
        )
    binary = apply_hysteresis(probabilities, high=high, low=low)
    return BocpdDetector(hazard=hazard).detect(binary, min_bayes_factor=min_bayes_factor)


def apply_hysteresis(
    probabilities: NDArray[np.float64], *, high: float = 0.7, low: float = 0.3
) -> NDArray[np.bool_]:
    """ヒステリシスで確率列を二値化する（設計書 §5.3 の手順 2）。

    成立していない状態からは ``high`` を超えたときだけ成立に移り、成立している状態からは
    ``low`` を下回ったときだけ不成立に戻る。閾値 1 本だと境界付近で往復するが、2 本あれば
    その往復が消える。

    Args:
        probabilities: 較正済み確率。
        high: 上昇側の閾値。
        low: 下降側の閾値。
    """
    out = np.zeros(len(probabilities), dtype=np.bool_)
    state = bool(probabilities[0] >= high) if len(probabilities) else False
    for i, p in enumerate(probabilities):
        if state:
            if p < low:
                state = False
        elif p >= high:
            state = True
        out[i] = state
    return out
