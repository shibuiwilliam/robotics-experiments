"""概念プローブ（設計書 §5.2、bridge-worldmodel ルール）。

潜在状態から概念の成立確率を出す学習器。**まず線形プローブ**にするのが方針で、これは潜在の
品質検査を兼ねる（設計書 §5.2）。非線形が必要になったら「潜在が概念を線形分離できない」
というシグナルとして WM 改善候補に回す——プローブを複雑にして隠さない。

昇格（``candidate`` → ``active``）は 3 条件の **AND**（不変条件に準じる強さの規約）:

- AUROC ≥ 0.85
- ECE ≤ 0.05
- 整合残差 ≤ 既存 active の中央値

「静的精度（AUROC）だけで active にする」のは CLAUDE.md の禁止事項である。較正（ECE）と
動的整合（整合残差）を欠いたプローブは、よく当たるのに信念を壊す。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import BridgeError, ProbePromotionError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from sbp.bridge.calibration import Calibrator
    from sbp.core.config import BridgeSettings

__all__ = [
    "LinearProbe",
    "ProbeKind",
    "ProbeMetrics",
    "ProbeOutput",
    "ProbeSpec",
    "ProbeStatus",
]

log = get_logger(__name__)


class ProbeKind(StrEnum):
    """プローブの種類（設計書 §5.2）。"""

    CLASSIFIER = "classifier"
    """概念の成立確率 φ_C: Z → [0,1]。"""
    REGRESSOR = "regressor"
    """属性の値分布 ψ_p: Z → Dist(V_p)。"""
    DETECTOR = "detector"
    """時空間局在を伴う検出。"""


class ProbeStatus(StrEnum):
    """プローブの身分。``active`` 化は昇格ゲートを通ること。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class ProbeMetrics(BaseModel):
    """プローブの評価指標（設計書 §5.2 の登録メタデータ）。

    Attributes:
        auroc: 静的な識別性能。
        ece: 期待較正誤差（Expected Calibration Error）。較正の良さ。
        alignment_residual: 整合残差 ‖A(f(z,a)) − T(A(z),a)‖。WM とオントロジーの可換性の破れ。
        n_samples: 評価に使った標本数。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    auroc: float = Field(ge=0.0, le=1.0)
    ece: float = Field(ge=0.0, le=1.0)
    alignment_residual: float = Field(ge=0.0)
    n_samples: int = Field(ge=1)


class ProbeSpec(BaseModel):
    """プローブの登録メタデータ（設計書 §5.2）。

    Attributes:
        probe_id: プローブの IRI。
        grounds: 接地する概念または属性の IRI。
        kind: プローブの種類。
        wm_version: 対応する WM 版の IRI。潜在空間が変われば別のプローブになる。
        latent_level: 使う潜在の階層（``fast`` / ``mid`` / ``slow``）。
        metrics: 評価指標。
        status: 身分。
        validity_sites: 有効な拠点。空なら全拠点。
        operating_modes: 有効な運転モード。空なら全モード。
        linear: 線形プローブかどうか。偽なら「潜在が線形分離できなかった」記録になる。
        ontology_version: 登録時のオントロジー版。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    probe_id: str
    grounds: str
    kind: ProbeKind
    wm_version: str
    latent_level: str = "mid"
    metrics: ProbeMetrics | None = None
    status: ProbeStatus = ProbeStatus.CANDIDATE
    validity_sites: tuple[str, ...] = ()
    operating_modes: tuple[str, ...] = ()
    linear: bool = True
    ontology_version: str

    @model_validator(mode="after")
    def _active_requires_metrics(self) -> Self:
        """``active`` なプローブは評価指標を持たなければならない。

        指標の無い active は「検証されていないものを信じている」状態であり、
        設計書 §5.2 の採用基準を空洞にする。
        """
        if self.status is ProbeStatus.ACTIVE and self.metrics is None:
            raise ValueError(
                "an active probe must carry metrics; promotion requires AUROC / ECE / "
                "alignment residual (design §5.2)"
            )
        return self

    def applies_to(self, *, site: str | None = None, operating_mode: str | None = None) -> bool:
        """この拠点・運転モードで有効かどうか。

        Args:
            site: 拠点識別子。
            operating_mode: 運転モード。
        """
        if site is not None and self.validity_sites and site not in self.validity_sites:
            return False
        return not (
            operating_mode is not None
            and self.operating_modes
            and operating_mode not in self.operating_modes
        )


@dataclass(frozen=True, slots=True)
class ProbeOutput:
    """プローブの出力（設計書 §5.6）。

    認識的不確実性（モデルの知識不足。追加観測で減る）と偶然的不確実性（観測固有のばらつき）を
    分けて返す。エピステミック・スケジューラは前者だけを対象にする。

    Attributes:
        probabilities: 較正済みの成立確率（標本ごと）。
        raw_scores: 較正前の生スコア。``belief`` に入れず ``evidence`` に残す（不変条件 7）。
        epistemic: 認識的不確実性。
        aleatoric: 偶然的不確実性。
    """

    probabilities: NDArray[np.float64]
    raw_scores: NDArray[np.float64]
    epistemic: NDArray[np.float64]
    aleatoric: NDArray[np.float64]

    def __len__(self) -> int:
        """標本数。"""
        return int(self.probabilities.shape[0])


def auroc(labels: NDArray[np.bool_], scores: NDArray[np.float64]) -> float:
    """AUROC を順位和（Mann-Whitney U）で計算する。

    同順位を 0.5 として扱う。外部ライブラリを増やさないために自前で実装している
    （依存を増やさない方針）。

    Args:
        labels: 真ラベル。
        scores: スコア（高いほど正例と判定）。

    Raises:
        BridgeError: 正例または負例が 1 つも無い場合（AUROC が定義されない）。
    """
    positives = int(labels.sum())
    negatives = int((~labels).sum())
    if positives == 0 or negatives == 0:
        raise BridgeError(
            f"AUROC is undefined with {positives} positives and {negatives} negatives; "
            "evaluation needs both classes"
        )
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1, dtype=np.float64)
    # 同順位の平均順位にそろえる
    unique, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    for index in range(len(unique)):
        if counts[index] > 1:
            mask = inverse == index
            ranks[mask] = ranks[mask].mean()
    rank_sum = float(ranks[labels].sum())
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def expected_calibration_error(
    labels: NDArray[np.bool_], probabilities: NDArray[np.float64], *, bins: int = 10
) -> float:
    """ECE（期待較正誤差）を等幅ビンで計算する。

    「確率 0.8 と言ったものが本当に 8 割成立するか」を測る。AUROC が高くても ECE が悪い
    プローブは、順位は正しいが確率として使えない——信念更新に入れると事後が歪む。

    Args:
        labels: 真ラベル。
        probabilities: 較正済み確率。
        bins: ビン数。
    """
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(probabilities)
    error = 0.0
    for lower, upper in pairwise(edges):
        in_bin = (probabilities > lower) & (probabilities <= upper)
        if lower == 0.0:
            in_bin |= probabilities == 0.0
        count = int(in_bin.sum())
        if count == 0:
            continue
        accuracy = float(labels[in_bin].mean())
        confidence = float(probabilities[in_bin].mean())
        error += (count / total) * abs(accuracy - confidence)
    return error


class LinearProbe:
    """線形プローブ（ロジスティック回帰）。

    潜在 z から概念の成立確率を出す。実装は勾配降下による単純なロジスティック回帰で、
    **弱ラベル（時間バッグ）での学習**に対応する（:mod:`sbp.bridge.mil`）。

    重みを公開するのは意図的である。線形プローブの重みは「潜在のどの方向が概念に対応するか」
    を示し、WM の潜在設計を検査する材料になる（設計書 §5.2 の「潜在の品質検査を兼ねる」）。
    """

    def __init__(self, *, dim: int, seed: int = 0) -> None:
        """Args:
        dim: 潜在の次元。
        seed: 重み初期化の乱数シード。
        """
        self._dim = dim
        rng = np.random.default_rng(seed)
        self._weights = rng.normal(0.0, 0.01, size=dim)
        self._bias = 0.0
        self._calibrator: Calibrator | None = None
        self._trained = False

    @property
    def dim(self) -> int:
        """このプローブが期待する潜在次元。

        プローブが WM について知るのはこれだけである（ADR-0007 の境界）。モデル族を
        差し替えても、次元が合えばプローブはそのまま動く。
        """
        return self._dim

    @property
    def weights(self) -> NDArray[np.float64]:
        """学習した重み（潜在の各次元の寄与）。WM の帰属計算にそのまま渡せる。"""
        return self._weights.copy()

    @property
    def is_trained(self) -> bool:
        """学習済みかどうか。"""
        return self._trained

    def logits(self, latents: NDArray[np.float64]) -> NDArray[np.float64]:
        """生のロジット（較正前のスコア）。

        Args:
            latents: 潜在（``(標本数, 次元)``）。
        """
        if latents.shape[1] != self._dim:
            raise BridgeError(
                f"latent dimension mismatch: probe expects {self._dim}, got {latents.shape[1]}"
            )
        return latents @ self._weights + self._bias

    def fit(
        self,
        latents: NDArray[np.float64],
        labels: NDArray[np.bool_],
        *,
        epochs: int = 300,
        learning_rate: float = 0.1,
        weight_decay: float = 1e-4,
        sample_weights: NDArray[np.float64] | None = None,
    ) -> Self:
        """強ラベルで学習する（完全教師の場合）。

        Args:
            latents: 潜在。
            labels: 真ラベル。
            epochs: 反復回数。
            learning_rate: 学習率。
            weight_decay: L2 正則化の係数。
            sample_weights: 標本ごとの重み（信頼性モデル由来の重みを渡す）。

        Returns:
            自分自身（メソッドチェーン用）。
        """
        targets = labels.astype(np.float64)
        weights = (
            np.ones_like(targets) if sample_weights is None else sample_weights.astype(np.float64)
        )
        weights = weights / weights.sum() * len(weights)
        for _ in range(epochs):
            predictions = _sigmoid(self.logits(latents))
            error = (predictions - targets) * weights
            grad_w = latents.T @ error / len(targets) + weight_decay * self._weights
            grad_b = float(error.mean())
            self._weights -= learning_rate * grad_w
            self._bias -= learning_rate * grad_b
        self._trained = True
        return self

    def apply_gradient(
        self,
        grad_weights: NDArray[np.float64],
        grad_bias: float,
        *,
        learning_rate: float,
        weight_decay: float = 0.0,
    ) -> None:
        """勾配 1 ステップを適用する。

        弱ラベル学習（:mod:`sbp.bridge.mil`）が損失の形を決め、パラメータの更新はここに
        委ねる。学習則をプローブ側に集めることで、MIL 以外の学習（能動学習、連合学習）を
        足すときも更新規則が 1 箇所で済む。

        Args:
            grad_weights: 重みの勾配。
            grad_bias: バイアスの勾配。
            learning_rate: 学習率。
            weight_decay: L2 正則化の係数。
        """
        self._weights -= learning_rate * (grad_weights + weight_decay * self._weights)
        self._bias -= learning_rate * grad_bias

    def mark_trained(self) -> None:
        """学習済みとして印を付ける（外部の学習ルーチンから呼ぶ）。"""
        self._trained = True

    def attach_calibrator(self, calibrator: Calibrator) -> None:
        """較正器を取り付ける。

        Args:
            calibrator: 較正器（温度スケーリングなど）。
        """
        self._calibrator = calibrator

    def predict(self, latents: NDArray[np.float64], *, ensemble_noise: float = 0.0) -> ProbeOutput:
        """成立確率を返す（較正を通す）。

        不変条件 7 により、較正されていない値は ``belief`` に入れられない。較正器が
        取り付けられていなければ**例外にする**（素のシグモイドを確率として通さない）。

        Args:
            latents: 潜在。
            ensemble_noise: アンサンブルの代わりに重みへ加えるノイズの大きさ。
                認識的不確実性の見積りに使う（0 なら不確実性は 0 とする）。

        Raises:
            BridgeError: 未学習、または較正器が無い場合。
        """
        if not self._trained:
            raise BridgeError("probe is not trained; call fit() first")
        if self._calibrator is None:
            raise BridgeError(
                "probe has no calibrator; uncalibrated scores must not become beliefs "
                "(invariant 7). Attach a calibrator first."
            )
        raw = self.logits(latents)
        calibrated = self._calibrator.apply(raw)

        if ensemble_noise > 0.0:
            # 重みに摂動を与えた複数の予測のばらつきを認識的不確実性とみなす。
            rng = np.random.default_rng(0)
            members = []
            for _ in range(5):
                perturbed = self._weights + rng.normal(0.0, ensemble_noise, size=self._dim)
                members.append(_sigmoid(latents @ perturbed + self._bias))
            stack = np.stack(members)
            epistemic = stack.var(axis=0)
        else:
            epistemic = np.zeros_like(calibrated)

        # 偶然的不確実性は較正後の確率そのものの分散（ベルヌーイ分散）。
        aleatoric = calibrated * (1.0 - calibrated)
        return ProbeOutput(
            probabilities=calibrated,
            raw_scores=raw,
            epistemic=epistemic,
            aleatoric=aleatoric,
        )

    def evaluate(
        self,
        latents: NDArray[np.float64],
        labels: NDArray[np.bool_],
        *,
        alignment_residual: float,
    ) -> ProbeMetrics:
        """評価指標を計算する。

        整合残差は外から渡す。これはプローブ単体では測れない量（WM の遷移とオントロジーの
        記号遷移の可換性）であり、:mod:`sbp.bridge.alignment` が計算する。

        Args:
            latents: 評価用の潜在。
            labels: 真ラベル。
            alignment_residual: 整合残差。

        Returns:
            評価指標。
        """
        output = self.predict(latents)
        return ProbeMetrics(
            auroc=auroc(labels, output.raw_scores),
            ece=expected_calibration_error(labels, output.probabilities),
            alignment_residual=alignment_residual,
            n_samples=len(labels),
        )


def promote(
    spec: ProbeSpec,
    *,
    settings: BridgeSettings,
    active_alignment_residuals: tuple[float, ...],
) -> ProbeSpec:
    """昇格ゲート（設計書 §5.2 の採用基準）。3 条件の **AND**。

    - AUROC ≥ 閾値
    - ECE ≤ 閾値
    - 整合残差 ≤ 既存 active の中央値

    「静的精度だけで active にする」のは CLAUDE.md の禁止事項。満たさなければ例外を投げ、
    呼び出し側に ``candidate`` のまま残させる（黙って active にしない）。

    Args:
        spec: 昇格させたいプローブ。
        settings: 閾値（``promote_min_auroc`` / ``promote_max_ece``）。
        active_alignment_residuals: 既存 active プローブの整合残差。空なら最初のプローブで
            あり、中央値の条件は適用しない（比較対象が無い）。

    Returns:
        ``status=active`` にした新しい :class:`ProbeSpec`。

    Raises:
        ProbePromotionError: いずれかの条件を満たさない場合。満たさない条件をすべて挙げる。
    """
    if spec.metrics is None:
        raise ProbePromotionError(
            f"probe {spec.probe_id} has no metrics; evaluate it before promotion"
        )
    metrics = spec.metrics
    failures: list[str] = []

    if metrics.auroc < settings.promote_min_auroc:
        failures.append(
            f"AUROC {metrics.auroc:.3f} < {settings.promote_min_auroc} (discrimination)"
        )
    if metrics.ece > settings.promote_max_ece:
        failures.append(f"ECE {metrics.ece:.3f} > {settings.promote_max_ece} (calibration)")
    if active_alignment_residuals:
        median = float(np.median(np.asarray(active_alignment_residuals, dtype=np.float64)))
        if metrics.alignment_residual > median:
            failures.append(
                f"alignment residual {metrics.alignment_residual:.3f} > median of active "
                f"probes {median:.3f} (dynamic grounding)"
            )

    if failures:
        raise ProbePromotionError(
            f"probe {spec.probe_id} cannot be promoted; all three criteria must hold "
            f"(design §5.2). Failed: " + "; ".join(failures)
        )

    log.info(
        "bridge.probe_promoted",
        probe_id=spec.probe_id,
        grounds=spec.grounds,
        auroc=metrics.auroc,
        ece=metrics.ece,
        alignment_residual=metrics.alignment_residual,
    )
    return spec.model_copy(update={"status": ProbeStatus.ACTIVE})


def _sigmoid(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """数値的に安定なシグモイド。"""
    return np.where(x >= 0, 1.0 / (1.0 + np.exp(-x)), np.exp(x) / (1.0 + np.exp(x)))
