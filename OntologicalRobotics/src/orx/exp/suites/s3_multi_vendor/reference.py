"""S3 条件別リファレンスソルバ（ADR-014: 情報境界をシグネチャで強制）。

- OR-full: 共通オントロジーで全ベンダーの能力契約を横断照合。工程要求（重量・素材）×能力で
  実現可能集合を作り、台帳推定が最大の機体を選ぶ。故障で推定が下がれば適格機体へ再割当。
- B1     : 共通オントロジー無し。SOPの工程要求は基準ベンダー語彙でしか表現されず、他ベンダーの
  能力フィールドへ翻訳できない → 候補は基準ベンダーの機体のみ（語彙が揃う範囲）。
- round-robin: 能力を一切見ず候補を巡回（実現可能性を無視）。H3 の帰無仮説。
"""

from __future__ import annotations

from orx.exp.suites.s3_multi_vendor.ledger import CapabilityLedger
from orx.exp.suites.s3_multi_vendor.model import MachineSpec, ProductSpec, S3World

CONDITIONS = ["OR-full", "B1", "round-robin"]


def candidate_machines(
    condition: str, machines: list[MachineSpec], world: S3World
) -> list[MachineSpec]:
    """各条件が「触れられる」機体集合（情報境界）。"""
    if condition == "B1":
        # 共通オントロジー無し → 工程要求を翻訳できる基準ベンダーのみ
        return [m for m in machines if m.vendor == world.reference_vendor]
    return list(machines)  # OR-full / round-robin は全機体（物理的にはラインに居る）


class RoundRobinState:
    def __init__(self) -> None:
        self.i = 0


def allocate(
    condition: str,
    ledger: CapabilityLedger,
    machines: list[MachineSpec],
    product: ProductSpec,
    world: S3World,
    rr: RoundRobinState,
) -> str | None:
    """1工程を1機体へ割り当てる。実現可能な候補が無ければ None。"""
    cands = candidate_machines(condition, machines, world)
    if not cands:
        return None
    if condition == "round-robin":
        chosen = cands[rr.i % len(cands)]  # 能力無視の巡回
        rr.i += 1
        return chosen.machine_id
    # OR-full / B1: 能力契約で実現可能集合に絞り、台帳推定が最大の機体
    feasible = [m for m in cands if ledger.feasible(m.machine_id, product.weight_kg)]
    if not feasible:
        return None
    return max(
        feasible, key=lambda m: (ledger.estimate(m.machine_id, product.material), m.machine_id)
    ).machine_id
