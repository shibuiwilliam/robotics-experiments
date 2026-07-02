"""K0 — アクション型（検証→ステージング→実行→効果→アンドゥ）の単体テスト。

完全オフライン（stub SkillServer/決定的）。真値には触れない（SkillServer が境界）。
"""

from __future__ import annotations

from orx.common.config import (
    CameraConfig,
    RobotConfig,
    SkillTruthProfile,
    WorldConfig,
    ZoneConfig,
)
from orx.common.seeding import SeedTree
from orx.skills.action import (
    ActionExecutor,
    DictEffectSink,
    ValidationResult,
    always_valid,
)
from orx.skills.server import SkillRequest, SkillServer


def _world(base_success: float) -> WorldConfig:
    return WorldConfig(
        name="act-test",
        zones=[
            ZoneConfig(name="bench", center=(0.0, 0.0, 0.4), size=(2.0, 2.0, 0.4)),
            ZoneConfig(name="dock", center=(0.0, 1.0, 0.4), size=(2.0, 2.0, 0.4)),
        ],
        boxes=[
            {
                "name": "x1",
                "zone": "bench",
                "barcode": "BC-X",
                "weight_kg": 0.5,
                "material": "cardboard",
            },
        ],
        robots=[
            RobotConfig(
                name="r1",
                vendor_schema="vendor_arm_a",
                camera=CameraConfig(pos=(0.0, 0.0, 1.0), lookat=(0.0, 0.0, 0.0)),
                skill_truth=SkillTruthProfile(
                    base_success=base_success, reach_m=100.0, max_payload_kg=100.0
                ),
            )
        ],
    )


def _request() -> SkillRequest:
    return SkillRequest(
        robot_id="r1",
        skill="pick_and_place",
        target_barcode="BC-X",
        target_position=(0.0, 0.0, 0.4),
        dest_zone="dock",
    )


def _executor(base_success: float = 1.0, validate=always_valid):
    world = _world(base_success)
    server = SkillServer(world, SeedTree(7).child("skills").rng())
    sink = DictEffectSink({"BC-X": "bench"})
    return ActionExecutor(server, sink, validate=validate), sink


def test_applied_effect_moves_object() -> None:
    ex, sink = _executor(base_success=1.0)
    receipt = ex.apply(_request(), at_time=2.0, prior_zone="bench")
    assert receipt.status == "applied"
    assert receipt.effect_applied is True
    assert sink.zone_of("BC-X") == "dock"
    assert receipt.action_id == "act-0001"


def test_rejected_has_no_side_effect() -> None:
    def reject(_req):
        return ValidationResult(ok=False, reason="forbidden_zone")

    ex, sink = _executor(validate=reject)
    receipt = ex.apply(_request(), at_time=2.0, prior_zone="bench")
    assert receipt.status == "rejected"
    assert receipt.effect_applied is False
    assert receipt.reason == "forbidden_zone"
    assert sink.zone_of("BC-X") == "bench"  # 不正アクションは世界を変えない


def test_staged_escalates_without_execution() -> None:
    def stage(_req):
        return ValidationResult(ok=False, reason="low_margin", stage=True)

    ex, sink = _executor(validate=stage)
    receipt = ex.apply(_request(), at_time=2.0, prior_zone="bench")
    assert receipt.status == "staged"
    assert receipt.effect_applied is False
    assert sink.zone_of("BC-X") == "bench"


def test_failed_execution_applies_no_effect() -> None:
    ex, sink = _executor(base_success=0.0)  # 必ず失敗する擬似VLA
    receipt = ex.apply(_request(), at_time=2.0, prior_zone="bench")
    assert receipt.status == "applied"
    assert receipt.effect_applied is False
    assert receipt.failure_mode is not None
    assert sink.zone_of("BC-X") == "bench"


def test_compensation_reverses_effect() -> None:
    ex, sink = _executor(base_success=1.0)
    receipt = ex.apply(_request(), at_time=2.0, prior_zone="bench")
    assert sink.zone_of("BC-X") == "dock"
    comp = ex.compensate(receipt, prior_zone="bench", at_time=3.0)
    assert comp.compensates == receipt.action_id
    assert comp.effect_applied is True
    assert sink.zone_of("BC-X") == "bench"  # アンドゥで元ゾーンへ戻る


def test_receipt_round_trips_to_records() -> None:
    ex, _ = _executor(base_success=1.0)
    receipt = ex.apply(_request(), at_time=2.0, prior_zone="bench")
    rec = receipt.to_record()
    assert rec.action_id == receipt.action_id
    assert rec.status == "applied"
    req = receipt.request_record()
    assert req.dest_zone == "dock"
    assert req.target_barcode == "BC-X"
