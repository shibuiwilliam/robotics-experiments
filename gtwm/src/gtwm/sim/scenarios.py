"""シナリオ列挙（オントロジー制約からの生成）とドメインランダム化。

制約は最小実装のプレースホルダー（進入禁止・危険物隣接・容量）。SHACL による本格的な
制約検証は着手順3（kg）・着手順8（realism）で接続する。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

ZONE_CAPACITY_DEFAULT = 30
HAZMAT_MIN_DISTANCE_M = 1.5


@dataclass
class Placement:
    entity: str
    x: float
    y: float
    zone: str
    hazmat: bool = False


@dataclass
class DomainRandomizationConfig:
    light_intensity_jitter: float
    light_dir_jitter_deg: float
    floor_color_jitter: float
    rack_color_jitter: float
    placement_variant: int


def sample_domain_randomization(seed: int) -> DomainRandomizationConfig:
    rng = np.random.default_rng(seed)
    return DomainRandomizationConfig(
        light_intensity_jitter=float(rng.uniform(-0.15, 0.15)),
        light_dir_jitter_deg=float(rng.uniform(-10.0, 10.0)),
        floor_color_jitter=float(rng.uniform(-0.05, 0.05)),
        rack_color_jitter=float(rng.uniform(-0.05, 0.05)),
        placement_variant=int(rng.integers(0, 1_000_000)),
    )


def check_single_zone(placements: list[Placement], zone_half_x: float = 1.0) -> list[str]:
    """各個体が単一ゾーンに属するか（ゾーン境界にまたがっていないか）を検査する。"""
    violations = []
    for p in placements:
        # ゾーン中心からのオフセットが半width の90%を超える場合は境界またぎとみなす。
        zone_centers = {
            "Dock_In": -5.0,
            "Inspect": -3.0,
            "Storage_A": -1.0,
            "Storage_B": 1.0,
            "Pick": 3.0,
            "Dock_Out": 5.0,
        }
        center = zone_centers.get(p.zone)
        if center is None:
            violations.append(f"{p.entity}: 未知ゾーン {p.zone}")
            continue
        if abs(p.x - center) > zone_half_x * 0.9:
            violations.append(f"{p.entity}: ゾーン境界にまたがる可能性 ({p.zone})")
    return violations


def check_hazmat_adjacency(
    placements: list[Placement], min_distance: float = HAZMAT_MIN_DISTANCE_M
) -> list[str]:
    """危険物指定の個体が他の個体に近すぎないかを検査する。"""
    violations = []
    hazmats = [p for p in placements if p.hazmat]
    others = placements
    for h in hazmats:
        for o in others:
            if o.entity == h.entity:
                continue
            dist = float(np.hypot(h.x - o.x, h.y - o.y))
            if dist < min_distance:
                violations.append(f"{h.entity} と {o.entity} の距離 {dist:.2f}m < {min_distance}m")
    return violations


def check_zone_capacity(
    placements: list[Placement], capacity: int = ZONE_CAPACITY_DEFAULT
) -> list[str]:
    """ゾーンあたりの個体数が容量を超えないかを検査する。"""
    counts: dict[str, int] = {}
    for p in placements:
        counts[p.zone] = counts.get(p.zone, 0) + 1
    return [
        f"{zone}: {count} > 容量{capacity}" for zone, count in counts.items() if count > capacity
    ]


def validate_placements(placements: list[Placement]) -> list[str]:
    return (
        check_single_zone(placements)
        + check_hazmat_adjacency(placements)
        + check_zone_capacity(placements)
    )


@dataclass
class ScenarioConfig:
    seed: int
    domain_randomization: DomainRandomizationConfig
    hazmat_entities: list[str] = field(default_factory=list)


def enumerate_scenarios(
    n: int,
    seed: int,
    candidate_entities: list[str],
    placements_fn: Callable[[list[str]], list[Placement]],
) -> list[ScenarioConfig]:
    """n 個のシナリオ設定を、制約に違反しないよう棄却サンプリングしながら列挙する。

    `placements_fn(hazmat_entities) -> list[Placement]` は候補の危険物集合に対する
    配置を返す呼び出し側の関数（実際の配置はシミュレーション側が持つため注入する）。
    """
    rng = np.random.default_rng(seed)
    scenarios: list[ScenarioConfig] = []
    for i in range(n):
        for _attempt in range(50):
            hazmat_count = int(rng.integers(0, 3))
            hazmat_entities = list(rng.choice(candidate_entities, size=hazmat_count, replace=False))
            placements = placements_fn(hazmat_entities)
            violations = validate_placements(placements)
            if not violations:
                scenarios.append(
                    ScenarioConfig(
                        seed=seed + i,
                        domain_randomization=sample_domain_randomization(seed + i),
                        hazmat_entities=hazmat_entities,
                    )
                )
                break
        else:
            scenarios.append(
                ScenarioConfig(
                    seed=seed + i,
                    domain_randomization=sample_domain_randomization(seed + i),
                    hazmat_entities=[],
                )
            )
    return scenarios
