"""潜在動態モデル：Transformer 4層、d=256、[スロット列, 条件トークンγ, 行動トークン]。
アンサンブル3本で不確実性を出す。

契約（world_model.md）：
Dynamics.rollout(slots_t: [B,K,D], cond, actions: [B,h,Da] | None, h) -> [B,h,K,D]
（アンサンブル次元は別引数で返す＝出力は [E,B,h,K,D]）

Conditioner（γ：KGSubgraph -> cond[B,Dc]）は接地層（session 05、grounding/conditioning.py）
の責務。ここでは `cond: Tensor[B,Dc] | None` を受け取るだけで、None の場合はゼロ条件
（無条件ロールアウト）として扱う。

行動空間（actions: [B,h,Da]）の各次元の意味付け（例：「次元0はコンベア速度」）は
このモジュールの契約外で、`kg/whatif/compiler.py` の `ACTION_CHANNELS` が唯一の
定義元である（変更する場合は両方を同時に更新する）。現状 action_dim=4
（`configs/wm/*.yaml`）のうち3チャネルが割当済み（speed/active/staging_offset）、
1チャネルは将来の介入種別のため未使用のまま予約されている
（`ACTION_DIM_RESERVED`）。

**重要な既知の制約**：`wm/train.py` の学習ループは `dynamics.rollout(context, None,
None, horizon)` を常に `actions=None` で呼び出しており、`action_in`（下記）は
学習中に一度も非ゼロの行動勾配を受け取らない。そのため現時点では
`action_in` の重みはランダム初期化のまま学習されておらず、`do()` 介入が
ロールアウト出力に及ぼす差は「学習された意味的効果」ではなく「未学習の線形層に
よる入力の増幅」でしかない（実測：action値を0→50に振ると出力ノルムは変化するが、
現実的な介入スケール（例：`1.8 * current` で 1.8）では基準との差はノイズに埋もれる
——EXP-07 の `kpi_relative_error`/`interval_coverage_90` が smoke で悪い値になる
一因）。WHAT-IF の `do()` に真の予測的な効果を持たせるには、行動条件付きの
訓練データ（実際に行動を変えた場合の結果とペアになったエピソード）を生成し、
`wm/train.py` のロールアウト損失に非ゼロの `actions` を渡すよう学習ループを
拡張する必要がある（本セッションのスコープ外、docs/status.md に申し送り）。
"""

from __future__ import annotations

from typing import cast

import torch
from torch import Tensor, nn


class DynamicsHead(nn.Module):
    """単一のアンサンブルメンバー。スロット列＋条件トークン＋行動トークンを
    Transformer エンコーダで処理し、次スロット状態を予測する。
    """

    def __init__(
        self,
        slot_dim: int,
        d_model: int,
        n_layers: int,
        n_heads: int,
        cond_dim: int,
        action_dim: int,
    ):
        super().__init__()
        self.slot_dim = slot_dim
        self.d_model = d_model

        self.slot_in = nn.Linear(slot_dim, d_model)
        self.cond_in = nn.Linear(cond_dim, d_model)
        self.action_in = nn.Linear(action_dim, d_model)
        self.slot_out = nn.Linear(d_model, slot_dim)

        # dropout=0.0：MPS の scaled_dot_product_attention は dropout>0 を未サポート
        # （`NotImplementedError: scaled_dot_product_attention for MPS does not
        # support dropout` を train/no_grad 双方の経路で確認済み）。CLAUDE.md の
        # 絶対条件（MPS のみ）を満たすため、この動態モデルでは dropout を使わない。
        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 2,
            dropout=0.0,
            batch_first=True,
        )
        self.transformer = nn.TransformerEncoder(layer, num_layers=n_layers)

    def step(self, slots: Tensor, cond: Tensor, action: Tensor | None) -> Tensor:
        """1ステップぶんの遷移。

        slots:[B,K,D] cond:[B,Dc] action:[B,Da]|None -> next slots:[B,K,D]。
        """
        b, k, _ = slots.shape
        tokens = [self.slot_in(slots)]  # [B,K,d_model]
        tokens.append(self.cond_in(cond).unsqueeze(1))  # [B,1,d_model]
        if action is not None:
            tokens.append(self.action_in(action).unsqueeze(1))  # [B,1,d_model]
        seq = torch.cat(tokens, dim=1)
        out = self.transformer(seq)
        next_slots = self.slot_out(out[:, :k, :])
        return slots + next_slots  # 残差更新

    def rollout(self, slots_t: Tensor, cond: Tensor, actions: Tensor | None, h: int) -> Tensor:
        """slots_t:[B,K,D] -> [B,h,K,D]。actions:[B,h,Da] があれば各ステップで使う。"""
        outputs = []
        slots = slots_t
        for step in range(h):
            action = actions[:, step, :] if actions is not None else None
            slots = self.step(slots, cond, action)
            outputs.append(slots)
        return torch.stack(outputs, dim=1)


class Dynamics(nn.Module):
    """アンサンブル（既定3本）の DynamicsHead をまとめる。"""

    def __init__(
        self,
        slot_dim: int,
        d_model: int = 256,
        n_layers: int = 4,
        n_heads: int = 8,
        cond_dim: int = 64,
        action_dim: int = 4,
        ensemble_size: int = 3,
    ):
        super().__init__()
        self.ensemble_size = ensemble_size
        self.cond_dim = cond_dim
        self.members = nn.ModuleList(
            [
                DynamicsHead(slot_dim, d_model, n_layers, n_heads, cond_dim, action_dim)
                for _ in range(ensemble_size)
            ]
        )

    def rollout(
        self, slots_t: Tensor, cond: Tensor | None, actions: Tensor | None, h: int
    ) -> Tensor:
        """契約シグネチャ通り。cond が None ならゼロ条件を使う。戻り値 [E,B,h,K,D]。"""
        b = slots_t.shape[0]
        if cond is None:
            cond = torch.zeros(b, self.cond_dim, dtype=slots_t.dtype, device=slots_t.device)
        rollouts = [
            cast(DynamicsHead, member).rollout(slots_t, cond, actions, h) for member in self.members
        ]
        return torch.stack(rollouts, dim=0)

    def forward(
        self, slots_t: Tensor, cond: Tensor | None, actions: Tensor | None, h: int
    ) -> Tensor:
        return self.rollout(slots_t, cond, actions, h)


__all__ = ["Dynamics", "DynamicsHead"]
