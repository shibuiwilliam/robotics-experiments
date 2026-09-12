"""MPPI 計画の骨格。サンプル256、ホライズン20（既定）。出力は助言のみ。

SHACL シールド（候補軌道を α で記号化し違反候補を除外）は session 05 の接続対象。
ここでは `shield` フックだけを用意する（`Callable[[Tensor actions], Tensor mask]`。
mask[n]=False の候補はコスト無限大として扱われる）。session 05 は
`grounding/constraints.py` 側でこのフックに適合する呼び出し可能オブジェクトを渡す。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import torch
from torch import Tensor

from gtwm.wm.dynamics import Dynamics

ShieldFn = Callable[[Tensor], Tensor]


@dataclass
class MPPIConfig:
    n_samples: int = 256
    horizon: int = 20
    action_dim: int = 4
    temperature: float = 1.0
    noise_std: float = 1.0


class MPPIPlanner:
    """Model Predictive Path Integral 計画器の骨格。

    `cost_fn` はロールアウトされたスロット系列 [B,h,K,D] からコスト [B] を返す
    呼び出し可能オブジェクト（KPI・目的関数は WHAT-IF/eval 側が注入する）。
    """

    def __init__(self, dynamics: Dynamics, config: MPPIConfig, shield: ShieldFn | None = None):
        self.dynamics = dynamics
        self.config = config
        self.shield = shield

    def plan(
        self,
        slots_t: Tensor,
        cond: Tensor | None,
        cost_fn: Callable[[Tensor], Tensor],
        nominal_actions: Tensor | None = None,
    ) -> Tensor:
        """slots_t: [1,K,D]（単一状態を想定） -> 最良候補の行動系列 [horizon, action_dim]。

        候補行動をガウスノイズでサンプルし、ロールアウトのコストで重み付き平均する
        （標準的な MPPI 更新）。シールドが設定されていれば違反候補のコストを +inf にする。
        """
        cfg = self.config
        device = slots_t.device
        dtype = slots_t.dtype

        if nominal_actions is None:
            nominal_actions = torch.zeros(cfg.horizon, cfg.action_dim, dtype=dtype, device=device)

        noise = (
            torch.randn(cfg.n_samples, cfg.horizon, cfg.action_dim, dtype=dtype, device=device)
            * cfg.noise_std
        )
        candidate_actions = nominal_actions.unsqueeze(0) + noise  # [N,h,Da]

        slots_batch = slots_t.expand(cfg.n_samples, -1, -1)
        cond_batch = None if cond is None else cond.expand(cfg.n_samples, -1)

        rollouts = self.dynamics.rollout(
            slots_batch, cond_batch, candidate_actions, cfg.horizon
        )  # [E,N,h,K,D]
        mean_rollout = rollouts.mean(dim=0)  # アンサンブル平均 [N,h,K,D]

        costs = cost_fn(mean_rollout)  # [N]

        if self.shield is not None:
            valid_mask = self.shield(candidate_actions)  # [N] bool
            costs = torch.where(valid_mask, costs, torch.full_like(costs, float("inf")))

        finite = torch.isfinite(costs)
        if not finite.any():
            # 全候補がシールドで棄却された場合は無介入（ゼロ行動）を返す。
            return torch.zeros(cfg.horizon, cfg.action_dim, dtype=dtype, device=device)

        fill_value = float(costs[finite].max().item()) * 10.0 + 1.0
        costs = torch.where(finite, costs, torch.full_like(costs, fill_value))
        weights = torch.softmax(-costs / cfg.temperature, dim=0)  # [N]
        best_actions = torch.einsum("n,nha->ha", weights, candidate_actions)
        return best_actions


__all__ = ["MPPIPlanner", "MPPIConfig", "ShieldFn"]
