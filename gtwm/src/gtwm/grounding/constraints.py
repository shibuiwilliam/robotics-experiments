"""制約損失（C6）：`ontology/shapes/*.ttl` の3本の SHACL 形状に対応する、微分可能な
product t-norm 制約損失（world_model.md「損失」節、`.claude/rules/world_model.md`）。

product t-norm：AND(x,y) = x*y、NOT(x) = 1-x。真理値はすべて [0,1] の「充足度」として
扱い、違反確率 = 1 - 充足度 とする。3本の形状はいずれも `sh:severity sh:Warning` の
ソフト制約であり、ここではその「破っている度合い」を連続値の罰則として WM の学習に返す。
"""

from __future__ import annotations

import torch
from torch import Tensor


def single_zone_violation(zone_probs: Tensor) -> Tensor:
    """`gt:PalletSingleLocationShape`：同一時刻に複数ゾーンを主張していないか。

    zone_probs: [...,n_zones]（α のゾーン分類確率）。1個のゾーンに確率が集中していれば
    充足度が高い（= 違反度が低い）とみなす：違反度 = 1 - max_zone_prob。
    """
    satisfaction = zone_probs.max(dim=-1).values
    return 1.0 - satisfaction


def zone_capacity_violation(zone_probs: Tensor, capacities: Tensor) -> Tensor:
    """`gt:ZoneCapacityShape`：ゾーン容量超過。

    zone_probs: [N,n_zones]（N個体ぶんのゾーン所属確率）、capacities: [n_zones]。
    期待占有数 = Σ_i zone_probs[i,z] をゾーンごとに求め、容量超過分をシグモイドで
    [0,1] の違反度に変換する（超過が大きいほど1に近づく）。
    """
    expected_occupancy = zone_probs.sum(dim=0)  # [n_zones]
    excess = expected_occupancy - capacities
    return torch.sigmoid(excess)  # [n_zones]


def hazmat_adjacency_violation(
    hazard_prob_a: Tensor,
    hazard_prob_b: Tensor,
    incompatible_prob: Tensor,
    adjacent_mask: Tensor,
) -> Tensor:
    """`gt:HazmatAdjacencyShape`：危険物クラスの隣接禁止。

    全て [0,1] の充足度/確率として、product t-norm の AND（要素積）で結合する：
    違反度 = adjacent * hazard_a * hazard_b * incompatible。
    """
    return adjacent_mask * hazard_prob_a * hazard_prob_b * incompatible_prob


def constraint_loss(
    zone_probs: Tensor,
    capacities: Tensor | None = None,
    hazmat_terms: tuple[Tensor, Tensor, Tensor, Tensor] | None = None,
) -> Tensor:
    """3本の違反度の平均を単一スカラー損失として返す（`wm/train.py` の `loss_weights.constraint`）。

    容量・危険物項は対応する情報（ゾーン容量、危険物クラス確率）が無ければ省略できる
    （スロット単位の学習では単一ゾーン制約のみが常に適用可能）。
    """
    terms = [single_zone_violation(zone_probs).mean()]
    if capacities is not None:
        terms.append(zone_capacity_violation(zone_probs, capacities).mean())
    if hazmat_terms is not None:
        terms.append(hazmat_adjacency_violation(*hazmat_terms).mean())
    return torch.stack(terms).mean()


__all__ = [
    "single_zone_violation",
    "zone_capacity_violation",
    "hazmat_adjacency_violation",
    "constraint_loss",
]
