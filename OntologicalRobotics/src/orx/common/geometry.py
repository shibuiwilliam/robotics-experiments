"""ゾーン幾何の共有ユーティリティ（ゾーン定義は公開された静的世界知識）。"""

from __future__ import annotations

from orx.common.config import ZoneConfig
from orx.common.schemas import Vec3

_Z_MARGIN = 0.30  # 箱がゾーン平面上に載った際の中心高さを許容する余裕 [m]


def zone_of(zones: list[ZoneConfig], position: Vec3) -> str | None:
    """位置が属するゾーン名（x-y包含＋z許容範囲）。複数該当時は中心距離最小。"""
    x, y, z = position
    hits: list[tuple[float, str]] = []
    for zone in zones:
        cx, cy, cz = zone.center
        sx, sy, sz = zone.size
        if abs(x - cx) > sx / 2 or abs(y - cy) > sy / 2:
            continue
        z_low = cz - sz / 2 - _Z_MARGIN
        z_high = cz + sz / 2 + _Z_MARGIN
        if not (z_low <= z <= z_high):
            continue
        d2 = (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2
        hits.append((d2, zone.name))
    if not hits:
        return None
    return min(hits)[1]
