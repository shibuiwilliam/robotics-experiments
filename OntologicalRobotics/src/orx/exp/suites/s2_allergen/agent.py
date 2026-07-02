"""S2 エージェント条件（agent 射程・live, R-3a）。

各把持可否クエリ（gripper を free_of から守れるか）を**実 LLM エージェント**が解く。
- OR-full-llm: 汚染グラフ(possiblyContaminatedBy 推移閉包・洗浄反映済み)を SPARQL で参照。
- B1-llm:      ロボット別の生接触＋固有アレルゲンのみ（横断融合・閉包なし）→ 自力で推論。
- B0-llm:      直近1接触のスナップショットを inline（履歴なし）。

回答 `ANSWER: yes|no` を bool に写像し、既存の真値 query.truth_allowed と突き合わせる。
"""

from __future__ import annotations

from orx.agent.toolloop import AgentRunResult
from orx.common import iri
from orx.common.providers import LLMClient
from orx.exp.agent_tools import ANSWER_RULES, build_agent, facts_tool, sparql_tool
from orx.kg.world_graph import CURRENT_GRAPH

AGENT_CONDITIONS = ["OR-full-llm", "B1-llm", "B0-llm"]

_ANSWER = (
    "回答形式: 把持してよいなら `ANSWER: yes`、アレルゲン混入の恐れがあり把持すべきでないなら "
    "`ANSWER: no`。\n" + ANSWER_RULES
)


def parse_yes_no(answer: str, default: bool = True) -> bool:
    """`yes/no` を bool に。yes=把持可。曖昧時は default（安全側は呼び出し側で選ぶ）。"""
    t = (answer or "").strip().lower()
    if t.startswith("yes") or t.startswith("はい") or t == "true":
        return True
    if t.startswith("no") or t.startswith("いいえ") or t == "false":
        return False
    return default


def _or_prompt(gripper: str, free_of: list[str]) -> str:
    obj = iri.entity("object", gripper)
    return (
        "あなたは食品工場で把持可否を判定するエージェント。世界グラフ(RDF)に接触履歴から"
        "導出済みの汚染関係がある。\n"
        "ツール: sparql_query。現在信念は GRAPH <" + CURRENT_GRAPH + "> の中。\n"
        "次のクエリで対象が現在 possiblyContaminatedBy であるアレルゲンを取れる:\n"
        "PREFIX orx-st: <https://orx.local/onto/st#>\n"
        "SELECT ?a WHERE {\n"
        f"  GRAPH <{CURRENT_GRAPH}> {{ <{obj}> orx-st:possiblyContaminatedBy ?a }}\n"
        "}\n"
        "?a は https://orx.local/id/allergen/{name} 形式。\n"
        f"対象 {gripper} は次のアレルゲンを**含んではならない**: {free_of}。\n"
        "返ったアレルゲン name のいずれかが上の禁止集合に入るなら把持不可。\n" + _ANSWER
    )


def _b1_prompt(gripper: str, free_of: list[str], per_robot: dict, intrinsic: dict) -> str:
    import json as _json

    return (
        "あなたは把持可否を判定するエージェント。共通の知識基盤は無い。\n"
        "各ロボットが独立に観測した接触イベント（横断融合なし・推移閉包は自分で計算）:\n"
        + _json.dumps(per_robot, ensure_ascii=False, default=str)
        + "\n各物体の固有アレルゲン:\n"
        + _json.dumps(intrinsic, ensure_ascii=False, default=str)
        + f"\n対象 {gripper} は次を含んではならない: {free_of}。\n"
        "接触は推移的にアレルゲンを移す（A が peanut 接触, A→B 接触なら B も peanut の恐れ）。"
        "別ロボの接触を同一物体で結ぶ手段は無い。\n" + _ANSWER
    )


def _b0_prompt(gripper: str, free_of: list[str], snapshot: list, intrinsic: dict) -> str:
    import json as _json

    return (
        "あなたは把持可否を判定するエージェント。以下が利用できる全情報である"
        "（直近1接触のスナップショットのみ・履歴なし）:\n"
        "直近接触: "
        + _json.dumps(snapshot, ensure_ascii=False, default=str)
        + "\n固有アレルゲン: "
        + _json.dumps(intrinsic, ensure_ascii=False, default=str)
        + f"\n対象 {gripper} は次を含んではならない: {free_of}。\n"
        + _ANSWER
    )


def make_query_agent(
    condition: str,
    graph: object,
    query: object,
    seen_contacts: list,
    observers: list[str],
    intrinsic: dict,
    llm: LLMClient,
):
    """1 クエリ分のエージェントを構成する（条件別の情報窓口）。"""
    gripper = query.gripper
    free_of = list(query.free_of)
    if condition == "OR-full-llm":
        return build_agent(llm, _or_prompt(gripper, free_of), [sparql_tool(graph)])
    if condition == "B1-llm":
        per_robot = {
            r: [{"a": c.a, "b": c.b, "t": c.sim_time} for c in seen_contacts if c.observer == r]
            for r in observers
        }
        prompt = _b1_prompt(gripper, free_of, per_robot, intrinsic)
        return build_agent(
            llm,
            prompt,
            [
                facts_tool(
                    "all_contacts",
                    "全ロボットの接触観測（横断融合なし）",
                    per_robot,
                )
            ],
        )
    if condition == "B0-llm":
        snap = [{"a": c.a, "b": c.b, "t": c.sim_time} for c in seen_contacts[-1:]]
        return build_agent(llm, _b0_prompt(gripper, free_of, snap, intrinsic), [])
    raise ValueError(f"S2 の未知のエージェント条件: {condition!r}")


def solve_query_llm(
    condition: str,
    graph: object,
    query: object,
    seen_contacts: list,
    observers: list[str],
    intrinsic: dict,
    llm: LLMClient,
) -> tuple[bool, AgentRunResult]:
    agent = make_query_agent(condition, graph, query, seen_contacts, observers, intrinsic, llm)
    q = f"対象 {query.gripper} を把持してよいですか？（{list(query.free_of)} を含んではいけません）"
    rr = agent.run(q)
    # 安全側 default: 不明なら「把持不可(no=False)」にすると過保守。判定不能は許可寄り=True にし、
    # 取りこぼし（安全違反）を条件の弱さとして可視化する。
    return parse_yes_no(rr.answer, default=True), rr
