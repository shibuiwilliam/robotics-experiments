"""`eval/experiments/exp03.py` の部分グラフ構築ヘルパーの単体テスト。"""

from __future__ import annotations

import pytest

from gtwm.eval.experiments.exp03 import _dynamic_subgraph, _static_subgraph
from gtwm.sim.env import ZONE_NAMES

pytestmark = pytest.mark.unit


def test_static_subgraph_has_fixed_zone_adjacency_chain() -> None:
    sg = _static_subgraph()
    assert sg.nodes == [f"gt:Zone_{z}" for z in ZONE_NAMES]
    assert len(sg.edges) == len(ZONE_NAMES) - 1
    assert all(pred == "gt:adjacentTo" for _, _, pred in sg.edges)


def test_dynamic_subgraph_reflects_current_occupancy() -> None:
    sg = _dynamic_subgraph({"gt:Pallet_0001": "Storage_A", "gt:Pallet_0002": "Pick"})
    zone_nodes = {f"gt:Zone_{z}" for z in ZONE_NAMES}
    assert zone_nodes.issubset(set(sg.nodes))
    assert "gt:Pallet_0001" in sg.nodes
    assert "gt:Pallet_0002" in sg.nodes
    assert len(sg.edges) == 2
    assert all(pred == "gt:currentZone" for _, _, pred in sg.edges)


def test_dynamic_subgraph_skips_unknown_zone() -> None:
    sg = _dynamic_subgraph({"gt:Pallet_0001": "Nonexistent_Zone"})
    assert sg.edges == []
