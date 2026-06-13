"""S2 条件別リファレンスソルバ（ADR-014: 情報層での反証）。

各条件が許す情報のみで「把持可否」と「汚染集合」を解く:
- OR-full : 全接触（共通オントロジーで横断統合）＋洗浄リセット＋推移閉包。
- OR-belief: 全接触だが**洗浄リセットを無視**（来歴・時刻オフ）→ 洗浄後も汚染扱い（過保守）。
- B1      : ロボット別に独立閉包し和を取る（**横断融合なし**）→ 越境連鎖を組めない。
- B0      : 直近1接触のスナップショットのみ（履歴なし）→ ほぼ全て取りこぼす。
"""

from __future__ import annotations

from orx.exp.suites.s2_allergen.model import S2Episode, S2Query
from orx.oracle.scenarios.s2 import ContaminationState, contamination_closure, grasp_allowed

CONDITIONS = ["OR-full", "OR-belief", "B1", "B0"]


def _before(contacts: list, t: float) -> list:
    return [c for c in contacts if c.sim_time <= t + 1e-9]


def carried_state(
    condition: str, episode: S2Episode, distilled: list, observers: list[str], at_time: float
) -> ContaminationState:
    intrinsic = episode.intrinsic
    cleanings = episode.cleanings
    seen = _before(distilled, at_time)
    if condition == "OR-full":
        return contamination_closure(intrinsic, seen, cleanings, at_time)
    if condition == "OR-belief":
        return contamination_closure(intrinsic, seen, [], at_time)  # 洗浄リセット無視
    if condition == "B1":
        merged: dict[str, set[str]] = {}
        for r in observers:
            per = contamination_closure(
                intrinsic, [c for c in seen if c.observer == r], cleanings, at_time
            )
            for entity, alls in per.carried.items():
                merged.setdefault(entity, set()).update(alls)
        return ContaminationState(carried={e: sorted(v) for e, v in merged.items()})
    if condition == "B0":
        snapshot = seen[-1:]  # seen は時刻昇順 → 直近1件
        return contamination_closure(intrinsic, snapshot, [], at_time)
    raise ValueError(f"未知の条件 {condition!r}")


def grasp_allowed_under(
    condition: str, episode: S2Episode, distilled: list, observers: list[str], query: S2Query
) -> bool:
    state = carried_state(condition, episode, distilled, observers, query.sim_time)
    return grasp_allowed(state, query.gripper, set(query.free_of))


def contamination_pairs(
    condition: str, episode: S2Episode, distilled: list, observers: list[str], at_time: float
) -> set[tuple[str, str]]:
    """(entity, allergen) の汚染集合（F1 採点用）。"""
    state = carried_state(condition, episode, distilled, observers, at_time)
    return {(e, a) for e, alls in state.carried.items() for a in alls}
