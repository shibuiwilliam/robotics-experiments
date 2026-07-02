"""S2 アレルゲン交差汚染の真値導出（T9, ADR-015）。

接触イベント列の前方向（時間順）推移閉包として、各実体が任意時刻に保持しうる
アレルゲン集合を機械導出する。ORコア非依存（common のみ依存）。

セマンティクス（D3 / ADR-015）:
- intrinsic[e]: 不変な源アレルゲン（peanut製品など。洗っても消えない）。
- acquired[e]: 接触で得たアレルゲン（洗浄でリセット）。
- carried(e) = intrinsic[e] | acquired[e]。
- ContactEvent(a,b,t): 対称ユニオン（両者が carried(a)|carried(b) を保持）。
- CleaningEvent(e,t): acquired[e] を空に。
"""

from __future__ import annotations

from orx.common.schemas import ContactEvent, ResetEvent, StrictModel


class ContaminationState(StrictModel):
    """任意時刻の汚染状態: entity -> 保持しうるアレルゲン集合（ソート済みリスト）。"""

    carried: dict[str, list[str]]

    def carries(self, entity: str, allergen: str) -> bool:
        return allergen in self.carried.get(entity, [])


def contamination_closure(
    intrinsic: dict[str, set[str]],
    contacts: list[ContactEvent],
    cleanings: list[ResetEvent],
    at_time: float,
) -> ContaminationState:
    """時刻 at_time における汚染推移閉包（イベントを時間順に再生）。

    同時刻は ContactEvent → CleaningEvent の順で適用する（洗浄が最後に効く＝
    「触れてから洗った」が同tickなら清浄、を表す）。
    """
    # intrinsic の値は set/list いずれも許容（堅牢化）
    intr: dict[str, set[str]] = {e: set(v) for e, v in intrinsic.items()}
    acquired: dict[str, set[str]] = {e: set() for e in intr}

    def carried(e: str) -> set[str]:
        return intr.get(e, set()) | acquired.get(e, set())

    timeline: list[tuple[float, int, str, tuple]] = []
    for c in contacts:
        if c.sim_time <= at_time + 1e-9:
            timeline.append((c.sim_time, 0, "contact", (c.a, c.b)))
    for cl in cleanings:
        if cl.sim_time <= at_time + 1e-9:
            timeline.append((cl.sim_time, 1, "clean", (cl.entity,)))
    timeline.sort(key=lambda x: (x[0], x[1]))

    for _t, _ord, kind, args in timeline:
        if kind == "contact":
            a, b = args
            acquired.setdefault(a, set())
            acquired.setdefault(b, set())
            tab = carried(a) | carried(b)
            acquired[a] |= tab - intr.get(a, set())
            acquired[b] |= tab - intr.get(b, set())
        else:
            (e,) = args
            acquired[e] = set()

    entities = set(intr) | set(acquired)
    return ContaminationState(carried={e: sorted(carried(e)) for e in entities if carried(e)})


def grasp_allowed(state: ContaminationState, gripper: str, allergen_free_of: set[str]) -> bool:
    """把持可否: グリッパが「フリーであるべきアレルゲン」を保持していなければ許可。"""
    carried = set(state.carried.get(gripper, []))
    return not (carried & allergen_free_of)
