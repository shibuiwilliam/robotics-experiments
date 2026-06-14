"""S7 視覚署名（X4: ID無し）— 物品種プロトタイプ＋所有者オフセットの単位ベクトル。

各入居者の所有物は「種プロトタイプ＋所有者固有オフセット×分離係数＋ノイズ」で生成。
分離係数を小さくすると瓜二つ（look-alike）になり、ベクトル単独では所有者を判別できなくなる。
"""

from __future__ import annotations

import numpy as np

from orx.common.seeding import SeedTree


def prototype(item_type: str, dim: int) -> list[float]:
    rng = SeedTree(0).child("s7-proto").child(item_type).rng()
    v = rng.normal(0.0, 1.0, dim)
    return [float(x) for x in (v / (np.linalg.norm(v) or 1.0))]


def owner_offset(resident: str, dim: int) -> list[float]:
    rng = SeedTree(0).child("s7-owner").child(resident).rng()
    v = rng.normal(0.0, 1.0, dim)
    return [float(x) for x in (v / (np.linalg.norm(v) or 1.0))]


def _signature(
    proto: list[float], offset: list[float], sep: float, sigma: float, rng: np.random.Generator
) -> list[float]:
    v = np.asarray(proto) + sep * np.asarray(offset) + rng.normal(0.0, sigma, len(proto))
    n = np.linalg.norm(v)
    return [float(x) for x in (v / n if n else v)]


def true_signature(
    proto: list[float], offset: list[float], sep: float, sigma: float, rng: np.random.Generator
) -> list[float]:
    """観測される真の視覚署名。"""
    return _signature(proto, offset, sep, sigma, rng)


def note_signature(
    proto: list[float], offset: list[float], sep: float, sigma: float, rng: np.random.Generator
) -> list[float]:
    """台帳に記録された特徴メモ署名（別ノイズ）。"""
    return _signature(proto, offset, sep, sigma, rng)


def cos(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a), np.asarray(b)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    return float(np.dot(va, vb) / (na * nb)) if na and nb else 0.0
