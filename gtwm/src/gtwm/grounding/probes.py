"""意味プローブ α：Probe(slots) -> list[Belief]（world_model.md の契約）。

ヘッド：存在（スロットが実在物体か）、型（オントロジークラス。SlotModule の type_logits を
再利用する）、位置（ゾーン分類＋床面座標回帰）、状態（ディスポジション分類）、
関係（集約：ケース→パレットの所属、ペアワイズ）。

確信度は温度スケーリングで較正する（`calibrate_temperature` / `expected_calibration_error`）。
較正済み確信度は `eval/metrics.py` の ECE 定義と一致するように ECE ≤ 0.05 を目標にする
（session 06 で目標値そのものは criteria.yaml に転記する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import torch
from torch import Tensor, nn

from gtwm.kg.schema import Belief
from gtwm.sim.env import ZONE_NAMES
from gtwm.wm.slots import SLOT_TYPES

N_ZONES = len(ZONE_NAMES)
N_TYPES = len(SLOT_TYPES)
N_STATES = 2
STATE_NAMES = ["active", "idle"]  # CBV disposition の簡易2値化（urn:epcglobal:cbv:disp:*）
DISPOSITION_URI = {
    "active": "urn:epcglobal:cbv:disp:active",
    "idle": "urn:epcglobal:cbv:disp:idle",
}


class Probe(nn.Module):
    """契約：Probe(slots:[B,T,K,D]) -> list[Belief]。

    型ヘッドは `SlotModule` が既に計算する `type_logits` を再利用する契約とし、
    ここでは existence / position(zone分類+床面座標回帰) / state / relations(集約) の
    4ヘッドのみを追加する。
    """

    def __init__(self, slot_dim: int, n_zones: int = N_ZONES, n_states: int = N_STATES):
        super().__init__()
        self.existence_head = nn.Linear(slot_dim, 1)
        self.zone_head = nn.Linear(slot_dim, n_zones)
        self.floor_head = nn.Linear(slot_dim, 2)
        self.state_head = nn.Linear(slot_dim, n_states)
        # 関係（集約）ヘッド：ケーススロットとパレットスロットのペアが「積載関係」にある
        # ロジットを双線形形式で出す（poc_plan.md 5.4「関係：集約...ペアワイズ分類」）。
        self.relation_head = nn.Bilinear(slot_dim, slot_dim, 1)
        # 温度スケーリング較正（ゾーン分類の確信度に対して適用する）。
        self.log_temperature = nn.Parameter(torch.zeros(()))

    def forward(self, slots: Tensor) -> dict[str, Tensor]:
        """slots:[...,D] -> 各ヘッドの生ロジット/回帰値の辞書。"""
        return {
            "existence_logit": self.existence_head(slots).squeeze(-1),
            "zone_logit": self.zone_head(slots),
            "floor_xy": self.floor_head(slots),
            "state_logit": self.state_head(slots),
        }

    def zone_probs(self, slots: Tensor, calibrated: bool = True) -> Tensor:
        logit = self.zone_head(slots)
        if calibrated:
            logit = logit / self.log_temperature.exp().clamp_min(1e-3)
        return logit.softmax(dim=-1)

    def relation_logit(self, case_slot: Tensor, pallet_slot: Tensor) -> Tensor:
        return self.relation_head(case_slot, pallet_slot).squeeze(-1)


@dataclass
class SlotFacts:
    """1スロットぶんの、Belief化する前の生の推定結果。

    identity.py が個体対応を確定した後に使う。
    """

    entity_gt_id: str
    existence_prob: float
    zone_idx: int
    zone_confidence: float
    floor_xy: tuple[float, float]
    type_idx: int
    type_confidence: float
    state_idx: int
    state_confidence: float


def slot_facts_to_beliefs(
    facts: SlotFacts,
    t: datetime,
    source: str = "wm",
    existence_threshold: float = 0.5,
) -> list[Belief]:
    """`SlotFacts` を `gt:Belief` の集合に変換する（存在確信度が閾値未満なら空リスト）。

    - 型：`rdf:type` 述語で `gt:<ClassName>` を目的語にする。
    - 位置：`gt:currentZone` 述語で `gt:Zone_<name>` を目的語にする。
    - 状態：`gt:disposition` 述語で CBV disposition URI を目的語にする。
    """
    if facts.existence_prob < existence_threshold:
        return []
    beliefs = []
    zone_name = ZONE_NAMES[facts.zone_idx]
    type_name = SLOT_TYPES[facts.type_idx]
    state_name = STATE_NAMES[facts.state_idx]
    beliefs.append(
        Belief(
            subject=facts.entity_gt_id,
            predicate="rdf:type",
            object=f"gt:{type_name.capitalize()}",
            confidence=facts.type_confidence,
            source=source,  # type: ignore[arg-type]
            valid_from=t,
            transaction_time=t,
        )
    )
    beliefs.append(
        Belief(
            subject=facts.entity_gt_id,
            predicate="gt:currentZone",
            object=f"gt:Zone_{zone_name}",
            confidence=facts.zone_confidence,
            source=source,  # type: ignore[arg-type]
            valid_from=t,
            transaction_time=t,
        )
    )
    beliefs.append(
        Belief(
            subject=facts.entity_gt_id,
            predicate="gt:disposition",
            object=DISPOSITION_URI[state_name],
            confidence=facts.state_confidence,
            source=source,  # type: ignore[arg-type]
            valid_from=t,
            transaction_time=t,
        )
    )
    return beliefs


def utc(seconds_from_epoch: float) -> datetime:
    """エピソード内時刻（秒）を、ledger/KGStore が要求する `datetime` に変換する。

    実時刻には意味がない（シミュレーション時間）ため、epoch 起点の相対時刻として扱う。
    """
    return datetime.fromtimestamp(seconds_from_epoch, tz=UTC)


def calibrate_temperature(
    logits: Tensor, labels: Tensor, t_min: float = 0.1, t_max: float = 5.0, steps: int = 50
) -> float:
    """グリッドサーチで温度 T を求める（NLL 最小化）。

    探索範囲は `configs/grounding/epsilon.yaml` の値を使う。
    """
    best_t = 1.0
    best_nll = float("inf")
    for i in range(steps):
        t = t_min + (t_max - t_min) * i / max(steps - 1, 1)
        scaled = logits / t
        nll = torch.nn.functional.cross_entropy(scaled, labels).item()
        if nll < best_nll:
            best_nll = nll
            best_t = t
    return best_t


def expected_calibration_error(probs: Tensor, labels: Tensor, n_bins: int = 10) -> float:
    """ECE（付録A）：確信度を10区間に分け、各区間の平均確信度と正答率の差の加重平均。"""
    confidences, predictions = probs.max(dim=-1)
    correct = (predictions == labels).float()
    ece = torch.zeros(())
    n = confidences.shape[0]
    bin_edges = torch.linspace(0.0, 1.0, n_bins + 1)
    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mask = (
            (confidences > lo) & (confidences <= hi)
            if i > 0
            else (confidences >= lo) & (confidences <= hi)
        )
        if mask.sum() == 0:
            continue
        acc_in_bin = correct[mask].mean()
        conf_in_bin = confidences[mask].mean()
        ece = ece + (mask.float().sum() / n) * (acc_in_bin - conf_in_bin).abs()
    return float(ece.item())


__all__ = [
    "N_ZONES",
    "N_TYPES",
    "N_STATES",
    "STATE_NAMES",
    "DISPOSITION_URI",
    "Probe",
    "SlotFacts",
    "slot_facts_to_beliefs",
    "utc",
    "calibrate_temperature",
    "expected_calibration_error",
]
