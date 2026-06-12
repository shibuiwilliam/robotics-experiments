"""T2 業務‐物理クエリスイート（H6, H7）。

「受注Xの品はいまどこか」型の質問を真値照合で採点する。
- 条件 OR-reference: 決定的リファレンスソルバ（期待SPARQL/SQL）— 表現の上限を
  オフラインで証明する（LLM不要）。
- 条件 OR-full / B1 / B0: C7エージェント（LLM）。スタブでは正答できないため、
  本計測は provider mode=openai（要承認・キャッシュ記録）で行う。

全質問でトークンを記録し、正答率/トークン（知識効率, H7）を算出する。
"""

from __future__ import annotations

import json
from pathlib import Path

from orx.agent.toolloop import AgentRunResult, ToolAgent, ToolFn, ToolSpec
from orx.business.db import BusinessDB, WmsRecord
from orx.business.lifting import wms_claims
from orx.common import iri
from orx.common.config import RunConfig, WorldConfig
from orx.common.providers import LLMClient
from orx.common.schemas import StrictModel, TruthState
from orx.common.seeding import SeedTree
from orx.exp.episode import _apply_condition, _Pipeline
from orx.kg.world_graph import CURRENT_GRAPH, WorldGraph
from orx.replay.io import RunReader


class T2Question(StrictModel):
    qid: str
    qtype: str
    text: str
    truth: str  # 正規化済み正答


class T2Answer(StrictModel):
    qid: str
    qtype: str
    condition: str
    answer: str
    truth: str
    correct: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0


# ------------------------------------------------------------- normalization


def normalize(value: str) -> str:
    parts = [p.strip() for p in value.strip().split(",") if p.strip()]
    if not parts:
        return "unknown"
    return ",".join(sorted(parts)).lower()


def _zone_key(zone_iri: str) -> str:
    return iri.parse_entity(zone_iri)[1]


# ---------------------------------------------------------------- generation


def generate_questions(
    record: WmsRecord, world: WorldConfig, final_truth: TruthState
) -> list[T2Question]:
    """WMSと最終真値から質問＋正答を決定的に生成する。"""
    barcode_to_order = {i["barcode"]: i["order_id"] for i in record.instructions}
    order_status = {str(o["order_id"]): str(o["status"]) for o in record.orders}
    order_sku = {str(o["order_id"]): str(o["sku"]) for o in record.orders}
    order_dest = {str(o["order_id"]): str(o["destination"]) for o in record.orders}
    fragile_skus = {str(s["sku"]) for s in record.skus if s["fragile"]}
    box_zone = {o.barcode: o.zone for o in final_truth.objects if o.barcode}
    zones = [z.name for z in world.zones]

    questions: list[T2Question] = []

    # Q1: 受注の現在ゾーン
    for inst in record.instructions:
        oid = inst["order_id"]
        zone = box_zone.get(inst["barcode"]) or "unknown"
        questions.append(
            T2Question(
                qid=f"where-{oid}",
                qtype="where_order",
                text=f"受注 {oid} の品は現在どのゾーンにあるか？",
                truth=normalize(zone),
            )
        )
    # Q2: 物理個体に紐づかない受注（負例 — unknown と答えるべき）
    for oid, status in order_status.items():
        if status == "open" and oid not in {i["order_id"] for i in record.instructions}:
            questions.append(
                T2Question(
                    qid=f"where-{oid}",
                    qtype="where_order_negative",
                    text=f"受注 {oid} の品は現在どのゾーンにあるか？",
                    truth="unknown",
                )
            )
    # Q3: ゾーン内の受注一覧
    for zone in zones:
        oids = sorted(
            barcode_to_order[bc]
            for bc, z in box_zone.items()
            if z == zone and bc in barcode_to_order
        )
        questions.append(
            T2Question(
                qid=f"orders-in-{zone}",
                qtype="orders_in_zone",
                text=(
                    f"ゾーン {zone} に現在ある箱に対応する受注IDをすべて挙げよ"
                    "（カンマ区切り、無ければ unknown）。"
                ),
                truth=normalize(",".join(oids)) if oids else "unknown",
            )
        )
    # Q4: ゾーン内の受注紐づき箱の数
    for zone in zones:
        count = sum(1 for bc, z in box_zone.items() if z == zone and bc in barcode_to_order)
        questions.append(
            T2Question(
                qid=f"count-in-{zone}",
                qtype="count_in_zone",
                text=f"ゾーン {zone} に現在ある、受注に紐づく箱は何個か？数字のみ。",
                truth=str(count),
            )
        )
    # Q5: 壊れやすいSKUの受注で handoff にあるもの
    fragile_in_handoff = sorted(
        barcode_to_order[bc]
        for bc, z in box_zone.items()
        if z == "handoff"
        and bc in barcode_to_order
        and order_sku[barcode_to_order[bc]] in fragile_skus
    )
    questions.append(
        T2Question(
            qid="fragile-in-handoff",
            qtype="fragile_in_zone",
            text=(
                "壊れやすい(fragile)SKUの受注のうち、品が現在ゾーン handoff にある"
                "受注IDをすべて挙げよ（カンマ区切り、無ければ unknown）。"
            ),
            truth=normalize(",".join(fragile_in_handoff)) if fragile_in_handoff else "unknown",
        )
    )
    # Q6: handoff にある品の出荷先
    dests = sorted(
        {
            order_dest[barcode_to_order[bc]]
            for bc, z in box_zone.items()
            if z == "handoff" and bc in barcode_to_order
        }
    )
    questions.append(
        T2Question(
            qid="dest-of-handoff",
            qtype="destinations_in_zone",
            text=(
                "ゾーン handoff に現在ある品の出荷先(destination)を重複なくすべて"
                "挙げよ（カンマ区切り、無ければ unknown）。"
            ),
            truth=normalize(",".join(dests)) if dests else "unknown",
        )
    )
    return questions


# ---------------------------------------------------------- reference solver


def _entity_zone_by_barcode(graph: WorldGraph, barcode: str) -> str | None:
    rows = graph.query(
        f"""
        PREFIX orx-upper: <https://orx.local/onto/upper#>
        PREFIX orx-st: <https://orx.local/onto/st#>
        SELECT ?z WHERE {{
          GRAPH <{CURRENT_GRAPH}> {{
            ?e orx-upper:hasIdentifier "{barcode}" ; orx-st:inZone ?z .
          }}
        }}
        """
    )
    if not rows:
        return None
    return _zone_key(rows[0]["z"])


def _zone_barcodes(graph: WorldGraph, zone: str) -> list[str]:
    rows = graph.query(
        f"""
        PREFIX orx-upper: <https://orx.local/onto/upper#>
        PREFIX orx-st: <https://orx.local/onto/st#>
        SELECT ?bc WHERE {{
          GRAPH <{CURRENT_GRAPH}> {{
            ?e orx-st:inZone <{iri.entity("zone", zone)}> ;
               orx-upper:hasIdentifier ?bc .
          }}
        }}
        """
    )
    return sorted({r["bc"] for r in rows})


def reference_solve(graph: WorldGraph, db: BusinessDB, q: T2Question) -> str:
    """期待SPARQL/SQLによる決定的解答（表現の上限証明）。"""
    if q.qtype in ("where_order", "where_order_negative"):
        oid = q.qid.removeprefix("where-")
        rows = db.query(
            f"SELECT barcode FROM shipping_instructions WHERE order_id = '{oid}'"
        )
        if not rows:
            return "unknown"
        zone = _entity_zone_by_barcode(graph, str(rows[0]["barcode"]))
        return normalize(zone or "unknown")
    if q.qtype == "orders_in_zone":
        zone = q.qid.removeprefix("orders-in-")
        barcodes = _zone_barcodes(graph, zone)
        if not barcodes:
            return "unknown"
        placeholders = ",".join(f"'{b}'" for b in barcodes)
        rows = db.query(
            "SELECT order_id FROM shipping_instructions "
            f"WHERE barcode IN ({placeholders})"
        )
        oids = sorted({str(r["order_id"]) for r in rows})
        return normalize(",".join(oids)) if oids else "unknown"
    if q.qtype == "count_in_zone":
        zone = q.qid.removeprefix("count-in-")
        barcodes = _zone_barcodes(graph, zone)
        if not barcodes:
            return "0"
        placeholders = ",".join(f"'{b}'" for b in barcodes)
        rows = db.query(
            "SELECT COUNT(DISTINCT order_id) AS n FROM shipping_instructions "
            f"WHERE barcode IN ({placeholders})"
        )
        return str(rows[0]["n"])
    if q.qtype == "fragile_in_zone":
        barcodes = _zone_barcodes(graph, "handoff")
        if not barcodes:
            return "unknown"
        placeholders = ",".join(f"'{b}'" for b in barcodes)
        rows = db.query(
            "SELECT DISTINCT si.order_id FROM shipping_instructions si "
            "JOIN orders o ON o.order_id = si.order_id "
            "JOIN skus s ON s.sku = o.sku "
            f"WHERE si.barcode IN ({placeholders}) AND s.fragile = 1"
        )
        oids = sorted({str(r["order_id"]) for r in rows})
        return normalize(",".join(oids)) if oids else "unknown"
    if q.qtype == "destinations_in_zone":
        barcodes = _zone_barcodes(graph, "handoff")
        if not barcodes:
            return "unknown"
        placeholders = ",".join(f"'{b}'" for b in barcodes)
        rows = db.query(
            "SELECT DISTINCT o.destination FROM shipping_instructions si "
            "JOIN orders o ON o.order_id = si.order_id "
            f"WHERE si.barcode IN ({placeholders})"
        )
        dests = sorted({str(r["destination"]) for r in rows})
        return normalize(",".join(dests)) if dests else "unknown"
    raise ValueError(f"未知の質問タイプ: {q.qtype}")


# ------------------------------------------------------------ agent条件の構成

_ANSWER_RULES = (
    "回答規則: 最終行に `ANSWER: <値>` を1行で書く。複数値はカンマ区切りで"
    "辞書順に並べる。数は数字のみ。分からない/該当なしは `ANSWER: unknown`。"
)


def _sparql_tool(graph: WorldGraph) -> tuple[ToolSpec, ToolFn]:
    spec = ToolSpec(
        name="sparql_query",
        description=(
            "世界グラフへのSPARQL SELECT。現在信念は "
            f"GRAPH <{CURRENT_GRAPH}> {{ ... }} の中を問い合わせる。"
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    def run(args: dict) -> str:
        return json.dumps(graph.query(str(args["query"])), ensure_ascii=False)

    return spec, run


def _sql_tool(db: BusinessDB) -> tuple[ToolSpec, ToolFn]:
    spec = ToolSpec(
        name="business_sql",
        description=(
            "WMS(SQLite)への読み取り専用SQL。テーブル: skus(sku,name,weight_kg,fragile), "
            "orders(order_id,sku,quantity,status,destination), "
            "shipping_instructions(instruction_id,order_id,barcode)"
        ),
        parameters={
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    )

    def run(args: dict) -> str:
        sql = str(args["sql"]).strip()
        if not sql.lower().startswith("select"):
            return "error: SELECT文のみ実行できます"
        return json.dumps(db.query(sql), ensure_ascii=False, default=str)

    return spec, run


def _observations_tool(latest_obs: dict[str, dict]) -> tuple[ToolSpec, ToolFn]:
    spec = ToolSpec(
        name="robot_observations",
        description=(
            "指定ロボットの最新観測（ベンダー固有形式の生データ）を返す。"
            f"robot_id は {sorted(latest_obs)} のいずれか。形式はロボット毎に異なる。"
        ),
        parameters={
            "type": "object",
            "properties": {"robot_id": {"type": "string"}},
            "required": ["robot_id"],
        },
    )

    def run(args: dict) -> str:
        payload = latest_obs.get(str(args["robot_id"]))
        if payload is None:
            return f"error: unknown robot_id（対応: {sorted(latest_obs)}）"
        return json.dumps(payload, ensure_ascii=False)

    return spec, run


def _zone_table(world: WorldConfig) -> str:
    rows = [
        {"name": z.name, "center": list(z.center), "size": list(z.size)}
        for z in world.zones
    ]
    return json.dumps(rows, ensure_ascii=False)


def _or_system_prompt(world: WorldConfig) -> str:
    zones = ", ".join(z.name for z in world.zones)
    return (
        "あなたは倉庫の世界グラフ(RDF)とWMS(SQL)を窓口に質問へ答えるエージェント。\n"
        "ツール: sparql_query / business_sql。\n"
        "語彙: PREFIX orx-upper: <https://orx.local/onto/upper#> "
        "PREFIX orx-st: <https://orx.local/onto/st#> "
        "PREFIX orx-biz: <https://orx.local/onto/biz#>\n"
        "- 物理個体: ?e orx-upper:hasIdentifier ?barcode ; orx-st:inZone ?zone\n"
        f"- ゾーンIRIは https://orx.local/id/zone/{{name}}、name ∈ {{{zones}}}\n"
        "- 業務とはバーコードで結合: shipping_instructions.barcode = 物理個体の識別子\n"
        f"{_ANSWER_RULES}"
    )


def _b1_system_prompt(world: WorldConfig) -> str:
    return (
        "あなたは倉庫の質問に答えるエージェント。共通の知識基盤は無い。\n"
        "ツール: robot_observations（ロボット毎に形式・単位が異なる生観測）と "
        "business_sql（WMS）。\n"
        "ロボット arm_a はネスト形式・メートル、mobile_b はフラット形式・センチ"
        "メートルで座標を返す。バーコードは arm_a のみ読める（bc フィールド）。\n"
        f"ゾーン定義（中心と全幅, m）: {_zone_table(world)}\n"
        "座標がどのゾーンに入るかは自分で計算すること。\n"
        f"{_ANSWER_RULES}"
    )


def _b0_system_prompt(world: WorldConfig, latest_obs: dict[str, dict], db: BusinessDB) -> str:
    return (
        "あなたは倉庫の質問に答えるエージェント。以下が利用できる全情報である。\n"
        f"ゾーン定義（中心と全幅, m）: {_zone_table(world)}\n"
        "ロボット観測（ベンダー固有形式・単位は形式毎に異なる）:\n"
        + json.dumps(latest_obs, ensure_ascii=False)
        + "\nWMSテーブル(CSV):\n"
        + db.dump_csv()
        + f"\n{_ANSWER_RULES}"
    )


def latest_observations(reader: RunReader) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for obs in reader.observations():
        latest[obs.robot_id] = obs.payload
    return latest


def make_agent(
    condition: str,
    world: WorldConfig,
    graph: WorldGraph,
    db: BusinessDB,
    reader: RunReader,
    llm: LLMClient,
) -> ToolAgent:
    if condition == "OR-full":
        tools = [_sparql_tool(graph), _sql_tool(db)]
        return ToolAgent(llm, tools, _or_system_prompt(world))
    if condition == "B1":
        tools = [_observations_tool(latest_observations(reader)), _sql_tool(db)]
        return ToolAgent(llm, tools, _b1_system_prompt(world))
    if condition == "B0":
        return ToolAgent(
            llm, [], _b0_system_prompt(world, latest_observations(reader), db)
        )
    raise ValueError(f"T2の未知のエージェント条件: {condition!r}")


# ------------------------------------------------------------------ episode


class T2EpisodeArtifacts(StrictModel):
    run_dir: str
    db_path: str
    questions: list[T2Question]


def prepare_graph(
    run_dir: Path, condition: str, wms: WmsRecord, at_time: float
) -> WorldGraph:
    """記録から条件付きで世界グラフを再構築し、WMS主張を載せて物質化する。"""
    reader = RunReader(run_dir)
    config: RunConfig = reader.config()
    pipeline_condition = "OR-full" if condition in ("OR-reference", "B1", "B0") else condition
    _apply_condition(config, pipeline_condition)
    stage = _Pipeline(config, SeedTree(config.root_seed))
    for event in reader.events():
        stage.feed(event, writer=None)
    stage.drain_all(writer=None)
    for claim in wms_claims(wms, SeedTree(config.root_seed)):
        stage.graph.assert_claim(claim)
    stage.graph.refresh_current_graph(at_time)
    return stage.graph


def answer_questions(
    condition: str,
    questions: list[T2Question],
    graph: WorldGraph,
    db: BusinessDB,
    world: WorldConfig,
    reader: RunReader,
    llm: LLMClient | None,
) -> list[T2Answer]:
    answers: list[T2Answer] = []
    agent: ToolAgent | None = None
    if condition != "OR-reference":
        if llm is None:
            raise ValueError("LLMクライアントが必要です（OR-reference以外）")
        agent = make_agent(condition, world, graph, db, reader, llm)
    for q in questions:
        if condition == "OR-reference":
            raw = reference_solve(graph, db, q)
            result = AgentRunResult(
                answer=raw, turns=0, tool_call_count=0, prompt_tokens=0,
                completion_tokens=0,
            )
        else:
            assert agent is not None
            result = agent.run(q.text)
        norm = normalize(result.answer) if result.answer else "unknown"
        answers.append(
            T2Answer(
                qid=q.qid,
                qtype=q.qtype,
                condition=condition,
                answer=norm,
                truth=q.truth,
                correct=norm == q.truth,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                tool_calls=result.tool_call_count,
            )
        )
    return answers
