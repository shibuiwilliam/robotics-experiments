"""較正（設計書 §5.6、bridge-worldmodel ルール）。

不変条件 7 により、``belief`` には**較正済みの値だけ**を入れる。生スコアは ``evidence`` に残す。

- 分類: 温度スケーリング + 信頼性ダイアグラムの定期監査。
- 回帰: conformal による予測区間と被覆率の監視。

温度スケーリングを選ぶ理由は、**順位を変えずに確率だけを直す**ことである。AUROC（順位の
性能）を保ったまま ECE（確率の正しさ）を改善できるため、「識別はできているが確率が歪んで
いる」という最も普通の状態に対する最小の介入になる。
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

import numpy as np

from sbp.core.errors import CalibrationError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from numpy.typing import NDArray

__all__ = ["Calibrator", "ConformalRegressor", "TemperatureScaler", "reliability_diagram"]

log = get_logger(__name__)


@runtime_checkable
class Calibrator(Protocol):
    """較正器の抽象。生スコアを較正済み確率に変える。"""

    def apply(self, raw_scores: NDArray[np.float64]) -> NDArray[np.float64]:
        """生スコアを較正済み確率に変換する。

        Args:
            raw_scores: 較正前のスコア（ロジット）。
        """
        ...

    @property
    def is_fitted(self) -> bool:
        """較正パラメータが推定済みかどうか。"""
        ...


class TemperatureScaler:
    """温度スケーリング（設計書 §5.6）。

    ``p = sigmoid(logit / T)`` の ``T`` を保留データで推定する。``T > 1`` は自信過剰を、
    ``T < 1`` は自信不足を意味する。順位は変わらないので AUROC は保たれる。
    """

    def __init__(self) -> None:
        self._temperature = 1.0
        self._fitted = False

    @property
    def temperature(self) -> float:
        """推定した温度。"""
        return self._temperature

    @property
    def is_fitted(self) -> bool:
        """推定済みかどうか。"""
        return self._fitted

    def fit(
        self,
        raw_scores: NDArray[np.float64],
        labels: NDArray[np.bool_],
        *,
        max_iterations: int = 200,
        learning_rate: float = 0.05,
    ) -> Self:
        """保留データで温度を推定する（負の対数尤度を最小化）。

        Args:
            raw_scores: 保留データの生スコア。
            labels: 保留データの真ラベル。
            max_iterations: 反復回数。
            learning_rate: 学習率。

        Raises:
            CalibrationError: 保留データが空、または片方のクラスしか無い場合。
                片方だけでは温度が定まらない（どんな T でも尤度が単調になる）。
        """
        if len(raw_scores) == 0:
            raise CalibrationError("cannot fit a calibrator on an empty hold-out set")
        if labels.all() or not labels.any():
            raise CalibrationError(
                "calibration needs both positive and negative examples in the hold-out set; "
                "with a single class the temperature is not identifiable"
            )
        targets = labels.astype(np.float64)
        # log T を最適化する（T > 0 を保つ）
        log_t = 0.0
        for _ in range(max_iterations):
            temperature = float(np.exp(log_t))
            scaled = raw_scores / temperature
            probabilities = _sigmoid(scaled)
            # dNLL/d(log T) = mean((p - y) * (-logit / T))
            gradient = float(np.mean((probabilities - targets) * (-raw_scores / temperature)))
            log_t -= learning_rate * gradient
        self._temperature = float(np.exp(log_t))
        self._fitted = True
        log.debug("bridge.calibrated", temperature=self._temperature, n_samples=len(raw_scores))
        return self

    def apply(self, raw_scores: NDArray[np.float64]) -> NDArray[np.float64]:
        """生スコアを較正済み確率に変換する。

        Raises:
            CalibrationError: 未推定の較正器を使おうとした場合（不変条件 7）。
        """
        if not self._fitted:
            raise CalibrationError(
                "temperature scaler is not fitted; applying it would produce uncalibrated "
                "values that must not enter belief (invariant 7)"
            )
        return _sigmoid(raw_scores / self._temperature)


class ConformalRegressor:
    """conformal 予測区間（設計書 §5.6）。

    回帰プローブの予測に、**指定した被覆率を経験的に保証する**区間を付ける。分布の仮定を
    置かずに「保留データでの残差の分位点」を使うため、誤差分布が正規でなくても被覆が保たれる。
    """

    def __init__(self, *, coverage: float = 0.9) -> None:
        """Args:
        coverage: 目標被覆率（0 < coverage < 1）。
        """
        if not 0.0 < coverage < 1.0:
            raise ValueError(f"coverage must be in (0, 1), got {coverage}")
        self._coverage = coverage
        self._quantile: float | None = None

    @property
    def is_fitted(self) -> bool:
        """推定済みかどうか。"""
        return self._quantile is not None

    @property
    def coverage(self) -> float:
        """目標被覆率。"""
        return self._coverage

    def fit(self, predictions: NDArray[np.float64], actuals: NDArray[np.float64]) -> Self:
        """保留データの残差から区間幅を決める。

        Args:
            predictions: 予測値。
            actuals: 実測値。

        Raises:
            CalibrationError: 保留データが空の場合。
        """
        if len(predictions) == 0:
            raise CalibrationError("cannot fit a conformal regressor on an empty hold-out set")
        residuals = np.abs(actuals - predictions)
        # 有限標本補正つきの分位点（conformal の標準的な取り方）
        n = len(residuals)
        level = min(1.0, np.ceil((n + 1) * self._coverage) / n)
        self._quantile = float(np.quantile(residuals, level))
        return self

    def interval(self, prediction: float) -> tuple[float, float]:
        """予測値に対する区間を返す。

        Args:
            prediction: 予測値。

        Raises:
            CalibrationError: 未推定の場合。
        """
        if self._quantile is None:
            raise CalibrationError("conformal regressor is not fitted")
        return (prediction - self._quantile, prediction + self._quantile)

    def empirical_coverage(
        self, predictions: NDArray[np.float64], actuals: NDArray[np.float64]
    ) -> float:
        """実測の被覆率（監視指標。設計書 §13.1 の「区間被覆率」）。

        Args:
            predictions: 予測値。
            actuals: 実測値。
        """
        if self._quantile is None:
            raise CalibrationError("conformal regressor is not fitted")
        covered = np.abs(actuals - predictions) <= self._quantile
        return float(covered.mean())


def reliability_diagram(
    labels: NDArray[np.bool_], probabilities: NDArray[np.float64], *, bins: int = 10
) -> list[tuple[float, float, int]]:
    """信頼性ダイアグラムのデータ（設計書 §5.6 の定期監査）。

    「確率 p と言ったものが実際にどれだけ成立したか」をビンごとに返す。較正が崩れると
    対角線から離れるため、ECE の数値だけでなく**どこで崩れているか**が分かる。

    Args:
        labels: 真ラベル。
        probabilities: 較正済み確率。
        bins: ビン数。

    Returns:
        ``(平均予測確率, 実測成立率, 標本数)`` のリスト（標本の無いビンは除く）。
    """
    edges = np.linspace(0.0, 1.0, bins + 1)
    out: list[tuple[float, float, int]] = []
    for lower, upper in pairwise(edges):
        in_bin = (probabilities > lower) & (probabilities <= upper)
        if lower == 0.0:
            in_bin |= probabilities == 0.0
        count = int(in_bin.sum())
        if count == 0:
            continue
        out.append((float(probabilities[in_bin].mean()), float(labels[in_bin].mean()), count))
    return out


def _sigmoid(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """数値的に安定なシグモイド。"""
    return np.where(x >= 0, 1.0 / (1.0 + np.exp(-x)), np.exp(x) / (1.0 + np.exp(x)))
