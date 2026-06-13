"""S6 視覚接地（X4: 記号IDに頼らない接地）— プロトタイプ最近傍（ADR-017）。

クラス毎の固定プロトタイプ（決定的）への単位ベクトルコサイン最近傍で接地し、
確信度 = top1−top2 マージンを返す。記号識別子は使わない（ID無し物体）。
"""

from __future__ import annotations

import numpy as np

from orx.common.seeding import SeedTree


def class_prototypes(classes: list[str], dim: int) -> dict[str, list[float]]:
    """クラス毎の決定的な単位プロトタイプベクトル（固定シード）。"""
    protos: dict[str, list[float]] = {}
    for c in classes:
        rng = SeedTree(0).child("s6-proto").child(c).rng()
        v = rng.normal(0.0, 1.0, dim)
        v = v / (np.linalg.norm(v) or 1.0)
        protos[c] = [float(x) for x in v]
    return protos


def embed_object(prototype: list[float], sigma: float, rng: np.random.Generator) -> list[float]:
    """プロトタイプ＋ガウスノイズの単位ベクトル埋め込み。"""
    v = np.asarray(prototype, dtype=float) + rng.normal(0.0, sigma, len(prototype))
    n = np.linalg.norm(v)
    return [float(x) for x in (v / n if n else v)]


def ground(
    embedding: list[float], prototypes: dict[str, list[float]]
) -> tuple[str, float]:
    """最近傍プロトタイプへ接地。返り値 (class, confidence=top1−top2 cos)。"""
    e = np.asarray(embedding, dtype=float)
    sims = sorted(
        ((float(np.dot(e, np.asarray(p))), c) for c, p in prototypes.items()),
        reverse=True,
    )
    top1, cls = sims[0]
    top2 = sims[1][0] if len(sims) > 1 else -1.0
    return cls, max(0.0, top1 - top2)
