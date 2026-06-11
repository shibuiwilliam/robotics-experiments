"""Spatial index using scipy KD-tree for spatial queries."""

from __future__ import annotations

import numpy as np
from scipy.spatial import KDTree


class SpatialIndex:
    """KD-tree backed spatial index for 3D position queries."""

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._positions: list[list[float]] = []
        self._tree: KDTree | None = None

    def add(self, atom_id: str, position: tuple[float, float, float]) -> None:
        """Add a point. Rebuilds tree lazily on next query."""
        self._ids.append(atom_id)
        self._positions.append(list(position))
        self._tree = None  # invalidate

    def _rebuild(self) -> None:
        if len(self._positions) > 0:
            self._tree = KDTree(np.array(self._positions))

    def query_radius(
        self,
        center: tuple[float, float, float],
        radius: float,
    ) -> list[tuple[str, float]]:
        """Find all points within radius. Returns (atom_id, distance) pairs."""
        if len(self._positions) == 0:
            return []
        if self._tree is None:
            self._rebuild()
        assert self._tree is not None
        indices = self._tree.query_ball_point(list(center), radius)
        center_arr = np.array(center)
        results = []
        for idx in indices:
            pos = np.array(self._positions[idx])
            dist = float(np.linalg.norm(pos - center_arr))
            results.append((self._ids[idx], dist))
        results.sort(key=lambda x: x[1])
        return results

    def query_nearest(
        self,
        point: tuple[float, float, float],
        k: int = 5,
    ) -> list[tuple[str, float]]:
        """Find k nearest neighbors. Returns (atom_id, distance) pairs."""
        if len(self._positions) == 0:
            return []
        if self._tree is None:
            self._rebuild()
        assert self._tree is not None
        k = min(k, len(self._ids))
        raw_distances, raw_indices = self._tree.query(list(point), k=k)
        if k == 1:
            return [(self._ids[int(raw_indices)], float(raw_distances))]
        idx_list: list[int] = [int(x) for x in np.asarray(raw_indices).flat]
        dist_list: list[float] = [float(x) for x in np.asarray(raw_distances).flat]
        return [(self._ids[i], d) for i, d in zip(idx_list, dist_list, strict=True)]

    @property
    def size(self) -> int:
        return len(self._ids)
