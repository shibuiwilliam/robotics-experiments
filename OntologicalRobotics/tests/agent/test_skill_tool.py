"""K1 — act ツール（アクション発行）の単体テスト（stub・決定的）。"""

from __future__ import annotations

import json

from orx.agent.tools.skill_tool import act_tool
from orx.common.config import (
    CameraConfig,
    RobotConfig,
    SkillTruthProfile,
    WorldConfig,
    ZoneConfig,
)
from orx.common.seeding import SeedTree
from orx.skills.action import ActionExecutor, DictEffectSink, ValidationResult
from orx.skills.server import SkillServer


def _world() -> WorldConfig:
    return WorldConfig(
        name="act-tool-test",
        zones=[
            ZoneConfig(name="bench", center=(0.0, 0.0, 0.4), size=(2.0, 2.0, 0.4)),
            ZoneConfig(name="dock", center=(0.0, 1.0, 0.4), size=(2.0, 2.0, 0.4)),
        ],
        boxes=[{"name": "x1", "zone": "bench", "barcode": "BC-X", "weight_kg": 0.5}],
        robots=[
            RobotConfig(
                name="r1",
                vendor_schema="vendor_arm_a",
                camera=CameraConfig(pos=(0.0, 0.0, 1.0), lookat=(0.0, 0.0, 0.0)),
                skill_truth=SkillTruthProfile(
                    base_success=1.0, reach_m=100.0, max_payload_kg=100.0
                ),
            )
        ],
    )


def _setup(validate):
    server = SkillServer(_world(), SeedTree(7).child("skills").rng())
    sink = DictEffectSink({"BC-X": "bench"})
    ex = ActionExecutor(server, sink, validate=validate)
    received: list = []
    spec, run = act_tool(
        ex,
        position_of=lambda bc: (0.0, 0.0, 0.4) if bc == "BC-X" else None,
        now=lambda: 2.0,
        zone_of=lambda bc: sink.zone_of(bc),
        on_receipt=lambda r, prior: received.append((r.status, prior)),
    )
    return run, sink, received


def test_act_tool_applies_when_valid() -> None:
    run, sink, received = _setup(lambda _r: ValidationResult(ok=True))
    out = json.loads(run({"robot_id": "r1", "target_barcode": "BC-X", "dest_zone": "dock"}))
    assert out["status"] == "applied"
    assert out["effect_applied"] is True
    assert sink.zone_of("BC-X") == "dock"
    assert received == [("applied", "bench")]


def test_act_tool_rejects_with_reason() -> None:
    run, sink, _ = _setup(
        lambda _r: ValidationResult(ok=False, reason="reagent_zeta は atrium を通れない")
    )
    out = json.loads(run({"robot_id": "r1", "target_barcode": "BC-X", "dest_zone": "atrium"}))
    assert out["status"] == "rejected"
    assert "atrium" in out["reason"]
    assert sink.zone_of("BC-X") == "bench"  # 世界は変わらない


def test_act_tool_rejects_unknown_target() -> None:
    run, _, _ = _setup(lambda _r: ValidationResult(ok=True))
    out = json.loads(run({"robot_id": "r1", "target_barcode": "BC-ZZZ", "dest_zone": "dock"}))
    assert out["status"] == "rejected"
    assert "位置" in out["reason"]
