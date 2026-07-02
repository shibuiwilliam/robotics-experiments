"""S8 エージェント条件（agent 射程・live, K2）。

実 LLM が `act` ツールでアクションを発行して fulfillment を遂行する閉ループ。条件別の窓口:
- OR-full-llm: 世界グラフ蒸留（能力契約・規制・所有）＋ act ツール（送信基準を**ハード強制**）。
- B1-llm:      ロボット個別の非正規化能力（規制/所有なし）＋ act ツール（検証なし）。
- B0-llm:      生情報のみ＋ act ツール（検証なし）。
- OR-full-llm-guarded: OR-full-llm と同一（送信基準はツール側で常に強制）。S7 の教訓
                 「安全はプロンプトでなくツール強制で移植」を S8 のアクションゲートに適用。

採点は決定的版と同一（score_s8）＝独立。LLM はプロバイダIF経由のみ。stub では tool 呼出が
無いため遂行 0（配線の smoke のみ）。本体は K2（live・要コスト承認）で実測する。
"""

from __future__ import annotations

from orx.agent.tools.skill_tool import act_tool
from orx.common.config import WorldConfig
from orx.common.providers import LLMClient
from orx.common.seeding import SeedTree
from orx.exp.act_loop import WriteBack
from orx.exp.agent_tools import ANSWER_RULES, build_agent, facts_tool
from orx.kg.world_graph import WorldGraph
from orx.skills.action import ActionExecutor, ActionReceipt, DictEffectSink, always_valid
from orx.skills.server import SkillServer

from .generator import (
    OntologyView,
    VendorView,
    box_positions,
    build_orders,
    initial_zones,
)
from .scorer import S8Score, score_s8
from .validation import or_validator

AGENT_CONDITIONS = ["OR-full-llm", "B1-llm", "B0-llm", "OR-full-llm-guarded"]


def _orders_text(world: WorldConfig) -> str:
    lines = []
    for o in build_orders(world):
        lines.append(f"- {o.order_id}: {o.kind} 対象={o.barcode}")
    return "\n".join(lines)


def _prompt(condition: str, world: WorldConfig, knowledge: str) -> str:
    return (
        "あなたは倉庫の遂行エージェント。`act(robot_id, target_barcode, dest_zone)` で"
        "アクションを発行し、各オーダを正しい目的ゾーンへ遂行せよ。\n"
        "送信基準（能力・規範・所有権）を満たさないアクションは rejected（理由付き）になり"
        "世界は変わらない。rejected の理由を読んで再計画せよ。\n"
        f"ロボット: {[r.name for r in world.robots]}。\n"
        f"オーダ:\n{_orders_text(world)}\n"
        f"{knowledge}\n"
        f"全オーダを遂行したら最後に `ANSWER: done` と書け。{ANSWER_RULES}"
    )


def _knowledge(condition: str, ontology: OntologyView, vendor: VendorView) -> str:
    if condition.startswith("OR-full"):
        return (
            "共通オントロジー（正規化済み）が使える:\n"
            f"  能力契約(可搬kg): {ontology.robot_payload}\n"
            f"  規制処分ルート: {ontology.disposal_route}\n"
            f"  禁止ゾーン: {ontology.forbidden_zone_class} / 物分類: {ontology.item_class}\n"
            f"  所有者の部屋: {ontology.owner_room}\n"
            f"  移動先: {ontology.move_dest}（重量物の必要可搬: {ontology.required_payload}）"
        )
    return (
        "共通オントロジーは無い。ロボット個別の生能力（単位・キーがベンダー毎に異なる）のみ:\n"
        f"  {vendor.vendor_caps}\n"
        "規制・所有の知識は与えられない（推測するしかない）。"
    )


def run_condition_llm(
    condition: str,
    world: WorldConfig,
    ontology: OntologyView,
    vendor: VendorView,
    llm: LLMClient,
    seeds: SeedTree,
    truth,
) -> tuple[S8Score, int]:
    """1 条件の LLM 閉ループを走らせ (S8Score, 総トークン) を返す。"""
    server = SkillServer(world, seeds.child("s8-skills").rng())
    sink = DictEffectSink(initial_zones(world))
    graph = WorldGraph()
    is_or = condition.startswith("OR-full")
    write_back = WriteBack(graph, seeds, emit_custody=is_or)
    validate = or_validator(ontology) if is_or else always_valid
    executor = ActionExecutor(server, sink, validate=validate)
    positions = box_positions(world)
    receipts: list[ActionReceipt] = []

    def on_receipt(receipt: ActionReceipt, prior: str | None) -> None:
        write_back.record(receipt, prior)
        receipts.append(receipt)

    spec, fn = act_tool(
        executor,
        position_of=lambda bc: positions.get(bc),
        now=lambda: 2.0,
        zone_of=sink.zone_of,
        on_receipt=on_receipt,
    )
    info = facts_tool(
        "fulfillment_context",
        "遂行に使える知識（能力・規制・所有 or ベンダー生能力）。",
        _knowledge(condition, ontology, vendor),
    )
    agent = build_agent(
        llm,
        _prompt(condition, world, _knowledge(condition, ontology, vendor)),
        [(spec, fn), info],
        max_turns=8,
    )
    result = agent.run("全オーダを act で遂行してください。")
    # 回復率（H8・可逆性）は条件の能力（共通オントロジー＋custody の有無）で決まる決定的 probe。
    from orx.exp.suites.s8_fulfillment.reference import recovery_probe

    recovery = recovery_probe(is_or, world, ontology, seeds)
    score = score_s8(dict(sink.state), truth, receipts, write_back, recovery_rate=recovery)
    return score, result.prompt_tokens + result.completion_tokens
