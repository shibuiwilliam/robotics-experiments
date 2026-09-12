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

        # 棄却候補には「有限コストの中で最悪のものより明確に悪い」値を割り当てる。
        # 単純に `max * 10 + 1` にすると、cost_fn が負の値を返す場合（例：報酬形の
        # コスト）に符号が反転し、棄却候補の方がむしろ魅力的になってしまう
        # （tests/unit/test_wm_planner.py で確認済みの実バグ）。有限コストの
        # 「広がり」に対する相対マージンを使うことで符号に依存しない。
        finite_costs = costs[finite]
        spread = float((finite_costs.max() - finite_costs.min()).clamp_min(1.0).item())
        fill_value = float(finite_costs.max().item()) + spread * 10.0 + 1.0
        costs = torch.where(finite, costs, torch.full_like(costs, fill_value))
        weights = torch.softmax(-costs / cfg.temperature, dim=0)  # [N]
        best_actions = torch.einsum("n,nha->ha", weights, candidate_actions)

        if self.shield is not None:
            # 安全網：受理された候補の重み付き平均（凸結合）は、Dynamics/Probe が
            # 非線形であるため、平均自体がシールドを再度満たす保証がない
            # （個々の受理候補は違反しなくても、その線形結合が違反する経路上に
            # 乗ってしまう場合がある）。EXP-06 の smoke 実行で実際に観測された
            # （docs/status.md 参照）。平均後の行動を再検査し、違反していれば
            # 単一候補（受理済みの中で最小コスト）にフォールバックすることで、
            # 「シールドを通った行動しか返さない」という契約を厳密に守る。
            blended_valid = self.shield(best_actions.unsqueeze(0))[0]
            if not bool(blended_valid.item()):
                inf_costs = torch.full_like(costs, float("inf"))
                accepted_costs = torch.where(valid_mask, costs, inf_costs)
                best_idx = int(torch.argmin(accepted_costs).item())
                if torch.isfinite(accepted_costs[best_idx]):
                    best_actions = candidate_actions[best_idx]

        return best_actions


__all__ = ["MPPIPlanner", "MPPIConfig", "ShieldFn"]
