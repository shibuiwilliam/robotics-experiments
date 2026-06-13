"""S2 アレルゲン交差汚染の型（T9）。"""

from __future__ import annotations

from orx.common.schemas import ContactEvent, ResetEvent, StrictModel


class S2Product(StrictModel):
    name: str
    intrinsic: list[str] = []  # 源アレルゲン（peanut 等）。空＝清浄品


class S2World(StrictModel):
    """S2 世界カタログ（configs/world/s2_allergen.yaml）。スケジュールは生成器が作る。"""

    allergens: list[str]  # 例 [peanut, gluten]
    grippers: list[str]  # 例 [g_a, g_b]
    trays: list[str]
    products: list[S2Product]
    robots: list[str]  # 観測ロボット 例 [arm, cobot]


class S2Query(StrictModel):
    """把持可否クエリ。truth_allowed は oracle が埋める（採点の正解）。"""

    qid: str
    kind: str  # cross_robot | post_clean | direct | control
    gripper: str
    free_of: list[str]  # この製品が「フリーであるべき」アレルゲン
    sim_time: float
    truth_allowed: bool


class S2Episode(StrictModel):
    """1シードの生成エピソード。"""

    intrinsic: dict[str, list[str]]  # entity(canonical) -> 源アレルゲン
    contacts: list[ContactEvent]  # canonical・観測者付き（シム接触の真値列）
    cleanings: list[ResetEvent]
    queries: list[S2Query]
    eval_time: float  # 汚染集合 F1 を採る基準時刻
