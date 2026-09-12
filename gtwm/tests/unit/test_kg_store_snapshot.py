"""KGStore.snapshot(t) のバイテンポラル挙動（有効時間で成り立つ信念だけを返す）。"""

from datetime import datetime, timedelta

import pytest
from rdflib import Namespace

from gtwm.kg.schema import GT, Belief
from gtwm.kg.store import RdflibKGStore

pytestmark = pytest.mark.unit

GTNS = Namespace(GT)
T0 = datetime(2026, 1, 1, 0, 0, 0)


def _store_with_move() -> RdflibKGStore:
    store = RdflibKGStore()
    # Pallet_0001: T0..T0+10s は Storage_A、T0+10s 以降は Pick。
    store.add_beliefs(
        [
            Belief(
                subject="gt:Pallet_0001",
                predicate="gt:currentZone",
                object="gt:Zone_Storage_A",
                confidence=0.95,
                source="wm",
                valid_from=T0,
                valid_to=T0 + timedelta(seconds=10),
                transaction_time=T0,
            ),
            Belief(
                subject="gt:Pallet_0001",
                predicate="gt:currentZone",
                object="gt:Zone_Pick",
                confidence=0.9,
                source="wm",
                valid_from=T0 + timedelta(seconds=10),
                valid_to=None,
                transaction_time=T0 + timedelta(seconds=10),
            ),
        ]
    )
    return store


def test_snapshot_before_move_shows_original_zone() -> None:
    store = _store_with_move()
    snap = store.snapshot(T0 + timedelta(seconds=5))
    assert (GTNS.Pallet_0001, GTNS.currentZone, GTNS.Zone_Storage_A) in snap
    assert (GTNS.Pallet_0001, GTNS.currentZone, GTNS.Zone_Pick) not in snap


def test_snapshot_after_move_shows_new_zone() -> None:
    store = _store_with_move()
    snap = store.snapshot(T0 + timedelta(seconds=20))
    assert (GTNS.Pallet_0001, GTNS.currentZone, GTNS.Zone_Pick) in snap
    assert (GTNS.Pallet_0001, GTNS.currentZone, GTNS.Zone_Storage_A) not in snap


def test_snapshot_before_any_belief_is_empty() -> None:
    store = _store_with_move()
    snap = store.snapshot(T0 - timedelta(seconds=1))
    assert len(snap) == 0


def test_snapshot_exactly_at_boundary_uses_new_belief() -> None:
    """validTo は排他的境界（[validFrom, validTo)）：境界時刻ちょうどは新しい信念側に属する。"""
    store = _store_with_move()
    snap = store.snapshot(T0 + timedelta(seconds=10))
    assert (GTNS.Pallet_0001, GTNS.currentZone, GTNS.Zone_Pick) in snap
    assert (GTNS.Pallet_0001, GTNS.currentZone, GTNS.Zone_Storage_A) not in snap
