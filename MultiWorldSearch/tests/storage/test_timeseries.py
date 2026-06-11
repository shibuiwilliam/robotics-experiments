"""Tests for timeseries store."""

from mws.storage.timeseries import TimeseriesRecord, TimeseriesStore


def test_add_and_query_range() -> None:
    ts = TimeseriesStore()
    ts.add(TimeseriesRecord(atom_id="a1", timestamp=1.0, values={"temp": 25.0}))
    ts.add(TimeseriesRecord(atom_id="a2", timestamp=2.0, values={"temp": 30.0}))
    ts.add(TimeseriesRecord(atom_id="a3", timestamp=3.0, values={"temp": 80.0}))

    results = ts.query_range(1.5, 3.5)
    assert len(results) == 2
    assert results[0].atom_id == "a2"


def test_latest() -> None:
    ts = TimeseriesStore()
    ts.add(TimeseriesRecord(atom_id="a1", timestamp=1.0, values={"v": 1.0}))
    ts.add(TimeseriesRecord(atom_id="a2", timestamp=2.0, values={"v": 2.0}))
    latest = ts.latest(1)
    assert len(latest) == 1
    assert latest[0].atom_id == "a2"


def test_tag_filter() -> None:
    ts = TimeseriesStore()
    ts.add(
        TimeseriesRecord(atom_id="a1", timestamp=1.0, values={"v": 1.0}, tags={"sensor": "temp"})
    )
    ts.add(
        TimeseriesRecord(atom_id="a2", timestamp=2.0, values={"v": 2.0}, tags={"sensor": "vibr"})
    )
    results = ts.query_range(0, 10, tag_filter={"sensor": "temp"})
    assert len(results) == 1
    assert results[0].atom_id == "a1"
