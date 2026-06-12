"""知覚パイプライン（射影・切り出し・埋め込み・イベント化）のテスト。"""

from pathlib import Path

import numpy as np
import pytest

from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.seeding import SeedTree
from orx.perception.embedder import STUB_DIM, StubVisualEmbedder, make_visual_embedder
from orx.perception.lifting import load_mapping
from orx.perception.pipeline import PerceptionPipeline, project_to_pixel
from orx.sim.sensors import observe
from orx.sim.world import SimWorld

WORLD_PATH = repo_root() / "configs" / "world" / "demo_tiny.yaml"


@pytest.fixture(scope="module")
def settled_world() -> SimWorld:
    config = load_config(Path(WORLD_PATH), WorldConfig)
    world = SimWorld(config, SeedTree(7))
    world.step_to(config.settle_s)
    return world


def test_stub_embedder_deterministic_and_similar() -> None:
    embedder = StubVisualEmbedder()
    rng = np.random.default_rng(0)
    crop = rng.integers(0, 255, (48, 48, 3)).astype(np.uint8)
    [v1], [v2] = embedder.embed_crops([crop]), embedder.embed_crops([crop])
    assert v1 == v2
    assert len(v1) == STUB_DIM
    # 微小な画素変化なら高コサイン類似
    noisy = np.clip(crop.astype(int) + rng.integers(-8, 8, crop.shape), 0, 255).astype(np.uint8)
    [v3] = embedder.embed_crops([noisy])
    cos = float(np.dot(v1, v3))
    assert cos > 0.95


def test_projection_in_view(settled_world: SimWorld) -> None:
    image, view = settled_world.render("arm_a")
    truth = settled_world.truth()
    visible_pixels = [project_to_pixel(view, o.position) for o in truth.objects]
    assert any(p is not None for p in visible_pixels)


def test_pipeline_emits_event_with_embeddings(settled_world: SimWorld) -> None:
    robot = settled_world.config.robots[0]
    rng = SeedTree(7).child("sense").rng()
    obs = observe(settled_world, robot, seq=5, rng=rng)
    image, view = settled_world.render(robot.name)
    pipeline = PerceptionPipeline(load_mapping(robot.vendor_schema), StubVisualEmbedder())
    event = pipeline.process(obs, image, view)
    assert event.event_id == "arm_a-000005"
    assert len(event.detections) == len(obs.oracle_truth_ids)
    assert all(d.embedding is not None and len(d.embedding) == STUB_DIM for d in event.detections)
    # 同一観測の再処理は同一イベント（決定性）
    event2 = pipeline.process(obs, image, view)
    assert event == event2


def test_make_visual_embedder_validates() -> None:
    assert isinstance(make_visual_embedder("stub"), StubVisualEmbedder)
    with pytest.raises(ValueError, match="未知の視覚埋め込み器"):
        make_visual_embedder("nope")
