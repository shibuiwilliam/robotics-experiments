"""Tests for consolidation engine, dedup, and TTL pruning."""

from __future__ import annotations

from mws.consolidation.dedup import deduplicate
from mws.consolidation.engine import ConsolidationEngine
from mws.consolidation.ttl import prune_by_ttl
from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality


def _make_atom(
    tags: list[str],
    text: str = "obs",
    ts: float = 1.0,
    entity_id: str | None = None,
    x: float = 0.0,
    y: float = 0.0,
) -> ExperienceAtom:
    return ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=SpatiotemporalCoord(x=x, y=y, z=0, timestamp=ts),
        text_summary=text,
        tags=tags,
        entity_id=entity_id,
    )


# --- ConsolidationEngine ---


def test_cluster_by_tags_groups_atoms() -> None:
    engine = ConsolidationEngine()
    atoms = [
        _make_atom(["robot", "zone-a"], "a1"),
        _make_atom(["robot", "zone-a"], "a2"),
        _make_atom(["sensor", "zone-b"], "b1"),
    ]
    clusters = engine.cluster_by_tags(atoms)

    # atoms with same tags (minus sim/pose) should cluster together
    assert len(clusters) >= 2
    total = sum(len(v) for v in clusters.values())
    assert total == 3


def test_summarize_cluster_produces_summary() -> None:
    engine = ConsolidationEngine()
    atoms = [
        _make_atom(["robot"], "first obs", ts=1.0, x=0.0),
        _make_atom(["robot"], "second obs", ts=2.0, x=4.0),
    ]
    summary = engine.summarize_cluster("robot", atoms)

    assert summary.modality == Modality.DOCUMENT
    assert "Consolidated (2 atoms)" in summary.text_summary
    assert "consolidated" in summary.tags
    assert summary.structured_fields["n_source_atoms"] == 2
    # Average x should be 2.0
    assert summary.coord.x == 2.0
    # Max timestamp
    assert summary.coord.timestamp == 2.0


def test_consolidate_end_to_end() -> None:
    engine = ConsolidationEngine()
    atoms = [
        _make_atom(["robot", "zone-a"], "a1"),
        _make_atom(["robot", "zone-a"], "a2"),
        _make_atom(["robot", "zone-a"], "a3"),
        _make_atom(["sensor", "zone-b"], "b1"),  # singleton, won't consolidate
    ]
    summaries = engine.consolidate(atoms)

    # Only clusters with >= 2 atoms get summarized
    assert len(summaries) >= 1
    for s in summaries:
        assert "consolidated" in s.tags


# --- Deduplication ---


def test_dedup_removes_duplicates() -> None:
    atoms = [
        _make_atom(["a"], "same text"),
        _make_atom(["b"], "same text"),
        _make_atom(["c"], "different text"),
    ]
    result = deduplicate(atoms)

    assert len(result) == 2
    texts = [a.text_summary for a in result]
    assert "same text" in texts
    assert "different text" in texts


def test_dedup_keeps_unique() -> None:
    atoms = [
        _make_atom(["a"], "text1"),
        _make_atom(["b"], "text2"),
        _make_atom(["c"], "text3"),
    ]
    result = deduplicate(atoms)
    assert len(result) == 3


# --- TTL Pruning ---


def test_ttl_keeps_recent_atoms() -> None:
    atoms = [
        _make_atom(["a"], "recent", ts=90.0),
        _make_atom(["b"], "old", ts=10.0),
    ]
    result = prune_by_ttl(atoms, max_age=50.0, current_time=100.0)

    assert len(result) == 1
    assert result[0].text_summary == "recent"


def test_ttl_preserves_at_least_one_per_entity() -> None:
    atoms = [
        _make_atom(["a"], "old-e1", ts=10.0, entity_id="e1"),
        _make_atom(["b"], "older-e1", ts=5.0, entity_id="e1"),
        _make_atom(["c"], "old-e2", ts=8.0, entity_id="e2"),
    ]
    result = prune_by_ttl(atoms, max_age=50.0, current_time=100.0)

    # All are expired, but we keep latest per entity
    entity_ids = [a.entity_id for a in result]
    assert "e1" in entity_ids
    assert "e2" in entity_ids
    # Should keep the latest for e1 (ts=10)
    e1_atom = next(a for a in result if a.entity_id == "e1")
    assert e1_atom.coord.timestamp == 10.0
