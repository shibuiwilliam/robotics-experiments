"""Tests for spatial index."""

from mws.storage.spatial import SpatialIndex


def test_query_radius() -> None:
    si = SpatialIndex()
    si.add("a", (0.0, 0.0, 0.0))
    si.add("b", (1.0, 0.0, 0.0))
    si.add("c", (10.0, 10.0, 10.0))

    results = si.query_radius((0.0, 0.0, 0.0), radius=2.0)
    ids = [r[0] for r in results]
    assert "a" in ids
    assert "b" in ids
    assert "c" not in ids


def test_query_nearest() -> None:
    si = SpatialIndex()
    si.add("a", (0.0, 0.0, 0.0))
    si.add("b", (5.0, 0.0, 0.0))
    si.add("c", (1.0, 0.0, 0.0))

    results = si.query_nearest((0.0, 0.0, 0.0), k=2)
    assert len(results) == 2
    assert results[0][0] == "a"  # closest
    assert results[1][0] == "c"


def test_empty_index() -> None:
    si = SpatialIndex()
    assert si.query_radius((0, 0, 0), 1.0) == []
    assert si.query_nearest((0, 0, 0), 1) == []
