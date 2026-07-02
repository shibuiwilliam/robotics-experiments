"""シナリオ横断のエージェント・ツール束（R-3a）。

T2 で確立した「世界グラフ(SPARQL)/業務DB(SQL)/生観測 を窓口にする LLM エージェント」
パターンをシナリオへ再利用するための共通部品。各シナリオは本モジュールのツールを
組み合わせ、条件別（OR-full / B1 / B0）のエージェントを構成する。

不変条件（PROJECT.md §5.2-5）: エージェントが世界を知る窓は**ツール経由のみ**。
ここにシム真値への直接アクセスは存在しない（B0/B1 でも記録済み観測/業務DBのみ）。
採点は呼び出し側 scorer が行い、本モジュールは scorer を import しない（一方向）。
"""

from __future__ import annotations

import json

from orx.agent.toolloop import ToolAgent, ToolFn, ToolSpec
from orx.business.db import BusinessDB
from orx.common.providers import LLMClient
from orx.kg.world_graph import CURRENT_GRAPH, WorldGraph

ANSWER_RULES = (
    "回答規則: 推論の最後に必ず `ANSWER: <値>` を1行で書く。"
    "形式は各質問の指示に従う。分からない/該当なしは `ANSWER: unknown`。"
)


def sparql_tool(graph: WorldGraph) -> tuple[ToolSpec, ToolFn]:
    """世界グラフ(RDF)への SPARQL SELECT ツール。"""
    spec = ToolSpec(
        name="sparql_query",
        description=(
            "世界グラフへの SPARQL SELECT。現在信念は "
            f"GRAPH <{CURRENT_GRAPH}> {{ ... }} の中を問い合わせる。"
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    def run(args: dict) -> str:
        try:
            return json.dumps(graph.query(str(args["query"])), ensure_ascii=False)
        except Exception as exc:  # クエリ誤りは文字列でエージェントに返す
            return f"error: {type(exc).__name__}: {exc}"

    return spec, run


def sql_tool(db: BusinessDB, schema_hint: str) -> tuple[ToolSpec, ToolFn]:
    """業務DB(SQLite)への読み取り専用 SQL ツール。schema_hint にテーブル定義を渡す。"""
    spec = ToolSpec(
        name="business_sql",
        description=f"業務DB(SQLite)への読み取り専用SQL。テーブル: {schema_hint}",
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
        try:
            return json.dumps(db.query(sql), ensure_ascii=False, default=str)
        except Exception as exc:
            return f"error: {type(exc).__name__}: {exc}"

    return spec, run


def observations_tool(latest_obs: dict[str, dict]) -> tuple[ToolSpec, ToolFn]:
    """ロボット個別の最新観測（ベンダー固有・横断融合なし）を返すツール（B1用）。"""
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
        return json.dumps(payload, ensure_ascii=False, default=str)

    return spec, run


def facts_tool(name: str, description: str, data: object) -> tuple[ToolSpec, ToolFn]:
    """任意の構造化データを返す読み取り専用ツール（条件別の限定情報源に使う）。"""
    spec = ToolSpec(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}},
    )

    payload = json.dumps(data, ensure_ascii=False, default=str)

    def run(_args: dict) -> str:
        return payload

    return spec, run


def build_agent(
    llm: LLMClient,
    system_prompt: str,
    tools: list[tuple[ToolSpec, ToolFn]],
    max_turns: int = 6,
) -> ToolAgent:
    """条件別エージェントを構成する薄いラッパ（ツール無し=B0 一発回答も可）。"""
    return ToolAgent(llm, tools, system_prompt, max_turns=max_turns)
