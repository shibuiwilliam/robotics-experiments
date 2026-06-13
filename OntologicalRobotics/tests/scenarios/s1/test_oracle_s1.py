"""S1 oracle 真値導出の単体テスト（手構築フィクスチャ）。ORコア非依存。"""

from orx.common.schemas import TruthObject, TruthState
from orx.oracle.scenarios.s1 import recall_truth

LOT_MEMBERS = {
    "BC-101": "LOT-A",
    "BC-103": "LOT-A",
    "BC-106": "LOT-A",
    "BC-102": "LOT-B",
}


def obj(name: str, bc: str | None, zone: str | None) -> TruthObject:
    return TruthObject(object_id=name, position=(0.0, 0.0, 0.0), barcode=bc, zone=zone)


def test_recall_targets_at_recall_tick() -> None:
    states = [
        TruthState(
            sim_time=10.0,
            objects=[
                obj("b1", "BC-101", "shelf_a"),
                obj("b3", "BC-103", "handoff"),  # 搬送済み
                obj("b6", "BC-106", "shelf_a"),
                obj("b2", "BC-102", "shelf_a"),  # 別ロット
                obj("n1", None, "handoff"),  # ID無し
            ],
        )
    ]
    truth = recall_truth(states, LOT_MEMBERS, "LOT-A", recall_time=10.0)
    assert truth.targets == {"BC-101": "shelf_a", "BC-103": "handoff", "BC-106": "shelf_a"}
    assert "BC-102" not in truth.targets  # 別ロットは対象外


def test_position_history_dedups_consecutive() -> None:
    states = [
        TruthState(sim_time=2.0, objects=[obj("b3", "BC-103", "shelf_a")]),
        TruthState(sim_time=4.0, objects=[obj("b3", "BC-103", "shelf_a")]),  # 重複
        TruthState(sim_time=10.0, objects=[obj("b3", "BC-103", "handoff")]),
    ]
    truth = recall_truth(states, LOT_MEMBERS, "LOT-A", recall_time=10.0)
    assert truth.history["BC-103"] == [(2.0, "shelf_a"), (10.0, "handoff")]


def test_recall_time_picks_nearest_tick() -> None:
    states = [
        TruthState(sim_time=5.0, objects=[obj("b1", "BC-101", "shelf_a")]),
        TruthState(sim_time=14.0, objects=[obj("b1", "BC-101", "quarantine")]),
    ]
    truth = recall_truth(states, LOT_MEMBERS, "LOT-A", recall_time=13.0)
    assert truth.targets["BC-101"] == "quarantine"  # 13に最も近いのは14


def test_empty_states_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="空"):
        recall_truth([], LOT_MEMBERS, "LOT-A", recall_time=1.0)
