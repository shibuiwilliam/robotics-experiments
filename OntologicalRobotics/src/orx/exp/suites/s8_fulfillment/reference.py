"""S8 条件別 planner（アクション計画）＋ drive（アクション層を通した実行）。

各 planner は**その条件が許す情報のみ**を引数に取る（リギング防止）:
- plan_or:  OntologyView（正規化能力・規制・所有）→ 適合機体・正解処分・所有者部屋。
- plan_b1:  VendorView（非正規化能力・規制/所有なし）→ 語彙横断不可で先頭機体・naive 既定。
- plan_b0:  生観測のみ（B1 と同じく構造を持たない）→ 同様に naive。

drive はアクションを `ActionExecutor`（送信基準＋擬似VLA＋効果）に通し、OR-full は
custody/書戻しを `WriteBack` で残す（監査可能性）。返り値は最終ゾーン・レシート・WriteBack。
"""

from __future__ import annotations

from orx.common.config import WorldConfig
from orx.common.seeding import SeedTree
from orx.exp.act_loop import WriteBack
from orx.kg.world_graph import WorldGraph
from orx.oracle.scenarios.s8 import DELIVER, DISPOSE, MOVE
from orx.skills.action import ActionExecutor, ActionReceipt, DictEffectSink, always_valid
from orx.skills.server import SkillRequest, SkillServer

from .generator import (
    NAIVE_DELIVER_ZONE,
    NAIVE_DISPOSE_ZONE,
    OntologyView,
    VendorView,
    box_positions,
    build_orders,
    initial_zones,
)
from .validation import or_validator


def _request(robot: str, barcode: str, position, dest: str) -> SkillRequest:
    return SkillRequest(
        robot_id=robot,
        skill="pick_and_place",
        target_barcode=barcode,
        target_position=position,
        dest_zone=dest,
    )


def _capable_robot(view: OntologyView, item_weight: float) -> str:
    """必要重量を満たす最小可搬の機体（決定的: payload 昇順→name）。"""
    ranked = sorted(view.robot_payload.items(), key=lambda kv: (kv[1], kv[0]))
    for name, payload in ranked:
        if payload + 1e-9 >= item_weight:
            return name
    return ranked[-1][0] if ranked else ""  # 誰も満たさなければ最大機体


def plan_or(world: WorldConfig, view: OntologyView) -> list[SkillRequest]:
    """OR-full: 正規化オントロジーから適合機体・正解先を導く。"""
    pos = box_positions(world)
    plan: list[SkillRequest] = []
    for order in build_orders(world):
        bc = order.barcode
        weight = view.item_weight.get(bc, 0.0)
        robot = _capable_robot(view, max(weight, view.required_payload.get(bc, 0.0)))
        if order.kind == MOVE:
            plan.append(_request(robot, bc, pos[bc], view.move_dest[bc]))
        elif order.kind == DISPOSE:
            cls = view.item_class[bc]
            plan.append(_request(robot, bc, pos[bc], view.disposal_route[cls]))
        elif order.kind == DELIVER:
            plan.append(_request(robot, bc, pos[bc], view.owner_room[bc]))
    return plan


def plan_baseline(world: WorldConfig, vendor: VendorView) -> list[SkillRequest]:
    """B1/B0: 共通オントロジーが無く語彙横断できない → 先頭機体＋naive 既定。

    能力契約を異種スキーマ間で比較できないため重量物にも先頭の軽量機を選ぶ（→ 実行失敗）。
    規制・所有の語彙を持たないため処分は一般廃棄（禁止ゾーン）・配送は別室（誤配送）。
    """
    pos = box_positions(world)
    from .generator import MOVE_DEST

    first_robot = vendor.robots_in_order[0] if vendor.robots_in_order else ""
    plan: list[SkillRequest] = []
    for order in build_orders(world):
        bc = order.barcode
        if order.kind == MOVE:
            dest = MOVE_DEST[bc]
        elif order.kind == DISPOSE:
            dest = NAIVE_DISPOSE_ZONE
        else:
            dest = NAIVE_DELIVER_ZONE
        plan.append(_request(first_robot, bc, pos[bc], dest))
    return plan


def drive(
    plan: list[SkillRequest],
    world: WorldConfig,
    seeds: SeedTree,
    validate=always_valid,
    emit_custody: bool = False,
) -> tuple[dict[str, str], list[ActionReceipt], WriteBack, WorldGraph]:
    """アクション列をアクション層に通して実行する（検証→擬似VLA→効果→書戻し）。"""
    server = SkillServer(world, seeds.child("s8-skills").rng())
    sink = DictEffectSink(initial_zones(world))
    graph = WorldGraph()
    write_back = WriteBack(graph, seeds, emit_custody=emit_custody)
    executor = ActionExecutor(server, sink, validate=validate)
    receipts: list[ActionReceipt] = []
    for i, request in enumerate(plan):
        prior = sink.zone_of(request.target_barcode)
        receipt = executor.apply(request, at_time=2.0 + i, prior_zone=prior)
        write_back.record(receipt, prior)
        receipts.append(receipt)
    return dict(sink.state), receipts, write_back, graph


def recovery_probe(
    is_or: bool, world: WorldConfig, ontology: OntologyView, seeds: SeedTree
) -> float:
    """注入された誤動作からの回復率（H8・可逆性）。

    搬送物が誤ゾーンに着地する transient mishap を 1 件注入し、各条件が**自分の知識のみ**で
    検出・補償（アンドゥ→正しい再実行）できるかを測る。OR は共通オントロジーで正解先を知り
    custody で監査できるため回復する。ベースラインは正解先を知らず（OntologyView を持たない）
    検出すらできないため回復しない。本 probe は独立な world 状態で走り、本評価を汚染しない。
    """
    orders = build_orders(world)
    deliver = next((o for o in orders if o.kind == DELIVER), None)
    if deliver is None:
        return 1.0  # 回復対象が無い（vacuous）
    item, correct = deliver.barcode, deliver.correct_dest
    wrong = "atrium" if correct != "atrium" else "room_102"

    server = SkillServer(world, seeds.child("s8-recovery").rng())
    sink = DictEffectSink(initial_zones(world))
    graph = WorldGraph()
    write_back = WriteBack(graph, seeds.child("s8-recovery-wb"), emit_custody=is_or)
    executor = ActionExecutor(
        server, sink, validate=or_validator(ontology) if is_or else always_valid
    )
    sink.apply(item, wrong, 10.0)  # fault: 誤ゾーンに着地（外因的 mishap）

    # 検出可否は「正解先を知っているか」= 共通オントロジーの有無で決まる（no-rigging）。
    known = ontology.owner_room.get(item) if is_or else None
    if known is None:
        return 0.0  # 正解先を知らず検出不能 → 回復不可（来歴語彙も無い）
    if sink.zone_of(item) == known:
        return 1.0
    # 補償アクションをアクション層経由で実行（custody に補正を残す＝可逆な監査）。
    receipt = executor.apply(
        _request(
            _capable_robot(ontology, ontology.item_weight.get(item, 0.0)),
            item,
            box_positions(world)[item],
            known,
        ),
        at_time=11.0,
        prior_zone=wrong,
    )
    write_back.record(receipt, wrong)
    return 1.0 if (receipt.effect_applied and sink.zone_of(item) == known) else 0.0


def run_condition(
    condition: str,
    world: WorldConfig,
    ontology: OntologyView,
    vendor: VendorView,
    seeds: SeedTree,
):
    """1 条件の決定的ロールアウト。返り値 (final_zones, receipts, write_back, graph, recovery)。"""
    is_or = condition == "OR-full"
    if is_or:
        final, receipts, wb, graph = drive(
            plan_or(world, ontology),
            world,
            seeds,
            validate=or_validator(ontology),
            emit_custody=True,
        )
    elif condition in ("B1", "B0"):
        final, receipts, wb, graph = drive(
            plan_baseline(world, vendor),
            world,
            seeds,
            validate=always_valid,
            emit_custody=False,
        )
    else:
        raise ValueError(f"S8 の未知の決定的条件: {condition!r}")
    recovery = recovery_probe(is_or, world, ontology, seeds)
    return final, receipts, wb, graph, recovery
