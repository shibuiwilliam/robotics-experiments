"""弱教師学習（多重インスタンス学習、設計書 §5.2）。

業務記録は「いつ正確に成立したか」を教えてくれない。「WO-123 軸受交換 完了 9/12」という
記録から分かるのは、**9/12 のどこかで** UnderMaintenance が成立したということだけである。

そこで :class:`TimeBag`（時間バッグ）を単位にする。バッグ内の**少なくとも 1 区間**で概念が
成立する、という仮定（MIL 仮定）で学習する。これは業務記録が持つ情報の粗さを、嘘をつかずに
そのまま扱う方法である——「バッグ内のすべての標本が正例」と仮定すると、記録の粗さを
精密さと取り違えることになる。

バッグの重みは情報源の信頼性モデルから導く（設計書 §5.2「ラベル重み」）。誤記の多い情報源の
バッグは軽くする。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from sbp.core.errors import BridgeError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from numpy.typing import NDArray

    from sbp.bridge.probes import LinearProbe
    from sbp.core.time import Interval

__all__ = ["TimeBag", "fit_with_bags", "mil_loss"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TimeBag:
    """弱ラベルの単位（設計書 §5.2、付録 C）。

    「この区間のどこかで概念が成立する（または成立しない）」という主張。

    Attributes:
        concept: 対象概念の IRI またはローカル名。
        entity: 対象実体の IRI。
        interval: 時間区間。
        positive: 正例バッグ（区間内のどこかで成立）なら真、負例バッグ
            （区間内のどこでも成立しない）なら偽。
        weight: バッグの重み。情報源の信頼性から導く（誤記の多い情報源は軽く）。
        source_record: 由来の記録 IRI（来歴に残す）。
    """

    concept: str
    entity: str
    interval: Interval
    positive: bool
    weight: float = 1.0
    source_record: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.weight <= 1.0:
            raise ValueError(f"bag weight must be in (0, 1], got {self.weight}")

    def sample_indices(self, timestamps: Sequence[object]) -> list[int]:
        """バッグに入る標本の添字を返す。

        Args:
            timestamps: 標本時刻の列。
        """
        from datetime import datetime  # noqa: PLC0415 — 型だけの用途

        return [
            i
            for i, t in enumerate(timestamps)
            if isinstance(t, datetime) and self.interval.contains_instant(t)
        ]


def mil_loss(
    probabilities: NDArray[np.float64],
    *,
    positive: bool,
    weight: float = 1.0,
    epsilon: float = 1e-9,
) -> float:
    """1 バッグの MIL 損失（noisy-OR 定式化）。

    正例バッグでは「少なくとも 1 つが正」を ``1 - Π(1 - p_i)`` でモデル化し、その対数尤度を
    最大化する。負例バッグでは全標本が負でなければならないため、各 ``p_i`` を直接下げる。

    noisy-OR を使う理由は、**バッグ内の最大値だけを見る max 定式化より勾配が安定する**こと
    である。max だと 1 標本にしか勾配が流れず、長いバッグで学習が進まない。

    Args:
        probabilities: バッグ内の標本の成立確率。
        positive: 正例バッグかどうか。
        weight: バッグの重み。
        epsilon: 対数の下限。

    Returns:
        損失（小さいほど良い）。

    Raises:
        BridgeError: バッグが空の場合。
    """
    if len(probabilities) == 0:
        raise BridgeError("MIL loss is undefined for an empty bag")
    clipped = np.clip(probabilities, epsilon, 1.0 - epsilon)
    if positive:
        # noisy-OR: P(バッグが正) = 1 - Π(1 - p_i)
        log_all_negative = float(np.sum(np.log(1.0 - clipped)))
        bag_positive = 1.0 - np.exp(log_all_negative)
        return -weight * float(np.log(max(bag_positive, epsilon)))
    # 負例バッグでは全標本が負である対数尤度をそのまま返す。
    return -weight * float(np.sum(np.log(1.0 - clipped)))


def fit_with_bags(
    probe: LinearProbe,
    latents: NDArray[np.float64],
    bags: Sequence[TimeBag],
    timestamps: Sequence[object],
    *,
    epochs: int = 200,
    learning_rate: float = 0.1,
    weight_decay: float = 1e-4,
) -> dict[str, float]:
    """時間バッグ（弱ラベル）でプローブを学習する。

    各バッグの noisy-OR 損失を合計し、勾配降下で重みを更新する。勾配は解析的に求める
    （自動微分を持ち込まないのは、依存を増やさず挙動を追えるようにするため）。

    Args:
        probe: 学習するプローブ。
        latents: 潜在（``(標本数, 次元)``）。
        bags: 時間バッグの列。
        timestamps: 標本時刻（バッグと潜在を対応づけるため）。
        epochs: 反復回数。
        learning_rate: 学習率。
        weight_decay: L2 正則化の係数。

    Returns:
        学習の記録（``initial_loss`` / ``final_loss`` / ``n_bags`` / ``n_samples_covered``）。

    Raises:
        BridgeError: バッグが空、またはどのバッグにも標本が入らない場合。
    """
    if not bags:
        raise BridgeError("cannot train with zero bags")

    resolved: list[tuple[list[int], TimeBag]] = []
    for bag in bags:
        indices = bag.sample_indices(timestamps)
        if indices:
            resolved.append((indices, bag))
        else:
            log.warning(
                "bridge.empty_bag",
                concept=bag.concept,
                entity=bag.entity,
                interval=str(bag.interval),
            )
    if not resolved:
        raise BridgeError(
            "no bag contains any sample; check that bag intervals overlap the latent timeline"
        )

    covered = {i for indices, _ in resolved for i in indices}

    def total_loss() -> float:
        total = 0.0
        for indices, bag in resolved:
            probabilities = _sigmoid(probe.logits(latents[indices]))
            total += mil_loss(probabilities, positive=bag.positive, weight=bag.weight)
        return total / len(resolved)

    initial = total_loss()

    for _ in range(epochs):
        grad_w = np.zeros(latents.shape[1], dtype=np.float64)
        grad_b = 0.0
        for indices, bag in resolved:
            subset = latents[indices]
            logits = probe.logits(subset)
            probabilities = _sigmoid(logits)
            clipped = np.clip(probabilities, 1e-9, 1.0 - 1e-9)

            if bag.positive:
                # d/dp_i of -log(1 - Π(1-p_j)) = -Π_{j≠i}(1-p_j) / (1 - Π(1-p_j))
                log_all_neg = float(np.sum(np.log(1.0 - clipped)))
                prod_all = float(np.exp(log_all_neg))
                bag_positive = max(1.0 - prod_all, 1e-9)
                # Π_{j≠i}(1-p_j) = prod_all / (1-p_i)
                others = prod_all / (1.0 - clipped)
                d_loss_d_p = -others / bag_positive
            else:
                # d/dp_i of -Σ log(1-p_i) = 1/(1-p_i)
                d_loss_d_p = 1.0 / (1.0 - clipped)

            # dp/dlogit = p(1-p)
            d_loss_d_logit = bag.weight * d_loss_d_p * clipped * (1.0 - clipped)
            grad_w += subset.T @ d_loss_d_logit
            grad_b += float(d_loss_d_logit.sum())

        scale = 1.0 / len(resolved)
        probe.apply_gradient(
            grad_w * scale,
            grad_b * scale,
            learning_rate=learning_rate,
            weight_decay=weight_decay,
        )

    probe.mark_trained()
    final = total_loss()
    log.info(
        "bridge.mil_trained",
        n_bags=len(resolved),
        n_samples_covered=len(covered),
        initial_loss=initial,
        final_loss=final,
    )
    return {
        "initial_loss": initial,
        "final_loss": final,
        "n_bags": float(len(resolved)),
        "n_samples_covered": float(len(covered)),
    }


def _sigmoid(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """数値的に安定なシグモイド。"""
    return np.where(x >= 0, 1.0 / (1.0 + np.exp(-x)), np.exp(x) / (1.0 + np.exp(x)))
