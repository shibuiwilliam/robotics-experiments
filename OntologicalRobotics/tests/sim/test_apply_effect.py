"""K0 — SimWorld.apply_effect が物理真値を変えること＋物理ループ純粋性。

apply_effect は scripted_moves と同一機構で対象箱を別ゾーンへ移す（C1 物理権威）。
不変条件4: apply_effect は tick 境界（step_to の外）でのみ呼ぶ。
"""

from __future__ import annotations

from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.seeding import SeedTree
from orx.sim.world import SimWorld

WORLD = repo_root() / "configs" / "world" / "demo_tiny.yaml"


def _zone_of(world: SimWorld, box: str) -> str | None:
    truth = world.truth()
    obj = next(o for o in truth.objects if o.object_id == box)
    return obj.zone


def test_apply_effect_relocates_box_in_truth() -> None:
    world = SimWorld(load_config(WORLD, WorldConfig), SeedTree(7).child("sim"))
    world.step_to(world.config.settle_s)
    assert _zone_of(world, "b1") == "shelf_a"  # 初期配置

    world.apply_effect("b1", "dock", at_time=2.0)
    assert _zone_of(world, "b1") == "dock"  # アクション効果が真値に反映
    world.close()


def test_apply_effect_is_idempotent_under_further_steps() -> None:
    """効果適用後に物理を進めても箱は目的ゾーンに留まる（teleport は安定）。"""
    world = SimWorld(load_config(WORLD, WorldConfig), SeedTree(7).child("sim"))
    world.step_to(world.config.settle_s)
    world.apply_effect("b2", "dock", at_time=2.0)
    world.step_to(world.config.settle_s + 2.0)
    assert _zone_of(world, "b2") == "dock"
    world.close()


def test_apply_effect_rejects_unknown_box() -> None:
    world = SimWorld(load_config(WORLD, WorldConfig), SeedTree(7).child("sim"))
    world.step_to(world.config.settle_s)
    try:
        world.apply_effect("nope", "dock", at_time=2.0)
        raise AssertionError("未知の箱で ValueError を期待")
    except ValueError:
        pass
    finally:
        world.close()


def test_sim_effect_sink_drives_real_physics() -> None:
    """SimEffectSink（C1 物理権威の EffectSink）がアクション効果を真値に反映する。"""
    from orx.skills.action import SimEffectSink

    world = SimWorld(load_config(WORLD, WorldConfig), SeedTree(7).child("sim"))
    world.step_to(world.config.settle_s)
    sink = SimEffectSink(world, barcode_to_box={"BC-001": "b1"})
    assert sink.apply("BC-001", "dock", at_time=2.0) is True
    assert _zone_of(world, "b1") == "dock"
    assert sink.apply("BC-UNKNOWN", "dock", at_time=2.0) is False  # 未知対象は無効
    world.close()
