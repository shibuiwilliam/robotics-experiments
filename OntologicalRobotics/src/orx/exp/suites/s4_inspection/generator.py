"""S4 エピソード生成（シード決定的）: 2視点の知覚個体＋矛盾する異常観測＋台帳。

真の異常がある資産は、ドローン（上方）は見落とし正常判定（低確信）・地上ロボット（近接）は
異常判定（高確信）→ 矛盾。来歴・確信度の調停が必要になる（H5）。
"""

from __future__ import annotations

from orx.common.seeding import SeedTree
from orx.exp.suites.s4_inspection.grounding import asset_signature, noisy
from orx.exp.suites.s4_inspection.model import (
    S4Episode,
    S4LedgerEntry,
    S4Observation,
    S4World,
)


def _readings(world: S4World, anomaly: bool) -> dict[str, tuple[bool, float]]:
    """視点別の (異常判定, 確信度)。異常時のみ視点間で矛盾する。"""
    if anomaly:
        return {
            "drone": (False, world.drone_detect_conf),  # 上方は滲みを見落とす
            "ground": (True, world.ground_detect_conf),  # 近接は検知
        }
    return {
        "drone": (False, world.normal_conf),
        "ground": (False, world.normal_conf),
    }


def generate_episode(world: S4World, seed: int, position_noise: float) -> S4Episode:
    obs_rng = SeedTree(seed).child("s4-obs").rng()
    ref_rng = SeedTree(seed).child("s4-ref").rng()
    observations: list[S4Observation] = []
    ledger: list[S4LedgerEntry] = []
    for asset in world.assets:
        true_sig = asset_signature(asset.asset_id, world.embedding_dim)
        ledger.append(
            S4LedgerEntry(
                asset_id=asset.asset_id,
                system=asset.system,
                ledger_pos=list(asset.ledger_pos),
                ref_signature=noisy(true_sig, world.ref_noise, ref_rng),
            )
        )
        readings = _readings(world, asset.true_anomaly)
        for vp in ("drone", "ground"):
            reading, conf = readings[vp]
            pos = [c + obs_rng.normal(0.0, position_noise) for c in asset.true_pos]
            observations.append(
                S4Observation(
                    viewpoint=vp,
                    true_asset=asset.asset_id,
                    pos=pos,
                    signature=noisy(true_sig, world.signature_noise, obs_rng),
                    anomaly_reading=reading,
                    confidence=conf,
                )
            )
    return S4Episode(observations=observations, ledger=ledger)
