"""S2 エピソード生成（シード決定的）。

越境（cross-robot）の汚染連鎖を必ず1本含める:
  arm: g_a–source(peanut) → arm: g_a–tray → cobot: g_b–tray
これにより g_b は「armが観測した tray 汚染」経由で汚染されるが、共通オントロジー無しの
B1（ロボット別・横断融合なし）はこの連鎖を組み立てられない。洗浄イベントも1本入れ、
洗浄後の許可把持を OR−belief（リセット無視）が誤って拒否することを誘発する。
"""

from __future__ import annotations

from orx.common.schemas import ContactEvent, ResetEvent
from orx.common.seeding import SeedTree
from orx.exp.suites.s2_allergen.model import S2Episode, S2Query, S2World
from orx.oracle.scenarios.s2 import contamination_closure, grasp_allowed


def generate_episode(world: S2World, seed: int) -> S2Episode:
    rng = SeedTree(seed).child("s2-gen").rng()
    sources = [p for p in world.products if p.intrinsic]
    if not sources or len(world.grippers) < 2 or len(world.robots) < 2 or not world.trays:
        raise ValueError("S2世界は 源製品1+・グリッパ2+・ロボット2+・トレイ1+ が必要")
    if len(world.allergens) < 2:
        raise ValueError("S2は対照クエリ用に allergen 2種以上が必要")

    source = sources[int(rng.integers(0, len(sources)))]
    allergen = source.intrinsic[0]
    other_allergen = next(a for a in world.allergens if a != allergen)
    g_a, g_b = world.grippers[0], world.grippers[1]
    tray = world.trays[0]
    arm, cobot = world.robots[0], world.robots[1]
    intrinsic = {source.name: list(source.intrinsic)}

    t1 = round(float(rng.uniform(1.0, 2.0)), 3)
    t2 = round(t1 + float(rng.uniform(0.8, 1.5)), 3)
    t3 = round(t2 + float(rng.uniform(0.8, 1.5)), 3)
    t_clean = round(t3 + float(rng.uniform(0.8, 1.5)), 3)

    contacts = [
        ContactEvent(a=g_a, b=source.name, sim_time=t1, observer=arm),
        ContactEvent(a=g_a, b=tray, sim_time=t2, observer=arm),
        ContactEvent(a=g_b, b=tray, sim_time=t3, observer=cobot),
    ]
    # 清浄なディストラクタ接触（別の清浄品同士）
    clean_products = [p.name for p in world.products if not p.intrinsic]
    if len(clean_products) >= 2:
        contacts.append(
            ContactEvent(
                a=clean_products[0],
                b=clean_products[1],
                sim_time=round(t1 + 0.3, 3),
                observer=cobot,
            )
        )
    cleanings = [ResetEvent(entity=g_a, kind="cleaning", sim_time=t_clean)]
    eval_time = round(t_clean + 1.0, 3)

    def truth(gripper: str, free_of: list[str], t: float) -> bool:
        state = contamination_closure(intrinsic, contacts, cleanings, t)
        return grasp_allowed(state, gripper, set(free_of))

    t_cross = round(t3 + 0.3, 3)
    t_direct = round(t2 + 0.3, 3)  # g_a は source 経由で汚染、最新接触は g_a–tray
    t_post = round(t_clean + 0.3, 3)
    queries = [
        S2Query(
            qid=f"s2-{seed}-cross",
            kind="cross_robot",
            gripper=g_b,
            free_of=[allergen],
            sim_time=t_cross,
            truth_allowed=truth(g_b, [allergen], t_cross),
        ),
        S2Query(
            qid=f"s2-{seed}-direct",
            kind="direct",
            gripper=g_a,
            free_of=[allergen],
            sim_time=t_direct,
            truth_allowed=truth(g_a, [allergen], t_direct),
        ),
        S2Query(
            qid=f"s2-{seed}-postclean",
            kind="post_clean",
            gripper=g_a,
            free_of=[allergen],
            sim_time=t_post,
            truth_allowed=truth(g_a, [allergen], t_post),
        ),
        S2Query(
            qid=f"s2-{seed}-control",
            kind="control",
            gripper=g_b,
            free_of=[other_allergen],
            sim_time=t_cross,
            truth_allowed=truth(g_b, [other_allergen], t_cross),
        ),
    ]
    return S2Episode(
        intrinsic=intrinsic,
        contacts=contacts,
        cleanings=cleanings,
        queries=queries,
        eval_time=eval_time,
    )
