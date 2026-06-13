"""S1 ロット回収の真値導出（T8）。

シム真値（TruthState: 各物体の barcode・zone）と業務真値（lot_members: barcode→lot）
の結合で、回収対象集合・現在地・位置履歴を機械導出する。ORコア非依存。
"""

from __future__ import annotations

from orx.common.schemas import StrictModel, TruthState


class RecallTruth(StrictModel):
    """回収対象の業務真値。キーはバーコード（個装ID）。"""

    recall_lot: str
    targets: dict[str, str]  # barcode -> 回収時点の真のゾーン名
    history: dict[str, list[tuple[float, str]]]  # barcode -> [(sim_time, zone)] 軌跡


def _targets_at(state: TruthState, lot_members: dict[str, str], recall_lot: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for obj in state.objects:
        if obj.barcode is None:
            continue
        if lot_members.get(obj.barcode) == recall_lot and obj.zone is not None:
            out[obj.barcode] = obj.zone
    return out


def recall_truth(
    truth_states: list[TruthState],
    lot_members: dict[str, str],
    recall_lot: str,
    recall_time: float,
) -> RecallTruth:
    """回収時点の対象集合＋現在地、および全期間の位置履歴（監査用）。"""
    if not truth_states:
        raise ValueError("truth_states が空です")
    at_recall = min(truth_states, key=lambda s: abs(s.sim_time - recall_time))
    targets = _targets_at(at_recall, lot_members, recall_lot)

    history: dict[str, list[tuple[float, str]]] = {bc: [] for bc in targets}
    for state in sorted(truth_states, key=lambda s: s.sim_time):
        for obj in state.objects:
            if obj.barcode in history and obj.zone is not None:
                traj = history[obj.barcode]
                if not traj or traj[-1][1] != obj.zone:  # 連続重複は畳む
                    traj.append((round(state.sim_time, 3), obj.zone))
    return RecallTruth(recall_lot=recall_lot, targets=targets, history=history)
