"""S6 リサイクル選別の型（T13, H4）。"""

from __future__ import annotations

from orx.common.schemas import StrictModel


class S6World(StrictModel):
    """S6 世界カタログ（configs/world/s6_recycling.yaml）。"""

    classes: list[str]  # 例 [battery, freon, inert]
    lanes: list[str]  # 例 [fire_lane, ozone_lane, general]
    default_lane: str  # 規制推論が無い場合の既定レーン（OR-vec が使う）
    disposal_route: dict[str, str]  # class -> 正レーン（規制規則・非自明）
    cost: dict[str, dict[str, float]]  # cost[true_class][assigned_lane]
    escalation_cost: float = 3.0
    embedding_dim: int = 16
    n_objects: int = 12  # 1エピソードの流入物体数


class S6Object(StrictModel):
    obj_id: str
    true_class: str
    embedding: list[float]


class S6Episode(StrictModel):
    objects: list[S6Object]
