"""S1 エージェント条件（agent 射程・live, R-3a）。

決定的ソルバ（reference.py）と同じ回収列挙タスクを、**実 LLM エージェント**が
ツール経由で解く。条件別に与える窓口を変える（T2 と同型）:
- OR-full-llm: 世界グラフ(SPARQL)＋lot台帳(SQL)。anchoring 融合済みグラフで搬送個体も辿れる。
- B1-llm:      ロボット個別の生観測＋lot台帳(SQL)。横断融合が無いため搬送済みの現在地は不明。
- B0-llm:      生観測ダンプ＋lot台帳を inline（ツール無し1ショット）。

エージェントの最終回答 `ANSWER: bc=zone,...` を `RecallAnswer` に写像し、**既存の
score_recall をそのまま適用**する（採点は不変＝独立）。LLM はプロバイダIF経由のみ。
"""

from __future__ import annotations

from pathlib import Path

from orx.agent.toolloop import AgentRunResult
from orx.business.db import BusinessDB
from orx.common import iri
from orx.common.providers import LLMClient
from orx.exp.agent_tools import (
    ANSWER_RULES,
    build_agent,
    observations_tool,
    sparql_tool,
    sql_tool,
)
from orx.exp.suites.s1_lot_recall.reference import RecallAnswer
from orx.kg.world_graph import CURRENT_GRAPH

AGENT_CONDITIONS = ["OR-full-llm", "B1-llm", "B0-llm"]

_LOT_SCHEMA = "lot_members(barcode,lot_id), recall_orders(recall_id,lot_id,recall_time)"

_TASK = (
    "回収対象ロット {lot} に属する物理個体（個装バーコード）と、各個体の**現在ゾーン**を"
    "すべて列挙せよ。lot のメンバーは業務台帳 lot_members で分かる。\n"
    "回答形式: 各対象を `バーコード=ゾーン名` で表し、カンマ区切り・バーコード辞書順で1行に。"
    "現在地が不明な対象は `バーコード=unknown`。対象が無ければ `ANSWER: none`。\n"
    f"{ANSWER_RULES}"
)


def parse_recall_answer(answer: str) -> RecallAnswer:
    """`bc=zone,bc=unknown,...` 形式の最終回答を RecallAnswer に写像する。"""
    targets: dict[str, str | None] = {}
    text = (answer or "").strip()
    if text.lower() in ("", "none", "unknown"):
        return RecallAnswer(targets={})
    for part in text.split(","):
        if "=" not in part:
            continue
        bc, _, zone = part.partition("=")
        bc = bc.strip()
        zone = zone.strip()
        if not bc:
            continue
        if zone.lower() in ("unknown", "", "none"):
            targets[bc] = None
        elif "/" in zone:  # ゾーンIRIが来たら末尾の name を取り出す（堅牢化）
            targets[bc] = zone.rstrip("/").rsplit("/", 1)[-1]
        else:
            targets[bc] = zone
    return RecallAnswer(targets=targets)


def _or_prompt(lot: str) -> str:
    lot_iri = iri.entity("lot", lot)
    return (
        "あなたは倉庫の世界グラフ(RDF)と業務台帳(SQL)を窓口に回収を遂行するエージェント。\n"
        "ツール: sparql_query / business_sql。\n"
        "**重要**: 現在信念はすべて名前付きグラフ <" + CURRENT_GRAPH + "> の中にある。"
        "クエリは必ず `GRAPH <" + CURRENT_GRAPH + "> { ... }` で囲むこと。\n"
        f"ロット {lot} の IRI は <{lot_iri}>。\n"
        "次の1クエリで対象バーコードと現在ゾーンを同時に取れる（このまま sparql_query に渡せる）:\n"
        "PREFIX orx-upper: <https://orx.local/onto/upper#>\n"
        "PREFIX orx-st: <https://orx.local/onto/st#>\n"
        "PREFIX orx-biz: <https://orx.local/onto/biz#>\n"
        "SELECT ?bc ?zone WHERE {\n"
        f"  GRAPH <{CURRENT_GRAPH}> {{\n"
        f"    ?si orx-biz:memberOfLot <{lot_iri}> ; orx-biz:hasBarcode ?bc .\n"
        "    OPTIONAL { ?e orx-upper:hasIdentifier ?bc ; orx-st:inZone ?zone . }\n"
        "  }\n"
        "}\n"
        "?zone は https://orx.local/id/zone/{name} 形式なので末尾の name を回答に使う"
        "（?zone が無い対象は unknown）。搬送中で記号IDが読めない個体も、グラフは"
        "横断同一性で現在地を解決している。\n" + _TASK.format(lot=lot)
    )


def _zone_table(world: object) -> str:
    import json as _json

    rows = [{"name": z.name, "center": list(z.center), "size": list(z.size)} for z in world.zones]
    return _json.dumps(rows, ensure_ascii=False)


def _b1_prompt(lot: str, robot_ids: list[str], world: object) -> str:
    return (
        "あなたは回収を遂行するエージェント。共通の知識基盤は無い。\n"
        "ツール: robot_observations（ロボット毎に形式・単位が異なる生観測）と business_sql。\n"
        f"ロボット: {robot_ids}。バーコードを読めるロボットと読めないロボットがある。\n"
        f"ゾーン定義（中心と全幅, m）: {_zone_table(world)}\n"
        "各物体の座標がどのゾーンに入るかは自分で計算する。横断的な同一性解決は無いため、"
        "別ロボットが見た物体を同一個体と結ぶ手段は無い（搬送でID不可読になった個体は現在地不明）。\n"
        + _TASK.format(lot=lot)
    )


def _b0_prompt(lot: str, latest_obs: dict, lot_rows: list[dict], world: object) -> str:
    import json as _json

    return (
        "あなたは回収を遂行するエージェント。以下が利用できる全情報である。\n"
        f"ゾーン定義（中心と全幅, m）: {_zone_table(world)}\n"
        "ロボット観測（ベンダー固有形式・単位は形式毎に異なる・横断融合なし）:\n"
        + _json.dumps(latest_obs, ensure_ascii=False, default=str)
        + "\nlot_members 台帳:\n"
        + _json.dumps(lot_rows, ensure_ascii=False, default=str)
        + "\n座標がどのゾーンに入るかは自分で計算する。\n"
        + _TASK.format(lot=lot)
    )


def solve_agent_llm(
    condition: str,
    graph: object,
    run_dir: str,
    latest_raw: dict,
    recall_lot: str,
    llm: LLMClient,
    world: object,
) -> tuple[RecallAnswer, AgentRunResult]:
    """1 条件のエージェントを走らせ (RecallAnswer, 実行統計) を返す。"""
    db = BusinessDB(Path(run_dir) / "wms.sqlite")
    if condition == "OR-full-llm":
        tools = [sparql_tool(graph), sql_tool(db, _LOT_SCHEMA)]
        agent = build_agent(llm, _or_prompt(recall_lot), tools)
    elif condition == "B1-llm":
        obs = {rid: o.payload for rid, o in latest_raw.items()}
        tools = [observations_tool(obs), sql_tool(db, _LOT_SCHEMA)]
        agent = build_agent(llm, _b1_prompt(recall_lot, sorted(obs), world), tools)
    elif condition == "B0-llm":
        obs = {rid: o.payload for rid, o in latest_raw.items()}
        lot_rows = db.query("SELECT barcode, lot_id FROM lot_members")
        agent = build_agent(llm, _b0_prompt(recall_lot, obs, lot_rows, world), [])
    else:
        raise ValueError(f"S1 の未知のエージェント条件: {condition!r}")
    result = agent.run(f"ロット {recall_lot} の回収対象と現在ゾーンを列挙してください。")
    return parse_recall_answer(result.answer), result
