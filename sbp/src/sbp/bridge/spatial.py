"""空間束縛（設計書 §5.5、§3.7、bridge-worldmodel ルール）。

位置分布 ``N(μ, Σ)`` と Zone の幾何から**包含確率**を計算し、``locatedIn`` を確率的述語として
出力する。階層（棚 → 区画 → 建屋）は包含推移で導く。

**なぜ確率なのか**: 位置推定には必ず誤差があり、「このパレットは区画 A にある」と断定すると
境界付近での誤りが信念に直接入る。包含確率にしておけば、境界にあるものは 0.5 付近になり、
下流（照合）が「どちらとも言えない」として扱える。設計書 §5.5 が ``locatedIn`` を
「位置分布と Zone の包含確率から派生する確率的述語」と定めているのはこのためである。

ゾーン包含の計算は shapely（bridge-worldmodel ルール）。位置は ``(mean, cov)`` で受け取る。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np

from sbp.core.errors import BridgeError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["PositionBelief", "Zone", "containment_hierarchy", "containment_probability"]

log = get_logger(__name__)

_MONTE_CARLO_SAMPLES: Final = 2000
"""包含確率のモンテカルロ標本数。解析解が無い一般のポリゴンに対して使う。"""


@dataclass(frozen=True, slots=True)
class Zone:
    """施設座標系上の区域（設計書 §3.7）。

    Attributes:
        iri: ゾーンの IRI。
        polygon: 外周の頂点列（施設座標系、(x, y) の列）。
        contained_in: 上位ゾーンの IRI（階層。``None`` なら最上位）。
        zone_type: ゾーンの種別（危険区域、保管区画など）。
    """

    iri: str
    polygon: tuple[tuple[float, float], ...]
    contained_in: str | None = None
    zone_type: str | None = None

    def __post_init__(self) -> None:
        if len(self.polygon) < 3:
            raise BridgeError(
                f"zone {self.iri} needs at least 3 vertices to bound an area, "
                f"got {len(self.polygon)}"
            )

    def to_shapely(self) -> object:
        """shapely の Polygon に変換する。"""
        from shapely.geometry import Polygon  # noqa: PLC0415 — 重い依存は遅延 import

        return Polygon(self.polygon)

    @property
    def area(self) -> float:
        """面積（施設座標系の単位の 2 乗）。"""
        polygon = self.to_shapely()
        return float(polygon.area)  # type: ignore[attr-defined]


@dataclass(frozen=True, slots=True)
class PositionBelief:
    """位置の信念（2 次元ガウス）。

    Attributes:
        mean: 位置の平均 (x, y)。
        cov_diag: 共分散の対角成分 (σ²_x, σ²_y)。相関は持たない（知覚側が主軸に揃えた
            形で渡す前提。相関が必要になったら完全な共分散行列に拡張する）。
    """

    mean: tuple[float, float]
    cov_diag: tuple[float, float]

    def __post_init__(self) -> None:
        if any(v <= 0.0 for v in self.cov_diag):
            raise BridgeError(
                f"position variance must be positive, got {self.cov_diag}; "
                "a zero-variance position would make containment a hard decision"
            )

    @property
    def stddev(self) -> tuple[float, float]:
        """標準偏差。"""
        return (float(np.sqrt(self.cov_diag[0])), float(np.sqrt(self.cov_diag[1])))


def containment_probability(
    position: PositionBelief, zone: Zone, *, seed: int = 0, samples: int = _MONTE_CARLO_SAMPLES
) -> float:
    """位置がゾーンに含まれる確率（設計書 §5.5）。

    一般のポリゴンに対する 2 次元ガウスの積分は解析解を持たないため、モンテカルロで推定する。
    標本数は既定 2000 で、確率の標準誤差は最悪でも約 0.011（= 0.5/√2000）に収まる。
    これは下流の閾値（θ_1 = 0.8 など）に対して十分細かい。

    Args:
        position: 位置の信念。
        zone: 対象ゾーン。
        seed: 乱数シード（決定的にするため必須。CLAUDE.md 数値規約）。
        samples: モンテカルロ標本数。

    Returns:
        包含確率。
    """
    from shapely.geometry import Point  # noqa: PLC0415
    from shapely.prepared import prep  # noqa: PLC0415

    rng = np.random.default_rng(seed)
    sx, sy = position.stddev
    points_x = rng.normal(position.mean[0], sx, size=samples)
    points_y = rng.normal(position.mean[1], sy, size=samples)

    prepared = prep(zone.to_shapely())  # type: ignore[arg-type]
    inside = sum(
        1 for x, y in zip(points_x, points_y, strict=True) if prepared.contains(Point(x, y))
    )
    return inside / samples


def containment_hierarchy(
    position: PositionBelief,
    zones: Sequence[Zone],
    *,
    seed: int = 0,
) -> dict[str, float]:
    """全ゾーンに対する包含確率を返し、階層を推移で埋める（設計書 §5.5）。

    下位ゾーンに含まれるなら上位ゾーンにも含まれる（包含推移）。推移で得た確率は、直接計算した
    確率と下位からの伝播のうち**大きい方**を採る。下位に含まれていることが分かれば上位にも
    含まれているはずで、上位の直接計算が小さく出た場合（幾何の誤差など）は下位の情報の方が強い。

    Args:
        position: 位置の信念。
        zones: 対象ゾーン（階層を含む）。
        seed: 乱数シード。

    Returns:
        ゾーン IRI → 包含確率。
    """
    direct = {zone.iri: containment_probability(position, zone, seed=seed) for zone in zones}
    by_iri = {zone.iri: zone for zone in zones}

    result = dict(direct)
    for zone in zones:
        probability = direct[zone.iri]
        parent = zone.contained_in
        # 上位へ伝播（循環があっても止まるよう訪問済みを持つ）
        visited: set[str] = {zone.iri}
        while parent is not None and parent not in visited:
            visited.add(parent)
            if parent in result:
                result[parent] = max(result[parent], probability)
            parent_zone = by_iri.get(parent)
            parent = parent_zone.contained_in if parent_zone is not None else None

    log.debug(
        "bridge.containment_computed",
        zones=len(zones),
        max_probability=max(result.values()) if result else 0.0,
    )
    return result


def most_likely_zone(containment: dict[str, float], *, min_probability: float = 0.5) -> str | None:
    """最も確からしいゾーンを返す（確率が閾値未満なら ``None``）。

    ``None`` を返すのは「どこにあるか言えない」という答えであり、適当に最大値を返すより正しい。

    Args:
        containment: ゾーン IRI → 包含確率。
        min_probability: これ未満なら ``None`` を返す。
    """
    if not containment:
        return None
    iri, probability = max(containment.items(), key=lambda kv: kv[1])
    return iri if probability >= min_probability else None
