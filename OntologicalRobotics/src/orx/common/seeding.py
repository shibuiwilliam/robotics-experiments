"""単一ルートシードからの決定的シード派生（PROJECT.md §8.3）。

各モジュールは `SeedTree.child(name).rng()` で乱数源を得る。
`random.seed()` / `np.random.seed()` の直接呼び出しは禁止（CLAUDE.md §4）。
"""

from __future__ import annotations

import hashlib

import numpy as np


class SeedTree:
    """名前付き経路でシードを派生する決定的ツリー。"""

    def __init__(self, root_seed: int, path: tuple[str, ...] = ()) -> None:
        self.root_seed = root_seed
        self.path = path

    def child(self, name: str) -> SeedTree:
        return SeedTree(self.root_seed, (*self.path, name))

    def seed(self) -> int:
        h = hashlib.sha256()
        h.update(str(self.root_seed).encode())
        for part in self.path:
            h.update(b"/")
            h.update(part.encode())
        return int.from_bytes(h.digest()[:8], "big")

    def rng(self) -> np.random.Generator:
        return np.random.default_rng(self.seed())


def deterministic_id(rng: np.random.Generator, nbytes: int = 8) -> str:
    """seeded RNG から決定的な16進IDを生成する（リプレイ同一性のため）。"""
    return bytes(rng.bytes(nbytes)).hex()
