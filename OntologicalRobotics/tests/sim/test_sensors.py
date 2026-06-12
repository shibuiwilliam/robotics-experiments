"""合成検出器とベンダースキーマAエミッタのテスト。"""

from pathlib import Path

import pytest

from orx.common.config import DegradationConfig, WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.seeding import SeedTree
from orx.sim.sensors import observe, sense
from orx.sim.world import SimWorld

WORLD_PATH = repo_root() / "configs" / "world" / "demo_tiny.yaml"


@pytest.fixture(scope="module")
def settled_world() -> SimWorld:
    config = load_config(Path(WORLD_PATH), WorldConfig)
    world = SimWorld(config, SeedTree(7))
    world.step_to(config.settle_s)
    return world


def test_sense_detects_boxes_no_noise(settled_world: SimWorld) -> None:
    robot = settled_world.config.robots[0]
    rng = SeedTree(7).child("sense").rng()
    sensed = sense(settled_world, robot, DegradationConfig(), rng)
    assert len(sensed) >= 6  # 大半の箱が見えるカメラ配置
    truth = {o.object_id: o for o in settled_world.truth().objects}
    for s in sensed:
        t = truth[s.true_object_id]
        # ノイズ0: 検出位置は真値と一致
        assert max(abs(a - b) for a, b in zip(s.position, t.position, strict=True)) < 1e-9
        assert s.barcode == t.barcode  # 読取範囲内なら必ず読める


def test_vendor_a_payload_shape(settled_world: SimWorld) -> None:
    robot = settled_world.config.robots[0]
    rng = SeedTree(7).child("sense").rng()
    obs = observe(settled_world, robot, seq=0, rng=rng)
    assert obs.vendor_schema == "vendor_arm_a"
    assert set(obs.payload) == {"hdr", "dets"}
    assert obs.payload["hdr"]["rid"] == "arm_a"
    assert obs.payload["hdr"]["n"] == len(obs.payload["dets"])
    assert len(obs.oracle_truth_ids) == len(obs.payload["dets"])
    det = obs.payload["dets"][0]
    assert set(det) == {"p", "bc", "cf"}
    assert set(det["p"]) == {"x", "y", "z"}


def test_id_read_failure_knob(settled_world: SimWorld) -> None:
    robot = settled_world.config.robots[0]
    knobs = DegradationConfig(id_read_failure_rate=1.0)
    rng = SeedTree(7).child("sense").rng()
    sensed = sense(settled_world, robot, knobs, rng)
    assert all(s.barcode is None for s in sensed)


def test_occlusion_knob_drops_all(settled_world: SimWorld) -> None:
    robot = settled_world.config.robots[0]
    knobs = DegradationConfig(occlusion_rate=1.0)
    rng = SeedTree(7).child("sense").rng()
    assert sense(settled_world, robot, knobs, rng) == []


def test_contradiction_knob_stale_cache_ghosts() -> None:
    """contradiction: 視界から消えた既知箱が初期位置＋バーコードで再送出される。"""
    config = load_config(repo_root() / "configs" / "world" / "t2_warehouse.yaml", WorldConfig)
    world = SimWorld(config, SeedTree(7))
    world.step_to(12.0)  # b2 (BC-102) は handoff へ搬送済み = A視界外
    robot = config.robots[0]  # arm_a
    knobs = DegradationConfig(contradiction_rate=1.0)
    rng = SeedTree(7).child("sense").rng()
    sensed = sense(world, robot, knobs, rng)
    ghosts = [s for s in sensed if s.confidence == 0.60]
    assert any(g.true_object_id == "b2" for g in ghosts), [g.true_object_id for g in ghosts]
    g = next(g for g in ghosts if g.true_object_id == "b2")
    assert g.barcode == "BC-102"
    initial = world.initial_position("b2")
    assert max(abs(a - b) for a, b in zip(g.position, initial, strict=True)) < 1e-9


def test_unknown_vendor_schema_rejected(settled_world: SimWorld) -> None:
    robot = settled_world.config.robots[0].model_copy(update={"vendor_schema": "nope"})
    rng = SeedTree(7).child("sense").rng()
    with pytest.raises(ValueError, match="未知のベンダースキーマ"):
        observe(settled_world, robot, seq=0, rng=rng)
