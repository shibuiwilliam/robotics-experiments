"""合成系列（等速直線運動、静止＋遮蔽）での動態モデルの数値テスト。

閾値は configs/wm/base.yaml の `synthetic_eval.max_latent_cosine_distance` から読む
（world_model.md 「ε の距離...コードに数値を書かない」の精神に合わせ、テストにも
ハードコードしない）。
"""

from __future__ import annotations

import pytest
import torch

from gtwm.utils.config import load_config
from gtwm.utils.paths import repo_root
from gtwm.wm.dynamics import Dynamics

pytestmark = pytest.mark.unit


def _threshold() -> float:
    cfg = load_config(repo_root() / "configs" / "wm" / "base.yaml")
    return float(cfg.synthetic_eval.max_latent_cosine_distance)


def _cosine_distance(pred: torch.Tensor, target: torch.Tensor) -> float:
    cos = torch.nn.functional.cosine_similarity(pred, target, dim=-1)
    return float((1 - cos).mean().item())


def _fit_and_eval(
    target_fn: callable[[int], torch.Tensor],
    horizon: int,
    steps: int = 120,
    occluded_steps: set[int] | None = None,
) -> float:
    """target_fn(h) は真の h ステップ先のスロット [K,D] を返す（h=0 が初期状態）。

    `occluded_steps`（1始まりのホライズン添字）を渡すと、その添字は教師信号から除外する
    （観測が遮蔽で欠落しているステップを模擬する）。それでも最終ステップの予測精度が
    閾値を満たすことを検査する（静止系列なら遮蔽区間があっても外挿が容易なため）。
    """
    torch.manual_seed(0)
    d = 6
    dyn = Dynamics(
        slot_dim=d, d_model=16, n_layers=1, n_heads=2, cond_dim=1, action_dim=1, ensemble_size=1
    )
    optimizer = torch.optim.Adam(dyn.parameters(), lr=1e-2)

    occluded = occluded_steps or set()
    slots_0 = target_fn(0).unsqueeze(0)  # [1,K,D]
    targets = torch.stack([target_fn(h) for h in range(1, horizon + 1)], dim=0).unsqueeze(
        0
    )  # [1,H,K,D]
    supervised_mask = torch.tensor([h not in occluded for h in range(1, horizon + 1)])

    for _ in range(steps):
        rollout = dyn.rollout(slots_0, None, None, horizon)  # [E=1,1,H,K,D]
        pred = rollout[0]
        loss = ((pred[:, supervised_mask] - targets[:, supervised_mask]) ** 2).mean()
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    with torch.no_grad():
        final_pred = dyn.rollout(slots_0, None, None, horizon)[0, 0, -1]  # [K,D]
    return _cosine_distance(final_pred, target_fn(horizon))


def test_constant_velocity_prediction_error_below_threshold() -> None:
    torch.manual_seed(1)
    k, d = 3, 6
    z0 = torch.randn(k, d)
    velocity = torch.randn(k, d) * 0.1

    def target_fn(h: int) -> torch.Tensor:
        return z0 + h * velocity

    error = _fit_and_eval(target_fn, horizon=10)
    assert error < _threshold(), f"constant-velocity 10-step cosine distance {error} >= threshold"


def test_static_with_occlusion_prediction_error_below_threshold() -> None:
    """静止＋遮蔽：一部スロットの観測が欠けても真値は静止し続けるという想定を検査する。

    遮蔽は「学習時にそのスロットの観測が一部ステップで得られない」ことに相当するが、
    ここでは動態モデル単体の数値検証として、真値系列が静止であることを、
    観測欠損を模した中間ステップ抜き（教師信号なし）で学習しても再現できるかを見る。
    """
    torch.manual_seed(2)
    k, d = 3, 6
    z0 = torch.randn(k, d)

    def target_fn(h: int) -> torch.Tensor:
        return z0  # 静止

    error = _fit_and_eval(target_fn, horizon=10, occluded_steps={4, 5, 6})
    assert error < _threshold(), f"static+occlusion 10-step cosine distance {error} >= threshold"
