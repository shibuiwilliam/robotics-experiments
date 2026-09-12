"""視覚エンコーダ：凍結 DINOv2 バックボーン + 学習可能アダプタ。

契約（world_model.md）：Encoder.encode(frames: [B,T,C,3,H,W]) -> tokens [B,T,C,P,De]
Bはバッチ、Tは時刻、Cはカメラ数、Pはパッチ数、Deはアダプタ後の埋め込み次元。
"""

from __future__ import annotations

import torch
from torch import Tensor, nn


class Encoder(nn.Module):
    """凍結バックボーン（例：facebook/dinov2-small）+ 学習可能な線形アダプタ1層。

    バックボーンは呼び出し側から注入する（DI）。理由：
    - 実運用では `build_encoder()` が `transformers.AutoModel.from_pretrained` で
      HF キャッシュから読み込む（ネットワークが必要）。
    - `tests/unit` はネットワーク禁止のため、ランダム初期化の極小 Dinov2Model
      （`Dinov2Config` を直接構成、`from_pretrained` を呼ばない）を注入して
      形状のみを検査する。
    """

    def __init__(
        self, backbone: nn.Module, hidden_size: int, adapter_dim: int, freeze: bool = True
    ):
        super().__init__()
        self.backbone = backbone
        if freeze:
            for p in self.backbone.parameters():
                p.requires_grad_(False)
            self.backbone.eval()
        self.freeze = freeze
        self.adapter = nn.Linear(hidden_size, adapter_dim)

    def encode(self, frames: Tensor) -> Tensor:
        """frames: [B,T,C,3,H,W] (float32, 0..1 正規化済み) -> tokens [B,T,C,P,De]。"""
        b, t, c, ch, h, w = frames.shape
        flat = frames.reshape(b * t * c, ch, h, w)

        ctx = torch.no_grad() if self.freeze else torch.enable_grad()
        with ctx:
            out = self.backbone(pixel_values=flat, interpolate_pos_encoding=True)
            patch_tokens = out.last_hidden_state[:, 1:, :]  # CLS トークンを除く

        tokens = self.adapter(patch_tokens)
        p, de = tokens.shape[-2], tokens.shape[-1]
        return tokens.reshape(b, t, c, p, de)

    def forward(self, frames: Tensor) -> Tensor:
        return self.encode(frames)


def build_encoder(model_name: str, adapter_dim: int, freeze: bool = True) -> Encoder:
    """`transformers.AutoModel.from_pretrained` 経由で HF キャッシュからバックボーンを読む。

    torch.hub は使わない。学習・`gtwm wm train` からのみ呼ぶこと（tests/unit からは呼ばない）。
    """
    from transformers import AutoModel

    backbone = AutoModel.from_pretrained(model_name)
    hidden_size = int(backbone.config.hidden_size)
    return Encoder(
        backbone=backbone, hidden_size=hidden_size, adapter_dim=adapter_dim, freeze=freeze
    )


def build_tiny_test_backbone(
    hidden_size: int = 16, image_size: int = 32, patch_size: int = 8
) -> nn.Module:
    """ネットワーク不要のランダム初期化 Dinov2Model（テスト専用、形状検証のみ）。"""
    from transformers import Dinov2Config, Dinov2Model

    config = Dinov2Config(
        hidden_size=hidden_size,
        num_hidden_layers=1,
        num_attention_heads=2,
        mlp_ratio=2,
        image_size=image_size,
        patch_size=patch_size,
    )
    return Dinov2Model(config)


__all__ = ["Encoder", "build_encoder", "build_tiny_test_backbone"]
