"""S6 条件別リファレンスソルバ（ADR-014/017: 双対表現の分離）。

- OR-full: ベクトル接地（X4）＋規制オントロジー（class→lane）＋確信度閾値で人間委譲。
- OR-vec : ベクトル接地のみ。規制推論が無く全件 general へ（規制レーンを誤る）。
- OR-sym : 記号IDが無く接地不能 → 全件 人間委譲（スループット崩壊）。
"""

from __future__ import annotations

from orx.exp.suites.s6_recycling.grounding import ground
from orx.exp.suites.s6_recycling.model import S6Object, S6World
from orx.oracle.scenarios.s6 import ESCALATE

CONDITIONS = ["OR-full", "OR-vec", "OR-sym"]


def assign_lane(
    condition: str,
    obj: S6Object,
    world: S6World,
    prototypes: dict[str, list[float]],
    confidence_threshold: float,
) -> tuple[str, float]:
    """物体を1レーンへ割り当てる。返り値 (lane_or_ESCALATE, confidence)。"""
    if condition == "OR-sym":
        return ESCALATE, 0.0  # 記号ID無し → 接地不能 → 委譲
    cls, conf = ground(obj.embedding, prototypes)
    if condition == "OR-vec":
        return world.default_lane, conf  # 規制推論なし → 既定レーン（規制レーンを誤る）
    if condition == "OR-full":
        if conf < confidence_threshold:
            return ESCALATE, conf  # 低確信は委譲（誤レーンより安全）
        return world.disposal_route[cls], conf
    raise ValueError(f"未知の条件 {condition!r}")
