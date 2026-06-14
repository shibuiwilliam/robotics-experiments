"""S4 採点: アンカリング精度・調停正答率・見逃し・作業指示の正否（決定的）。"""

from __future__ import annotations

from orx.common.schemas import StrictModel
from orx.exp.suites.s4_inspection.model import S4Episode, S4World
from orx.exp.suites.s4_inspection.reference import anchor, reconcile
from orx.oracle.scenarios.s4 import is_missed_anomaly, required_action


class S4ConditionScore(StrictModel):
    anchor_accuracy: float  # 知覚個体を真の資産へ対応付けた割合
    anomaly_accuracy: float  # 最終異常状態が真値と一致した資産割合
    missed_anomalies: int  # 真の異常を見逃した数（安全上重大）
    workorder_correct: float  # 必要作業指示を正しく起票（該当系統SOP一致）した割合


def score_condition(world: S4World, episode: S4Episode, condition: str) -> S4ConditionScore:
    truth = {a.asset_id: a for a in world.assets}
    # 1) アンカリング
    anchored_correct = 0
    grouped: dict[str, list[tuple[bool, float]]] = {a.asset_id: [] for a in world.assets}
    for obs in episode.observations:
        a_id = anchor(condition, obs, episode.ledger)
        if a_id == obs.true_asset:
            anchored_correct += 1
        if a_id is not None:
            grouped.setdefault(a_id, []).append((obs.anomaly_reading, obs.confidence))
    anchor_acc = anchored_correct / len(episode.observations)

    # 2) 調停 → 3) 作業指示連鎖（異常→系統→SOP→WO）
    n = len(world.assets)
    correct_status = 0
    missed = 0
    wo_correct = 0
    for asset in world.assets:
        concluded = reconcile(condition, grouped.get(asset.asset_id, []))
        if concluded == asset.true_anomaly:
            correct_status += 1
        if is_missed_anomaly(asset.true_anomaly, concluded):
            missed += 1
        issued = world.sop.get(asset.system) if concluded else None
        if issued == required_action(asset.true_anomaly, asset.system, world.sop):
            wo_correct += 1
        _ = truth  # 真値は asset から直接
    return S4ConditionScore(
        anchor_accuracy=round(anchor_acc, 6),
        anomaly_accuracy=round(correct_status / n, 6),
        missed_anomalies=missed,
        workorder_correct=round(wo_correct / n, 6),
    )
