"""合成損失の各項（設計書 §4.6）。

```text
L = L_pred + λ_phys · L_phys + λ_probe · L_probe + λ_logic · L_logic + λ_align · L_align
```

**各項を個別に記録する**のが規約である（`.claude/rules/bridge-worldmodel.md`）。合計だけを
見ていると、整合損失が下がったのか予測損失が下がったのかが分からず、「記号に過剰適合して
現実を予測しなくなった」状態に気付けない。

**λ_probe と λ_align の項は外から渡す。** どちらもプローブ出力（記号の決定）を必要とし、それは
``bridge`` の責務である（ADR-0007 と同じ境界）。この層が持つのは**項の合成と重みのスケジュール**
だけで、項の中身は呼び出し側が与える。こうしておくと、学習コードがプローブの実装に引きずられ
ない。

**λ_align は後期に強める**（設計書 §4.6）。初期から強いと潜在が記号に過剰適合する——記号側の
遷移 T をなぞるだけの潜在になり、T が間違っている部分まで学んでしまう。整合残差は「潜在と記号が
どれだけ食い違うか」の測定値であって、ゼロにすべき目標ではない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import torch

from sbp.core.errors import WorldModelError

if TYPE_CHECKING:
    from torch import Tensor

__all__ = [
    "LossTerm",
    "LossWeights",
    "TermValues",
    "exclusivity_penalty",
    "smoothness_penalty",
]


class LossTerm(Protocol):
    """損失項の契約。潜在（と文脈）を受けてスカラーを返す。"""

    def __call__(self, latents: Tensor) -> Tensor:
        """項の値を返す。

        Args:
            latents: ``(バッチ, 潜在次元)``。
        """
        ...


@dataclass(frozen=True, slots=True)
class LossWeights:
    """合成損失の重みとスケジュール（設計書 §4.6）。

    Attributes:
        physics: λ_phys。物理則・保存量の重み。
        probe: λ_probe。弱ラベル（MIL）の重み。
        logic: λ_logic。オントロジー制約（t-ノルム緩和）の重み。
        align: λ_align。整合損失の最終的な重み。
        align_start_fraction: λ_align を効かせ始める進捗（0〜1）。既定 0.5 は「後半から」。
    """

    physics: float = 0.1
    probe: float = 1.0
    logic: float = 0.2
    align: float = 0.5
    align_start_fraction: float = 0.5

    def __post_init__(self) -> None:
        """重みとスケジュールの値域を検証する。"""
        for name, value in (
            ("physics", self.physics),
            ("probe", self.probe),
            ("logic", self.logic),
            ("align", self.align),
        ):
            if value < 0.0:
                raise WorldModelError(f"loss weight {name} must be non-negative, got {value}")
        if not 0.0 <= self.align_start_fraction < 1.0:
            raise WorldModelError(
                f"align_start_fraction must be in [0, 1), got {self.align_start_fraction}"
            )

    def align_at(self, progress: float) -> float:
        """進捗 ``progress``（0〜1）における λ_align。

        開始点から最終値まで線形に上げる。段階的に切り替える（ある時点で 0 から最終値へ跳ぶ）と
        損失が不連続になり、その時点で潜在が大きく動いて学習が崩れる。

        Args:
            progress: 学習の進捗（0 で開始、1 で終了）。

        Raises:
            WorldModelError: 進捗が 0〜1 の外の場合。
        """
        if not 0.0 <= progress <= 1.0:
            raise WorldModelError(f"progress must be in [0, 1], got {progress}")
        start = self.align_start_fraction
        if progress <= start:
            return 0.0
        ramp = (progress - start) / (1.0 - start)
        return self.align * ramp


@dataclass(frozen=True, slots=True)
class TermValues:
    """1 ステップの各項の値（個別にログする対象）。

    Attributes:
        prediction: 潜在予測損失。
        physics: 物理則項。
        probe: 弱ラベル項。
        logic: 論理制約項。
        align: 整合項。
        align_weight: そのステップで実際に使った λ_align（スケジュールの確認用）。
    """

    prediction: float
    physics: float = 0.0
    probe: float = 0.0
    logic: float = 0.0
    align: float = 0.0
    align_weight: float = 0.0

    def total(self, weights: LossWeights) -> float:
        """重み付き合計（記録用。勾配はテンソル側で計算する）。

        Args:
            weights: 重み。
        """
        return (
            self.prediction
            + weights.physics * self.physics
            + weights.probe * self.probe
            + weights.logic * self.logic
            + self.align_weight * self.align
        )

    def as_dict(self) -> dict[str, float]:
        """ログに出す形。"""
        return {
            "prediction": self.prediction,
            "physics": self.physics,
            "probe": self.probe,
            "logic": self.logic,
            "align": self.align,
            "align_weight": self.align_weight,
        }


def smoothness_penalty(latents: Tensor) -> Tensor:
    """潜在の変化率に上限を与える物理則項（L_phys の最小形）。

    設計書 §4.6 の L_phys は「物理則・保存量」だが、ドメイン非依存に書ける最小の制約は
    **慣性**である: 物理系の状態は 1 刻みで任意に飛べない。連続する潜在の差の二乗平均を
    罰則にすると、センサノイズを潜在にそのまま通すエンコーダが抑えられる。

    ドメイン固有の保存量（質量・エネルギー）は呼び出し側が :class:`LossTerm` として渡す。

    Args:
        latents: ``(時刻, 潜在次元)``。時間順に並んでいること。

    Raises:
        WorldModelError: 2 刻み未満の場合（差が取れない）。
    """
    if latents.shape[0] < 2:
        raise WorldModelError(
            f"smoothness needs at least 2 consecutive latents, got {latents.shape[0]}"
        )
    velocity = latents[1:] - latents[:-1]
    return (velocity**2).mean()


def exclusivity_penalty(first: Tensor, second: Tensor) -> Tensor:
    """排他制約の t-ノルム緩和（L_logic）。

    オントロジーの ``mutuallyExclusiveWith``（例: ``Running`` ⊥ ``UnderMaintenance``）を
    微分可能な罰則にする。Łukasiewicz の t-ノルムに対応する形で ``relu(p + q − 1)`` を使う:
    両方の確率の和が 1 を超えた分だけ罰する。

    **ハード制約にしない理由。** 排他の衝突は「拒否」ではなく**不一致候補**である
    （設計書 §3.9）。学習で完全に禁じてしまうと、実際に衝突している観測を潜在が表現できなく
    なり、検出すべき食い違いが消える。

    Args:
        first: 一方のフルーエントの確率（``(バッチ,)``）。
        second: 他方の確率。

    Raises:
        WorldModelError: 形が合わない場合。
    """
    if first.shape != second.shape:
        raise WorldModelError(
            f"exclusivity needs matching shapes, got {first.shape} and {second.shape}"
        )
    return torch.relu(first + second - 1.0).mean()
