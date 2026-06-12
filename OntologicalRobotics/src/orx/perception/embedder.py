"""視覚埋め込み器 — ローカルCLIP（MPS/CPU）と決定的スタブ。

CLIPはプロセス内シングルトン（初回ロードが遅い、CLAUDE.md §2）。
stub はモデルダウンロード不要で、画素から決定的に類似性のある埋め込みを作る
（同一物体の切り出しは似たベクトルになる）。テスト・デモはstubを使う。
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

STUB_DIM = 192  # 8x8x3


class VisualEmbedder(Protocol):
    def embed_crops(self, crops: list[np.ndarray]) -> list[list[float]]: ...


class StubVisualEmbedder:
    """8x8平均プーリングRGB → 192次元単位ベクトル（決定的）。"""

    def embed_crops(self, crops: list[np.ndarray]) -> list[list[float]]:
        out: list[list[float]] = []
        for crop in crops:
            if crop.size == 0:
                out.append([0.0] * STUB_DIM)
                continue
            h, w = crop.shape[:2]
            pooled = np.zeros((8, 8, 3), dtype=np.float64)
            for i in range(8):
                for j in range(8):
                    ys = slice(i * h // 8, max((i + 1) * h // 8, i * h // 8 + 1))
                    xs = slice(j * w // 8, max((j + 1) * w // 8, j * w // 8 + 1))
                    pooled[i, j] = crop[ys, xs].reshape(-1, 3).mean(axis=0)
            vec = pooled.reshape(-1) / 255.0 - 0.5
            norm = float(np.linalg.norm(vec))
            if norm < 1e-12:
                out.append([0.0] * STUB_DIM)
            else:
                out.append([float(v) for v in vec / norm])
        return out


class ClipVisualEmbedder:
    """open_clip をMPS（フォールバックCPU）で実行。プロセス内シングルトン。"""

    _instance: ClipVisualEmbedder | None = None

    def __init__(self, model_name: str = "ViT-B-32", pretrained: str = "laion2b_s34b_b79k") -> None:
        import open_clip
        import torch

        self._torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained
        )
        self.model = self.model.to(self.device).eval()

    @classmethod
    def shared(cls) -> ClipVisualEmbedder:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def embed_crops(self, crops: list[np.ndarray]) -> list[list[float]]:
        from PIL import Image

        torch = self._torch
        if not crops:
            return []
        batch = torch.stack(
            [self.preprocess(Image.fromarray(c.astype(np.uint8))) for c in crops]
        ).to(self.device)
        with torch.no_grad():
            feats = self.model.encode_image(batch)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return [[float(v) for v in row] for row in feats.cpu().numpy()]


def make_visual_embedder(kind: str) -> VisualEmbedder:
    if kind == "stub":
        return StubVisualEmbedder()
    if kind == "clip":
        return ClipVisualEmbedder.shared()
    raise ValueError(f"未知の視覚埋め込み器 {kind!r}（対応: stub / clip）")
