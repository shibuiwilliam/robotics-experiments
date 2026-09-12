"""Slot Attention とオントロジー型付きスロットの型ヘッド。

契約（world_model.md）：SlotModule(tokens) -> slots [B,T,K,D], type_logits [B,T,K,6]。
tokens は1カメラぶんの [B,T,P,De]（複数カメラは呼び出し側で C を疑似バッチとして
ループ・reshape する。マルチカメラ統合は wm/fusion.py が担当する）。

型6クラス：pallet, case, agv, worker, equipment, none（オントロジーの継続体クラスに対応）。
"""

from __future__ import annotations

import torch
from torch import Tensor, nn

SLOT_TYPES = ["pallet", "case", "agv", "worker", "equipment", "none"]


class SlotAttention(nn.Module):
    """Locatello et al. 2020 のスロットアテンション。反復的なスロット更新で
    パッチトークン集合をK個のオブジェクト中心スロットに束ねる。
    """

    def __init__(
        self, n_slots: int, slot_dim: int, input_dim: int, n_iters: int = 3, eps: float = 1e-8
    ):
        super().__init__()
        self.n_slots = n_slots
        self.slot_dim = slot_dim
        self.n_iters = n_iters
        self.eps = eps
        self.scale = slot_dim**-0.5

        self.slots_mu = nn.Parameter(torch.randn(1, 1, slot_dim))
        self.slots_log_sigma = nn.Parameter(torch.zeros(1, 1, slot_dim))

        self.norm_input = nn.LayerNorm(input_dim)
        self.norm_slots = nn.LayerNorm(slot_dim)
        self.norm_pre_ff = nn.LayerNorm(slot_dim)

        self.to_q = nn.Linear(slot_dim, slot_dim, bias=False)
        self.to_k = nn.Linear(input_dim, slot_dim, bias=False)
        self.to_v = nn.Linear(input_dim, slot_dim, bias=False)

        self.gru = nn.GRUCell(slot_dim, slot_dim)
        self.mlp = nn.Sequential(
            nn.Linear(slot_dim, slot_dim * 2), nn.ReLU(), nn.Linear(slot_dim * 2, slot_dim)
        )

    def forward(self, inputs: Tensor) -> Tensor:
        """inputs: [N,P,Din] (N=有効バッチ、Pはトークン数) -> slots [N,K,D]。"""
        n = inputs.shape[0]
        inputs = self.norm_input(inputs)
        k = self.to_k(inputs)
        v = self.to_v(inputs)

        mu = self.slots_mu.expand(n, self.n_slots, -1)
        sigma = self.slots_log_sigma.exp().expand(n, self.n_slots, -1)
        slots = mu + sigma * torch.randn_like(mu)

        for _ in range(self.n_iters):
            slots_prev = slots
            slots_n = self.norm_slots(slots)
            q = self.to_q(slots_n)

            attn_logits = torch.einsum("nkd,npd->nkp", q, k) * self.scale
            attn = attn_logits.softmax(dim=1) + self.eps
            attn = attn / attn.sum(dim=-1, keepdim=True)

            updates = torch.einsum("nkp,npd->nkd", attn, v)

            slots = self.gru(
                updates.reshape(-1, self.slot_dim), slots_prev.reshape(-1, self.slot_dim)
            )
            slots = slots.reshape(n, self.n_slots, self.slot_dim)
            slots = slots + self.mlp(self.norm_pre_ff(slots))

        return slots


class SlotModule(nn.Module):
    """SlotAttention + 型ヘッド。

    契約：(tokens[B,T,P,De]) -> slots[B,T,K,D], type_logits[B,T,K,6]。
    """

    def __init__(
        self,
        input_dim: int,
        n_slots: int = 16,
        slot_dim: int = 128,
        n_iters: int = 3,
        n_types: int = 6,
    ):
        super().__init__()
        self.attn = SlotAttention(
            n_slots=n_slots, slot_dim=slot_dim, input_dim=input_dim, n_iters=n_iters
        )
        self.type_head = nn.Linear(slot_dim, n_types)

    def forward(self, tokens: Tensor) -> tuple[Tensor, Tensor]:
        b, t, p, de = tokens.shape
        flat = tokens.reshape(b * t, p, de)
        slots = self.attn(flat)
        k, d = slots.shape[-2], slots.shape[-1]
        slots = slots.reshape(b, t, k, d)
        type_logits = self.type_head(slots)
        return slots, type_logits


__all__ = ["SlotAttention", "SlotModule", "SLOT_TYPES"]
