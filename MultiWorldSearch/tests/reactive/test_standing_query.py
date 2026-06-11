"""Tests for the standing query engine."""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality
from mws.reactive.standing_query import StandingQueryEngine


def _make_atom(tags: list[str], text: str = "test") -> ExperienceAtom:
    return ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=SpatiotemporalCoord(x=0, y=0, z=0, timestamp=1.0),
        text_summary=text,
        tags=tags,
    )


def test_matching_atom_fires_query() -> None:
    engine = StandingQueryEngine()
    fired_atoms: list[ExperienceAtom] = []
    engine.register("alert-heat", ["temperature", "alert"], callback=fired_atoms.append)

    atom = _make_atom(["temperature", "zone-a"])
    result = engine.on_atom_ingested(atom)

    assert result == ["alert-heat"]
    assert len(fired_atoms) == 1
    assert fired_atoms[0].atom_id == atom.atom_id


def test_non_matching_atom_does_not_fire() -> None:
    engine = StandingQueryEngine()
    fired_atoms: list[ExperienceAtom] = []
    engine.register("alert-heat", ["temperature"], callback=fired_atoms.append)

    atom = _make_atom(["humidity", "zone-b"])
    result = engine.on_atom_ingested(atom)

    assert result == []
    assert len(fired_atoms) == 0


def test_fire_count_tracking() -> None:
    engine = StandingQueryEngine()
    engine.register("q1", ["tag-a"])
    engine.register("q2", ["tag-b"])

    engine.on_atom_ingested(_make_atom(["tag-a"]))
    engine.on_atom_ingested(_make_atom(["tag-a"]))
    engine.on_atom_ingested(_make_atom(["tag-b"]))

    assert engine.registered_count == 2
    assert engine.total_fires == 3
    assert len(engine.fired_log) == 3


def test_multiple_queries_fire_on_same_atom() -> None:
    engine = StandingQueryEngine()
    engine.register("q1", ["tag-a"])
    engine.register("q2", ["tag-a", "tag-b"])

    result = engine.on_atom_ingested(_make_atom(["tag-a"]))
    assert sorted(result) == ["q1", "q2"]
