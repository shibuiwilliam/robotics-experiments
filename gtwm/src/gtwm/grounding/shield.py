"""計画のシールド（`wm/planner.py` の `ShieldFn` 契約に適合する候補フィルタ）。

world_model.md「計画」節：「候補軌道は α で記号化し、SHACL 違反の候補を除外する
（シールド）」。ここでは候補行動列をロールアウトし、α（`Probe.zone_probs`）で
ゾーン所属確率へ記号化したうえで、`ontology/shapes/*.ttl` の3本の SHACL 形状のうち
容量制約（`gt:ZoneCapacityShape`）を「進入禁止ゾーン＝容量0」として適用する。

実装ノート（session 06 の設計判断、docs/status.md に記載）：256候補×horizon本の
SHACL/pyshacl 検証をエピソードごとに実行するのはレイテンシ上非現実的なため、
`grounding/constraints.py` が学習損失用に定義済みの違反度関数と同じ意味論
（`single_zone_violation` 系：ゾーン所属確率がそのまま「違反度」）を再利用し、
「候補のロールアウトを記号化した結果、禁止ゾーンの所属確率が閾値を超える」候補を
棄却する。これは `gt:ZoneCapacityShape`（capacity=0 のゾーンへの所属者数>0を違反と
みなす）の決定的な記号版チェックを、連続値の予測確率に対して行っていることに相当する
（本実行時、`kg.store.KGStore.validate` を使った離散版のダブルチェックに拡張できる
よう `RestrictedZoneShield.symbolize_and_check` を分離してある）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import torch
from torch import Tensor

from gtwm.grounding.constraints import single_zone_violation, zone_capacity_violation
from gtwm.grounding.probes import N_ZONES, Probe
from gtwm.wm.dynamics import Dynamics


@dataclass
class RestrictedZoneShield:
    """`restricted_zone_idx` への進入を禁じるシールド（`gt:ZoneCapacityShape`, capacity=0）。

    `slots_t`/`cond` は計画開始時点の状態（`wm/planner.py MPPIPlanner.plan` に渡すのと
    同じもの）に固定し、シールド呼び出しのたびにその状態から候補行動列をロールアウトする。
    """

    dynamics: Dynamics
    probe: Probe
    slots_t: Tensor  # [1,K,D]
    cond: Tensor | None
    restricted_zone_idx: int
    violation_threshold: float = 0.5
    controlled_slot_idx: int | None = None  # None なら全スロットの最大違反度を見る

    def symbolize_and_check(self, candidate_actions: Tensor) -> Tensor:
        """candidate_actions: [N,h,Da] -> violation_degree: [N]（`gt:ZoneCapacityShape`
        の連続値版：禁止ゾーンに所属する確率の、ホライズン全体・対象スロットでの最大値）。
        """
        n, h, _da = candidate_actions.shape
        slots_batch = self.slots_t.expand(n, -1, -1)
        cond_batch = None if self.cond is None else self.cond.expand(n, -1)
        with torch.no_grad():
            rollout = self.dynamics.rollout(slots_batch, cond_batch, candidate_actions, h)
            mean_rollout = rollout.mean(dim=0)  # アンサンブル平均 [N,h,K,D]
            zone_probs = self.probe.zone_probs(mean_rollout, calibrated=True)  # [N,h,K,n_zones]
        restricted_prob = zone_probs[..., self.restricted_zone_idx]  # [N,h,K]
        if self.controlled_slot_idx is not None:
            restricted_prob = restricted_prob[:, :, self.controlled_slot_idx]  # [N,h]
        else:
            restricted_prob = restricted_prob.amax(dim=-1)  # [N,h]（スロット方向の最大）
        return restricted_prob.amax(dim=-1)  # [N]（ホライズン方向の最大＝最悪ケース）

    def __call__(self, candidate_actions: Tensor) -> Tensor:
        """`wm.planner.ShieldFn` 契約：candidate_actions -> mask[N]（True=許容）。"""
        violation_degree = self.symbolize_and_check(candidate_actions)
        return violation_degree < self.violation_threshold


@dataclass
class ComplianceShield:
    """`ontology/shapes/*.ttl` のうち連続値で近似検証できる2本
    （`gt:PalletSingleLocationShape` の単一ゾーン制約、`gt:ZoneCapacityShape` の
    ゾーン容量制約）を同時に検証する汎用シールド。`RestrictedZoneShield`
    （単一禁止ゾーンのみを見るEXP-06専用の特殊ケース）を置き換えるものではなく、
    WHAT-IF エンジンや将来の計画呼び出しから使える一般形として追加する
    （session 07、docs/status.md に設計判断を記載）。

    危険物隣接制約（`gt:HazmatAdjacencyShape`）は対象外：α に危険物クラスを
    予測するヘッドが無く（`grounding/probes.py` の Probe は existence/zone/
    floor/state のみ）、これは EXP-06 が同じ理由で対象外にしたのと同じ簡略化
    である（`grounding/shield.py` 冒頭のRestrictedZoneShieldのコメント参照）。

    `zone_capacities[z]` に0を指定したゾーンは「進入禁止」として扱える
    （`RestrictedZoneShield` の単一ゾーン版はこれの特殊ケースに相当する）。
    """

    dynamics: Dynamics
    probe: Probe
    slots_t: Tensor  # [1,K,D]
    cond: Tensor | None
    zone_capacities: Tensor = field(default_factory=lambda: torch.full((N_ZONES,), float("inf")))
    single_zone_threshold: float = 0.5
    capacity_violation_threshold: float = 0.5

    def symbolize_and_check(self, candidate_actions: Tensor) -> tuple[Tensor, Tensor]:
        """candidate_actions: [N,h,Da] -> (single_zone_violation[N], capacity_violation[N])。

        いずれも「ホライズン全体での最悪ケース」（最大値）を候補ごとに返す
        （`RestrictedZoneShield.symbolize_and_check` と同じ最悪ケース規約）。
        """
        n, h, _da = candidate_actions.shape
        slots_batch = self.slots_t.expand(n, -1, -1)
        cond_batch = None if self.cond is None else self.cond.expand(n, -1)
        with torch.no_grad():
            rollout = self.dynamics.rollout(slots_batch, cond_batch, candidate_actions, h)
            mean_rollout = rollout.mean(dim=0)  # アンサンブル平均 [N,h,K,D]
            zone_probs = self.probe.zone_probs(mean_rollout, calibrated=True)  # [N,h,K,n_zones]

        single_zone = single_zone_violation(zone_probs)  # [N,h,K]
        single_zone_worst = single_zone.amax(dim=(1, 2))  # [N]

        # `zone_capacity_violation` は個体軸（占有数を足し合わせる軸）が先頭次元
        # であることを前提とする（`grounding/constraints.py` の実装：
        # `zone_probs.sum(dim=0)`）。ここでは K（スロット＝個体）を先頭に転置してから
        # 渡すことで、候補・ホライズンをまとめて1回のベクトル化演算で処理できる
        # （EXP-06 のように候補ごとに Python ループを回す必要が無い）。
        capacities = self.zone_capacities.to(zone_probs.device)
        zone_probs_k_first = zone_probs.permute(2, 0, 1, 3)  # [K,N,h,n_zones]
        capacity_violation_nhz = zone_capacity_violation(  # [N,h,n_zones]
            zone_probs_k_first, capacities
        )
        capacity_worst = capacity_violation_nhz.amax(dim=(1, 2))  # [N]

        return single_zone_worst, capacity_worst

    def __call__(self, candidate_actions: Tensor) -> Tensor:
        """`wm.planner.ShieldFn` 契約：candidate_actions -> mask[N]（True=許容）。"""
        single_zone_worst, capacity_worst = self.symbolize_and_check(candidate_actions)
        return (single_zone_worst < self.single_zone_threshold) & (
            capacity_worst < self.capacity_violation_threshold
        )


__all__ = ["RestrictedZoneShield", "ComplianceShield"]
