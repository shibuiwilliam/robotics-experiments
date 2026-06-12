"""C1 sim の単体テスト: 決定的ロード・真値・スクリプト遷移・描画。"""

from pathlib import Path

import numpy as np
import pytest

from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.seeding import SeedTree
from orx.sim.world import SimWorld

WORLD_PATH = repo_root() / "configs" / "world" / "demo_tiny.yaml"


@pytest.fixture(scope="module")
def world_config() -> WorldConfig:
    return load_config(Path(WORLD_PATH), WorldConfig)


def test_world_loads_and_settles(world_config: WorldConfig) -> None:
    world = SimWorld(world_config, SeedTree(7))
    world.step_to(world_config.settle_s)
    truth = world.truth()
    assert len(truth.objects) == 8
    # 静定後、全箱が初期ゾーンに居る
    zones = {o.object_id: o.zone for o in truth.objects}
    assert zones["b1"] == "shelf_a"
    assert zones["b6"] == "dock"


def test_deterministic_given_same_seed(world_config: WorldConfig) -> None:
    w1 = SimWorld(world_config, SeedTree(7))
    w2 = SimWorld(world_config, SeedTree(7))
    w1.step_to(2.0)
    w2.step_to(2.0)
    p1 = {o.object_id: o.position for o in w1.truth().objects}
    p2 = {o.object_id: o.position for o in w2.truth().objects}
    assert p1 == p2


def test_different_seed_different_placement(world_config: WorldConfig) -> None:
    w1 = SimWorld(world_config, SeedTree(7))
    w2 = SimWorld(world_config, SeedTree(8))
    p1 = {o.object_id: o.position for o in w1.truth().objects}
    p2 = {o.object_id: o.position for o in w2.truth().objects}
    assert p1 != p2


def test_scripted_move_changes_zone(world_config: WorldConfig) -> None:
    world = SimWorld(world_config, SeedTree(7))
    world.step_to(7.5)
    assert next(o for o in world.truth().objects if o.object_id == "b3").zone == "shelf_a"
    world.step_to(9.5)
    assert next(o for o in world.truth().objects if o.object_id == "b3").zone == "dock"


def test_offscreen_render_224(world_config: WorldConfig) -> None:
    world = SimWorld(world_config, SeedTree(7))
    world.step_to(world_config.settle_s)
    image, view = world.render("arm_a")
    assert image.shape == (224, 224, 3)
    assert image.max() > 0  # 真っ黒でない
    assert np.allclose(view.xmat @ view.xmat.T, np.eye(3), atol=1e-6)
    world.close()
