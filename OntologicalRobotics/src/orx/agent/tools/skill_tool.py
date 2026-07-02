"""C7 — `act` ツール: エージェントがアクション型を発行する窓口（キネティック層）。

LLM が `act(robot_id, target_barcode, dest_zone)` を呼ぶと、注入済みの `ActionExecutor`
（送信基準の検証＋擬似VLA実行＋効果適用）を通し、**グラフ検証由来のレシート**
（applied/staged/rejected＋理由）をエージェントへ返す。理由が返ることで、エージェントは
不正アクション（能力不足・規範違反・所有権なし）を再計画できる。

不変条件5: ツールはシム真値を漏らさない。対象位置は注入された resolver（世界グラフ由来の
蒸留値）から得る。実行の確率的成否は `SkillServer` が境界として握る。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from orx.agent.toolloop import ToolFn, ToolSpec
from orx.common.schemas import Vec3
from orx.skills.action import ActionExecutor, ActionReceipt
from orx.skills.server import SkillRequest

PositionResolver = Callable[[str], Vec3 | None]
ZoneResolver = Callable[[str], str | None]
ReceiptHook = Callable[[ActionReceipt, str | None], None]


def act_tool(
    executor: ActionExecutor,
    position_of: PositionResolver,
    now: Callable[[], float],
    zone_of: ZoneResolver | None = None,
    on_receipt: ReceiptHook | None = None,
    skill: str = "pick_and_place",
) -> tuple[ToolSpec, ToolFn]:
    """アクション発行ツールを構成する。

    executor: 送信基準・効果適用を内包した実行器（条件別に validator/effect を注入済み）。
    position_of: barcode → 対象位置（世界グラフ蒸留値・真値ではない）。
    now: 現在のシム時刻（act-perceive ループの tick 時刻）。
    on_receipt: 適用後フック（exp 層が custody/書戻し claim を assert するのに使う）。
    """
    spec = ToolSpec(
        name="act",
        description=(
            "ロボットにアクションを発行する（対象個体を目的ゾーンへ運ぶ）。"
            "送信基準（能力・規範・所有権）を満たさないと rejected（理由付き）になり"
            "世界は変わらない。低確信は staged（人間確認へ委譲）。"
            "引数: robot_id, target_barcode, dest_zone。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "robot_id": {"type": "string"},
                "target_barcode": {"type": "string"},
                "dest_zone": {"type": "string"},
            },
            "required": ["robot_id", "target_barcode", "dest_zone"],
        },
    )

    def run(args: dict) -> str:
        robot = str(args.get("robot_id", "")).strip()
        barcode = str(args.get("target_barcode", "")).strip()
        dest = str(args.get("dest_zone", "")).strip()
        if not (robot and barcode and dest):
            return json.dumps(
                {"status": "rejected", "reason": "robot_id/target_barcode/dest_zone は必須"},
                ensure_ascii=False,
            )
        position = position_of(barcode)
        if position is None:
            return json.dumps(
                {"status": "rejected", "reason": f"対象 {barcode} の位置が世界グラフに無い"},
                ensure_ascii=False,
            )
        request = SkillRequest(
            robot_id=robot,
            skill=skill,
            target_barcode=barcode,
            target_position=position,
            dest_zone=dest,
        )
        prior_zone = zone_of(barcode) if zone_of else None
        receipt = executor.apply(request, at_time=now(), prior_zone=prior_zone)
        if on_receipt is not None:
            on_receipt(receipt, prior_zone)
        return json.dumps(
            {
                "action_id": receipt.action_id,
                "status": receipt.status,
                "reason": receipt.reason,
                "effect_applied": receipt.effect_applied,
                "failure_mode": receipt.failure_mode,
            },
            ensure_ascii=False,
        )

    return spec, run
