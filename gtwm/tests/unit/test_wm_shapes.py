"""世界モデル各モジュールの形状テスト（CPU・極小次元、ネットワーク不要）。"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from gtwm.utils.paths import warehouse_xml_path
from gtwm.wm.dynamics import Dynamics
from gtwm.wm.encoder import Encoder, build_tiny_test_backbone
from gtwm.wm.fusion import CameraParams, FusionModule, load_camera_params
from gtwm.wm.planner import MPPIConfig, MPPIPlanner
from gtwm.wm.slots import SlotModule

pytestmark = pytest.mark.unit


def test_encoder_shapes() -> None:
    backbone = build_tiny_test_backbone(hidden_size=16, image_size=32, patch_size=8)
    encoder = Encoder(backbone=backbone, hidden_size=16, adapter_dim=8, freeze=True)
    frames = torch.rand(2, 3, 2, 3, 20, 20)
    tokens = encoder.encode(frames)
    assert tokens.shape[:3] == (2, 3, 2)
    assert tokens.shape[-1] == 8


def test_encoder_frozen_backbone_has_no_grad() -> None:
    backbone = build_tiny_test_backbone(hidden_size=16, image_size=32, patch_size=8)
    encoder = Encoder(backbone=backbone, hidden_size=16, adapter_dim=8, freeze=True)
    assert all(not p.requires_grad for p in encoder.backbone.parameters())
    assert all(p.requires_grad for p in encoder.adapter.parameters())


def test_slot_module_shapes() -> None:
    sm = SlotModule(input_dim=8, n_slots=4, slot_dim=16, n_iters=2, n_types=6)
    tokens = torch.rand(2, 3, 5, 8)
    slots, type_logits = sm(tokens)
    assert slots.shape == (2, 3, 4, 16)
    assert type_logits.shape == (2, 3, 4, 6)


def test_fusion_shapes_with_synthetic_cameras() -> None:
    cams = {
        "cam:a": CameraParams(
            name="cam:a",
            pos=torch.tensor([0.0, 0.0, 3.0]).numpy(),
            rot=_identity3(),
            fovy_deg=60.0,
            width=32,
            height=32,
        ),
        "cam:b": CameraParams(
            name="cam:b",
            pos=torch.tensor([1.0, 0.0, 3.0]).numpy(),
            rot=_identity3(),
            fovy_deg=60.0,
            width=32,
            height=32,
        ),
    }
    fm = FusionModule(n_slots=4, slot_dim=8, n_heads=2)
    b, t, k, d = 2, 3, 4, 8
    slots_per_cam = {name: torch.rand(b, t, k, d) for name in cams}
    fused, floor_positions = fm(slots_per_cam, cams)
    assert fused.shape == (b, t, k, d)
    for name in cams:
        assert floor_positions[name].shape == (b, t, k, 3)


def test_load_camera_params_from_real_mjcf() -> None:
    cams = load_camera_params(warehouse_xml_path(), width=64, height=64)
    assert set(cams.keys()) == {"cam:1", "cam:2", "cam:3", "cam:4"}
    for cam in cams.values():
        assert cam.rot.shape == (3, 3)
        assert cam.pos.shape == (3,)
        assert cam.fovy_deg > 0


def test_dynamics_rollout_shapes() -> None:
    dyn = Dynamics(
        slot_dim=8, d_model=16, n_layers=1, n_heads=2, cond_dim=4, action_dim=3, ensemble_size=2
    )
    slots_t = torch.rand(2, 4, 8)
    cond = torch.rand(2, 4)
    actions = torch.rand(2, 5, 3)
    out = dyn.rollout(slots_t, cond, actions, h=5)
    assert out.shape == (2, 2, 5, 4, 8)

    out_no_cond = dyn.rollout(slots_t, None, None, h=3)
    assert out_no_cond.shape == (2, 2, 3, 4, 8)


def test_planner_shield_rejects_all_returns_zero_action() -> None:
    dyn = Dynamics(
        slot_dim=8, d_model=16, n_layers=1, n_heads=2, cond_dim=4, action_dim=3, ensemble_size=2
    )
    cfg = MPPIConfig(n_samples=16, horizon=4, action_dim=3)

    def cost_fn(rollout: torch.Tensor) -> torch.Tensor:
        return rollout.pow(2).mean(dim=(1, 2, 3))

    def shield_reject_all(actions: torch.Tensor) -> torch.Tensor:
        return torch.zeros(actions.shape[0], dtype=torch.bool)

    planner = MPPIPlanner(dyn, cfg, shield=shield_reject_all)
    slots_t = torch.rand(1, 4, 8)
    actions = planner.plan(slots_t, None, cost_fn)
    assert actions.shape == (4, 3)
    assert torch.allclose(actions, torch.zeros_like(actions))


def _identity3() -> np.ndarray:
    return np.eye(3)
