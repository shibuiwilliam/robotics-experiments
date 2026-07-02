"""S6 エージェント条件（agent 射程・live, R-3a）。

**射程の注記**: S6 の H4（記号＋ベクトルの双対表現）は知覚・接地の機構レベルの主張で、
決定的アブレーション（OR-vec/OR-sym）が既に示している。本 agent 版はそれとは別に
「**規制オントロジー（disposal_route 写像）が LLM エージェントの正レーン選定を助けるか**」を
測る（H3/H4-隣接の agent 検証）。接地（embedding→class）は知覚層 ground() が決定的に行い、
**エージェントは接地済みクラス＋確信度から推論する**（真値クラスは渡さない＝漏洩なし）:
- OR-full-llm: 接地クラス＋確信度＋規制写像(disposal_route)＋委譲閾値 → 正レーン/ESCALATE。
- B0-llm:      接地クラス＋レーン名のみ（規制写像なし）→ 自力で推測（規制レーンを誤る）。

全物体を1コールでまとめて経路化（`obj_id=lane` 形式）し、既存 scorer で採点する。
"""

from __future__ import annotations

import json

from orx.agent.toolloop import AgentRunResult
from orx.common.providers import LLMClient
from orx.exp.agent_tools import ANSWER_RULES, build_agent
from orx.exp.suites.s6_recycling.grounding import ground
from orx.oracle.scenarios.s6 import ESCALATE

AGENT_CONDITIONS = ["OR-full-llm", "B0-llm"]


def _answer_rules(lanes: list[str]) -> str:
    return (
        f"回答形式: 各物体を `obj_id=レーン名` で表す（レーン ∈ {lanes} または {ESCALATE}）。"
        "カンマ区切り・obj_id 辞書順で1行に。\n" + ANSWER_RULES
    )


def parse_lane_answer(answer: str, valid: set[str]) -> dict[str, str]:
    """`obj_id=lane,...` を {obj_id: lane} に。未知レーンは ESCALATE に丸める。"""
    out: dict[str, str] = {}
    for part in (answer or "").strip().split(","):
        if "=" not in part:
            continue
        oid, _, lane = part.partition("=")
        oid = oid.strip()
        lane = lane.strip()
        if not oid:
            continue
        out[oid] = lane if (lane in valid or lane == ESCALATE) else ESCALATE
    return out


def _or_prompt(world: object, grounded: list[dict]) -> str:
    route = json.dumps(world.disposal_route, ensure_ascii=False)
    return (
        "あなたはリサイクル選別の経路化エージェント。各物体は ID 無しで、知覚が視覚接地した"
        "推定クラスと確信度（top1−top2 余裕）が与えられる。\n"
        f"規制写像（class→正しいレーン, 非自明）: {route}\n"
        f"レーン: {world.lanes}。確信度が低い物体は誤レーンより {ESCALATE}（人間委譲）が安全。\n"
        f"物体（接地クラス・確信度）: {json.dumps(grounded, ensure_ascii=False)}\n"
        "各物体を規制写像で正しいレーンへ。確信度が低ければ ESCALATE。\n"
        + _answer_rules(world.lanes)
    )


def _b0_prompt(world: object, grounded: list[dict]) -> str:
    return (
        "あなたはリサイクル選別の経路化エージェント。各物体の知覚推定クラスと確信度が与えられる。\n"
        f"レーン: {world.lanes}（既定 {world.default_lane}）。各クラスがどのレーンに行くべきかの"
        "規制表は与えられない。\n"
        f"物体（接地クラス・確信度）: {json.dumps(grounded, ensure_ascii=False)}\n"
        "各物体を適切と思うレーンへ割り当てよ。\n" + _answer_rules(world.lanes)
    )


def route_episode_llm(
    condition: str,
    objects: list,
    world: object,
    prototypes: dict[str, list[float]],
    llm: LLMClient,
) -> tuple[list[tuple], AgentRunResult]:
    """1 エピソードの全物体をまとめて経路化し [(obj, lane, conf)] と実行統計を返す。"""
    grounded_class: dict[str, str] = {}
    conf_by: dict[str, float] = {}
    table: list[dict] = []
    for obj in objects:
        cls, conf = ground(obj.embedding, prototypes)
        grounded_class[obj.obj_id] = cls
        conf_by[obj.obj_id] = conf
        table.append({"obj_id": obj.obj_id, "class": cls, "confidence": round(conf, 3)})
    prompt = _or_prompt(world, table) if condition == "OR-full-llm" else _b0_prompt(world, table)
    agent = build_agent(llm, prompt, [])
    rr = agent.run("全物体を適切なレーンへ割り当ててください。")
    routed = parse_lane_answer(rr.answer, set(world.lanes))
    assignments = [(obj, routed.get(obj.obj_id, ESCALATE), conf_by[obj.obj_id]) for obj in objects]
    return assignments, rr
