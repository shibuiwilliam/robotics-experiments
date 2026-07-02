"""S1 エピソード生成: 回収ロットの選択と回収時刻の決定（シード決定的）。"""

from __future__ import annotations

from orx.common.config import WorldConfig
from orx.common.seeding import SeedTree


def moved_boxes(world: WorldConfig) -> set[str]:
    """搬送スクリプトで移動する箱名（搬送中＝バーコード不可読になりうる個体）。"""
    return {m.box for m in world.scripted_moves}


def candidate_recall_lots(world: WorldConfig) -> list[str]:
    """搬送中メンバーを少なくとも1つ持つロット（反証が成立する＝失敗予言が効く）。"""
    moved = moved_boxes(world)
    lots: set[str] = set()
    for box in world.boxes:
        if box.name in moved and box.barcode and box.lot:
            lots.add(box.lot)
    return sorted(lots)


def choose_recall_lot(world: WorldConfig, seed: int) -> str:
    """シードで回収ロットを選ぶ（搬送中メンバーを持つロットから）。"""
    candidates = candidate_recall_lots(world)
    if not candidates:
        raise ValueError("搬送中メンバーを持つロットがありません（S1の反証が成立しない世界）")
    rng = SeedTree(seed).child("s1-recall").rng()
    return candidates[int(rng.integers(0, len(candidates)))]


def recall_time(world: WorldConfig, margin_s: float = 3.0) -> float:
    """回収時刻 = 最後の搬送完了＋margin（搬送済み状態で逆引きさせる）。"""
    if not world.scripted_moves:
        return world.settle_s + 1.0
    last_end = max(m.at_time + m.duration_s for m in world.scripted_moves)
    return round(last_end + margin_s, 3)
