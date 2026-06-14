"""S3 能力台帳（決定的・T6 較正）。

宣言（能力契約）をベータ事前、実行エピソードを観測としてベイズ的に成功率を推定する。
T3 の `CapabilityLedger` を世界グラフ非依存の軽量版に簡約（決定的・オフライン）。
"""

from __future__ import annotations

from orx.exp.suites.s3_multi_vendor.model import MachineSpec


class CapabilityLedger:
    PRIOR_WEIGHT = 2.0

    def __init__(self) -> None:
        self._declared: dict[str, MachineSpec] = {}
        self._stats: dict[tuple[str, str], tuple[int, int]] = {}  # (machine,material)->(n,s)

    def declare(self, machine: MachineSpec) -> None:
        self._declared[machine.machine_id] = machine

    def record(self, machine_id: str, material: str, success: bool) -> None:
        n, s = self._stats.get((machine_id, material), (0, 0))
        self._stats[(machine_id, material)] = (n + 1, s + (1 if success else 0))

    def estimate(self, machine_id: str, material: str) -> float:
        """宣言事前のベータ事後平均。観測が増えるほど真率へ収束（T6）。"""
        m = self._declared.get(machine_id)
        prior = m.prior_success if m else 0.5
        n, s = self._stats.get((machine_id, material), (0, 0))
        return (s + prior * self.PRIOR_WEIGHT) / (n + self.PRIOR_WEIGHT)

    def feasible(self, machine_id: str, weight_kg: float) -> bool:
        """能力契約の宣言可搬上限による実現可能性判定。"""
        m = self._declared.get(machine_id)
        return m is not None and weight_kg <= m.declared_payload_kg
