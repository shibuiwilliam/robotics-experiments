"""Seeded RNG registry (NFR-DETERM, CLAUDE.md §0-5).

No production path may use an unseeded ``random``. All randomness is drawn from named
sub-streams forked deterministically from a single master seed::

    seed -> SHA256(seed || name) -> numpy.random.Generator

Same master seed + same sub-stream name -> bit-identical draws across runs. The canonical
sub-stream names are declared in ``config/registry.yaml`` (determinism.substreams).
"""

from __future__ import annotations

import hashlib

import numpy as np


def _derive_seed(master_seed: int, name: str) -> int:
    digest = hashlib.sha256(f"{master_seed}:{name}".encode()).digest()
    # 64-bit sub-seed from the first 8 bytes.
    return int.from_bytes(digest[:8], "big", signed=False)


class RngRegistry:
    """Deterministic factory of named ``numpy`` Generators forked from a master seed."""

    def __init__(self, master_seed: int) -> None:
        self._master_seed = int(master_seed)
        self._cache: dict[str, np.random.Generator] = {}

    @property
    def master_seed(self) -> int:
        return self._master_seed

    def generator(self, name: str) -> np.random.Generator:
        """Return the (cached) Generator for sub-stream ``name``."""
        gen = self._cache.get(name)
        if gen is None:
            gen = np.random.default_rng(_derive_seed(self._master_seed, name))
            self._cache[name] = gen
        return gen

    def fork(self, name: str) -> RngRegistry:
        """A child registry whose master seed is derived from this one and ``name``."""
        return RngRegistry(_derive_seed(self._master_seed, f"fork:{name}"))
