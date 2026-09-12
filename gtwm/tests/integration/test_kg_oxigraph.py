"""OxigraphKGStore の統合テスト。`make up` 済み（Oxigraph が :7878 で稼働中）が前提。

到達できない場合は skip する（`make test-int` は `make up` 済みを前提とするが、
単体でこのファイルを実行した場合にも分かりやすく失敗させないため）。
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gtwm.kg.schema import Belief
from gtwm.kg.store import OxigraphKGStore, load_query

pytestmark = pytest.mark.integration

SHAPES_DIR = Path(__file__).resolve().parents[2] / "ontology" / "shapes"


@pytest.fixture
def store() -> OxigraphKGStore:
    s = OxigraphKGStore()
    if not s.is_available():
        pytest.skip("Oxigraph が http://localhost:7878 で応答しない（`make up` を先に実行）")
    s.clear()
    yield s
    s.clear()


def test_oxigraph_add_and_query_zone_at_time(store: OxigraphKGStore) -> None:
    t0 = datetime(2026, 1, 1)
    store.add_beliefs(
        [
            Belief(
                subject="gt:Pallet_0099",
                predicate="gt:currentZone",
                object="gt:Zone_Storage_A",
                confidence=0.9,
                source="wm",
                valid_from=t0,
                transaction_time=t0,
            )
        ]
    )
    rows = store.query(
        load_query("pallet_zone_at_time"),
        bindings={"pallet": "gt:Pallet_0099", "t": t0 + timedelta(seconds=1)},
    )
    assert len(rows) == 1
    assert rows[0]["zone"].endswith("Zone_Storage_A")


def test_oxigraph_snapshot_bitemporal(store: OxigraphKGStore) -> None:
    t0 = datetime(2026, 1, 1)
    store.add_beliefs(
        [
            Belief(
                subject="gt:Pallet_0100",
                predicate="gt:currentZone",
                object="gt:Zone_Storage_A",
                confidence=0.9,
                source="wm",
                valid_from=t0,
                valid_to=t0 + timedelta(seconds=10),
                transaction_time=t0,
            ),
            Belief(
                subject="gt:Pallet_0100",
                predicate="gt:currentZone",
                object="gt:Zone_Pick",
                confidence=0.9,
                source="wm",
                valid_from=t0 + timedelta(seconds=10),
                transaction_time=t0 + timedelta(seconds=10),
            ),
        ]
    )
    early = store.snapshot(t0 + timedelta(seconds=5))
    late = store.snapshot(t0 + timedelta(seconds=20))
    assert len(early) == 1
    assert len(late) == 1
    assert list(early)[0][2] != list(late)[0][2]


def test_oxigraph_validate_matches_rdflib_backend(store: OxigraphKGStore) -> None:
    t0 = datetime(2026, 1, 1)
    store.add_beliefs(
        [
            Belief(
                subject="gt:Pallet_0101",
                predicate="rdf:type",
                object="gt:Pallet",
                confidence=1.0,
                source="human",
                valid_from=t0,
                transaction_time=t0,
            ),
            Belief(
                subject="gt:Pallet_0101",
                predicate="gt:currentZone",
                object="gt:Zone_Storage_A",
                confidence=1.0,
                source="human",
                valid_from=t0,
                transaction_time=t0,
            ),
        ]
    )
    report = store.validate([SHAPES_DIR / "single_zone.ttl"])
    assert report.conforms
