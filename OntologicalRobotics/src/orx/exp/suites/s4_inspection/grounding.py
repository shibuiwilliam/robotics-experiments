"""S4 視覚署名と位置の距離（X4: ID無しアンカリング）。

各資産は固有の据付時署名（asset_id で決定的に生成）を持つ。観測署名・参照署名はそこへノイズを
加えたもの。アンカリングは位置近接（記号/時空間）とコサイン署名（ベクトル）の融合で行う。
"""

from __future__ import annotations

import numpy as np

from orx.common.seeding import SeedTree


def asset_signature(asset_id: str, dim: int) -> list[float]:
    rng = SeedTree(0).child("s4-sig").child(asset_id).rng()
    v = rng.normal(0.0, 1.0, dim)
    return [float(x) for x in (v / (np.linalg.norm(v) or 1.0))]


def noisy(base: list[float], sigma: float, rng: np.random.Generator) -> list[float]:
    v = np.asarray(base) + rng.normal(0.0, sigma, len(base))
    n = np.linalg.norm(v)
    return [float(x) for x in (v / n if n else v)]


def cos(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a), np.asarray(b)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    return float(np.dot(va, vb) / (na * nb)) if na and nb else 0.0


def neg_distance(a: list[float], b: list[float]) -> float:
    return -float(np.linalg.norm(np.asarray(a) - np.asarray(b)))
