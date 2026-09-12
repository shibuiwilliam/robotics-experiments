"""同一性解決（C7）：トラック（スロットの時間的連続）とオントロジー個体の対応付け。

費用 = WM 予測位置の距離 + 外観埋め込みの距離。ハンガリアン割当（scipy）で解く。
遮蔽中は WM の予測位置で個体を保持し、アンカーで強制的に再同定する
（poc_plan.md 5.4「同一性解決」）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass
class TrackedEntity:
    """1個体ぶんの追跡状態。"""

    entity_gt_id: str
    predicted_xy: np.ndarray  # (2,) WM 予測（遮蔽中はこれを保持し続ける）
    appearance: np.ndarray  # (Da,) 外観埋め込み（最後に観測できた時点のもの）
    last_seen_step: int
    occluded: bool = False


@dataclass
class AssignmentResult:
    """1フレームぶんのハンガリアン割当結果。"""

    slot_to_entity: dict[int, str] = field(default_factory=dict)  # スロット idx -> entity_gt_id
    unmatched_slots: list[int] = field(default_factory=list)
    unmatched_entities: list[str] = field(default_factory=list)


def assignment_cost(
    slot_positions: np.ndarray,  # [K,2]
    slot_appearance: np.ndarray,  # [K,Da]
    entities: list[TrackedEntity],
    position_weight: float = 1.0,
    appearance_weight: float = 1.0,
    max_cost: float = 5.0,
) -> np.ndarray:
    """コスト行列 [K, N] = 位置距離*position_weight + 外観距離*appearance_weight。

    `max_cost` を超えるペアはこの値でクリップする（scipy の linear_sum_assignment は
    無限大や NaN を扱えないため、遠すぎる対応を「割当候補から事実上除外」する目的）。
    """
    k = slot_positions.shape[0]
    n = len(entities)
    cost = np.zeros((k, n))
    for j, ent in enumerate(entities):
        pos_dist = np.linalg.norm(slot_positions - ent.predicted_xy[None, :], axis=1)
        app_dist = np.linalg.norm(slot_appearance - ent.appearance[None, :], axis=1)
        cost[:, j] = position_weight * pos_dist + appearance_weight * app_dist
    return np.clip(cost, 0.0, max_cost)


def resolve_identities(
    slot_positions: np.ndarray,
    slot_appearance: np.ndarray,
    entities: list[TrackedEntity],
    distance_gate: float = 2.0,
    position_weight: float = 1.0,
    appearance_weight: float = 1.0,
) -> AssignmentResult:
    """1フレームぶんのスロット↔個体対応をハンガリアン割当で求める。

    `distance_gate`（メートル相当）を超える対応は採用しない
    （遠すぎる対応をそのまま受理すると誤同定を招くため。多物体追跡評価の
    ゲーティングと同じ考え方）。
    """
    if len(entities) == 0 or slot_positions.shape[0] == 0:
        return AssignmentResult(
            unmatched_slots=list(range(slot_positions.shape[0])),
            unmatched_entities=[e.entity_gt_id for e in entities],
        )
    cost = assignment_cost(
        slot_positions, slot_appearance, entities, position_weight, appearance_weight
    )
    row_ind, col_ind = linear_sum_assignment(cost)

    result = AssignmentResult()
    matched_slots = set()
    matched_entities = set()
    for r, c in zip(row_ind, col_ind, strict=True):
        pos_dist = float(np.linalg.norm(slot_positions[r] - entities[c].predicted_xy))
        if pos_dist > distance_gate:
            continue
        result.slot_to_entity[int(r)] = entities[c].entity_gt_id
        matched_slots.add(int(r))
        matched_entities.add(c)

    result.unmatched_slots = [i for i in range(slot_positions.shape[0]) if i not in matched_slots]
    result.unmatched_entities = [
        e.entity_gt_id for i, e in enumerate(entities) if i not in matched_entities
    ]
    return result


def update_occluded_entities(
    entities: list[TrackedEntity],
    result: AssignmentResult,
    predicted_next_xy: dict[str, np.ndarray],
    step: int,
) -> list[TrackedEntity]:
    """未マッチの個体は「遮蔽中」として WM 予測位置で保持する（アンカーが来るまで確定しない）。"""
    matched_ids = set(result.slot_to_entity.values())
    updated: list[TrackedEntity] = []
    for ent in entities:
        if ent.entity_gt_id in matched_ids:
            updated.append(
                TrackedEntity(
                    entity_gt_id=ent.entity_gt_id,
                    predicted_xy=ent.predicted_xy,
                    appearance=ent.appearance,
                    last_seen_step=step,
                    occluded=False,
                )
            )
        else:
            next_xy = predicted_next_xy.get(ent.entity_gt_id, ent.predicted_xy)
            updated.append(
                TrackedEntity(
                    entity_gt_id=ent.entity_gt_id,
                    predicted_xy=next_xy,
                    appearance=ent.appearance,
                    last_seen_step=ent.last_seen_step,
                    occluded=True,
                )
            )
    return updated


def force_reidentify(
    entities: list[TrackedEntity], anchor_entity_gt_id: str, xy: np.ndarray
) -> None:
    """アンカー検出（RFID/バーコード等）による強制再同定：該当個体の位置を真値で上書きする。"""
    for ent in entities:
        if ent.entity_gt_id == anchor_entity_gt_id:
            ent.predicted_xy = xy
            ent.occluded = False
            return
    raise KeyError(f"未追跡の個体に対する強制再同定: {anchor_entity_gt_id}")


__all__ = [
    "TrackedEntity",
    "AssignmentResult",
    "assignment_cost",
    "resolve_identities",
    "update_occluded_entities",
    "force_reidentify",
]
