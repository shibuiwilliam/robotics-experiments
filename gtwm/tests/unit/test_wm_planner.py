"""`MPPIPlanner.plan()` のシールド安全網テスト（EXP-06 で実際に観測されたバグの回帰）。

受理候補の重み付き平均（凸結合）は、Dynamics/Probe が非線形であるため、平均自体が
シールドを満たす保証がない。`plan()` は平均後の行動を再検査し、違反していれば
単一の最小コスト受理候補にフォールバックしなければならない。
"""

from __future__ import annotations

import pytest
import torch
from torch import Tensor

from gtwm.wm.planner import MPPIConfig, MPPIPlanner

pytestmark = pytest.mark.unit


class _IdentityDynamics:
    """テスト専用の偽 Dynamics：rollout は「行動そのもの」をスロット次元に埋め込んで
    返す（実際のニューラルネットの代わりに、シールド安全網ロジックだけを決定的に検証
    するため）。"""

    def rollout(self, slots_t: Tensor, cond: Tensor | None, actions: Tensor, h: int) -> Tensor:
        n = actions.shape[0]
        k, d = slots_t.shape[-2], slots_t.shape[-1]
        # actions[:, :, 0] の値をそのまま最初のスロット次元に書き込むだけの「恒等」モデル。
        out = torch.zeros(1, n, h, k, d)
        out[0, :, :, 0, 0] = actions[:, :, 0]
        return out


def test_shield_safety_net_falls_back_when_blended_action_violates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """許容領域が非凸（2つの離れた島：[8,12] と [-12,-8]）な場合、島の中の候補を
    2つ混ぜても平均は島の外（この例では0近辺）に落ちうる —— これが実際に
    EXP-06 smoke 実行で観測された失敗様式の最小再現（Probe.zone_probs は行動に対して
    非線形なので、受理候補どうしの凸結合が受理領域内に留まる保証がない）。

    候補は全て個別にシールドを満たす（valid_mask が全て True）ため、この失敗は
    「棄却コストの割り当て方」とは無関係で、安全網（平均後の再検査＋フォールバック）
    でしか防げないことを確認する。
    """
    # torch.randn を差し替え、候補ノイズを厳密に [+10, +10, -10, -10] に固定する
    # （実際の乱数任せだと 4 候補中どれが argmin になるか等が偶然に左右されるため、
    # 決定的に作る）。
    fixed_noise = torch.tensor([10.0, 10.0, -10.0, -10.0]).reshape(4, 1, 1)
    monkeypatch.setattr(torch, "randn", lambda *args, **kwargs: fixed_noise.clone())

    cfg = MPPIConfig(n_samples=4, horizon=1, action_dim=1, temperature=1.0, noise_std=1.0)
    dynamics = _IdentityDynamics()
    planner = MPPIPlanner(dynamics, cfg, shield=None)  # shieldは後で個別に差し替える

    def shield_fn(candidate_actions: Tensor) -> Tensor:
        value = candidate_actions[:, :, 0].mean(dim=-1)
        return (value.abs() >= 8.0) & (value.abs() <= 12.0)  # [8,12] ∪ [-12,-8] のみ許容

    def cost_fn(mean_rollout: Tensor) -> Tensor:
        value = mean_rollout[:, 0, 0, 0]
        return -value.abs()  # 絶対値が大きいほど低コスト（符号は問わない）

    planner.shield = shield_fn
    nominal = torch.zeros(cfg.horizon, cfg.action_dim)

    # 全4候補が個別にはシールドを満たすことを確認（この失敗様式の前提）。
    assert bool(shield_fn(fixed_noise)[0]) and bool(shield_fn(fixed_noise)[1])
    assert bool(shield_fn(fixed_noise)[2]) and bool(shield_fn(fixed_noise)[3])

    result = planner.plan(torch.zeros(1, 3, 4), None, cost_fn, nominal_actions=nominal)

    # 安全網が機能していれば、返された行動はシールドを実際に満たしている
    # （満たさないまま返すのはシールドの契約違反）。単純な加重平均（コスト同点なので
    # 一様重み）は (10+10-10-10)/4=0 となり、これは許容領域の外なので、フォール
    # バックが発動していなければこのテストは失敗する。
    final_valid = shield_fn(result.unsqueeze(0))[0]
    assert bool(final_valid.item()), f"シールドが要求する条件を満たさない行動が返された: {result}"
    # フォールバックは実在する受理候補（value=+10、コスト同点のうち最初の候補）
    # そのものであるべき（でっち上げの値ではない）。
    assert float(result[0, 0].item()) == pytest.approx(10.0)
