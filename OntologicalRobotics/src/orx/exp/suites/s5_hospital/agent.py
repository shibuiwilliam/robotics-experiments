"""S5 エージェント条件（agent 射程・live, R-B）。

**射程の注記**: S5 の機構（規範チェック／来歴記録）の有無による差は決定的アブレーション
（OR-no-normative/OR-no-prov/B1）が既に担う。本 agent 版はそれとは別に、
「**規範オントロジー（禁止規範＝item_class×forbidden zone_class の写像）を実 LLM に与えると、
禁止区画を通らない適合経路を計画できるか**」を測る（H5-隣接の agent 検証, S6 の規制写像と同型）。

- OR-full-llm: 区画グラフ＋区画分類＋**禁止規範写像** → 違反0の適合経路。
- B1-llm:      区画グラフ＋区画分類のみ（規範写像なし）→ 最短経路で禁止区画を通過（違反）。

全搬送を1コールでまとめて経路化（`tid=z1>z2>...>dst`）し、`count_violations`（真値写像）で採点。
LLM はプロバイダIF経由のみ。真値（違反数）はプロンプトに渡さない。
"""

from __future__ import annotations

import json

from orx.agent.toolloop import AgentRunResult
from orx.common.providers import LLMClient
from orx.exp.agent_tools import ANSWER_RULES, build_agent
from orx.exp.suites.s5_hospital.model import S5World

AGENT_CONDITIONS = ["OR-full-llm", "B1-llm"]


def adjacency(edges: list[list[str]]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def parse_route_answer(answer: str, transport_ids: set[str]) -> dict[str, list[str]]:
    """`tid=z1>z2>...,...` を {tid: [zones]} に。未知 tid は無視。"""
    out: dict[str, list[str]] = {}
    for part in (answer or "").strip().split(","):
        if "=" not in part:
            continue
        tid, _, path = part.partition("=")
        tid = tid.strip()
        if tid not in transport_ids:
            continue
        zones = [z.strip() for z in path.split(">") if z.strip()]
        out[tid] = zones
    return out


def is_valid_path(route: list[str], src: str, dst: str, adj: dict[str, set[str]]) -> bool:
    """経路が src 始点・dst 終点で、各隣接が通路として接続しているか。"""
    if len(route) < 1 or route[0] != src or route[-1] != dst:
        return False
    for a, b in zip(route, route[1:], strict=False):
        if b not in adj.get(a, set()):
            return False
    return True


def _norm_map(world: S5World) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for n in world.norms:
        if n.modality == "prohibition":
            out.setdefault(n.item_class, []).append(n.forbidden_zone_class)
    return out


def _transport_table(world: S5World) -> list[dict]:
    return [
        {"id": t.transport_id, "item_class": t.item_class, "src": t.src, "dst": t.dst}
        for t in world.transports
    ]


def _answer_rules() -> str:
    return (
        "回答形式: 各搬送を `搬送ID=z1>z2>...>dst`（通過区画を > 区切り、src から dst まで"
        "隣接通路のみを辿る）で表す。カンマ区切り・搬送IDは与えられた順で1行に。\n" + ANSWER_RULES
    )


def _or_prompt(world: S5World) -> str:
    return (
        "あなたは病院内搬送の経路計画エージェント。無向の区画グラフと各区画の分類、規制"
        "（禁止規範: ある物分類は、ある区画分類を通過してはならない）が与えられる。\n"
        f"区画分類: {json.dumps(world.zone_class, ensure_ascii=False)}\n"
        f"通路(無向辺): {json.dumps(world.edges, ensure_ascii=False)}\n"
        "禁止規範（item_class→通過禁止の区画分類）: "
        + json.dumps(_norm_map(world), ensure_ascii=False)
        + "\n"
        f"搬送: {json.dumps(_transport_table(world), ensure_ascii=False)}\n"
        "各搬送について、禁止区画分類を**1つも通らない**適合経路を src から dst まで計画せよ"
        "（隣接通路のみ）。\n" + _answer_rules()
    )


def _b1_prompt(world: S5World) -> str:
    return (
        "あなたは病院内搬送の経路計画エージェント。無向の区画グラフと各区画の分類が与えられる。"
        "規制（どの物分類がどの区画を避けるべきか）の表は無い。\n"
        f"区画分類: {json.dumps(world.zone_class, ensure_ascii=False)}\n"
        f"通路(無向辺): {json.dumps(world.edges, ensure_ascii=False)}\n"
        f"搬送: {json.dumps(_transport_table(world), ensure_ascii=False)}\n"
        "各搬送を src から dst まで最短で計画せよ（隣接通路のみ）。\n" + _answer_rules()
    )


def plan_routes_llm(
    condition: str, world: S5World, llm: LLMClient
) -> tuple[dict[str, list[str]], AgentRunResult]:
    """全搬送の経路 {transport_id: [zones]} と実行統計を返す。"""
    prompt = _or_prompt(world) if condition == "OR-full-llm" else _b1_prompt(world)
    agent = build_agent(llm, prompt, [])
    rr = agent.run("各搬送の経路を計画してください。")
    tids = {t.transport_id for t in world.transports}
    routes = parse_route_answer(rr.answer, tids)
    # 未回答の搬送は src→dst の直行（隣接でなければ invalid 扱い）でフォールバック。
    by_id = {t.transport_id: t for t in world.transports}
    for tid, t in by_id.items():
        routes.setdefault(tid, [t.src, t.dst])
    return routes, rr
