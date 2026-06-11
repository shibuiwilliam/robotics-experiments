"""Tests for the curiosity engine."""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality
from mws.reactive.curiosity import CuriosityEngine, GapDescriptor


def _make_atom(
    tags: list[str],
    text: str = "obs",
    region: str | None = None,
    modality: Modality = Modality.TELEMETRY,
) -> ExperienceAtom:
    fields: dict[str, str | float | int | bool] = {}
    if region:
        fields["region"] = region
    return ExperienceAtom(
        modality=modality,
        coord=SpatiotemporalCoord(x=0, y=0, z=0, timestamp=1.0),
        text_summary=text,
        tags=tags,
        structured_fields=fields,
    )


def test_gap_detected_when_no_atoms_in_region() -> None:
    engine = CuriosityEngine()
    atoms = [_make_atom(["zone-b"], region="zone-b")]

    gap = engine.detect_gap("zone-a", atoms)

    assert gap is not None
    assert isinstance(gap, GapDescriptor)
    assert gap.region == "zone-a"
    assert "No observations" in gap.reason
    assert engine.gaps_detected == 1


def test_no_gap_when_atoms_exist() -> None:
    engine = CuriosityEngine()
    atoms = [_make_atom(["zone-a"], region="zone-a")]

    gap = engine.detect_gap("zone-a", atoms)

    assert gap is None
    assert engine.gaps_detected == 0


def test_gap_detected_with_modality_filter() -> None:
    engine = CuriosityEngine()
    atoms = [_make_atom(["zone-a"], region="zone-a", modality=Modality.TELEMETRY)]

    # Require pointcloud but only telemetry available
    gap = engine.detect_gap("zone-a", atoms, required_modalities=["pointcloud"])

    assert gap is not None
    assert engine.gaps_detected == 1


def test_exploration_task_emission() -> None:
    engine = CuriosityEngine()
    gap = GapDescriptor(region="zone-c", reason="No observations in zone-c")

    task = engine.emit_exploration_task(gap, robot_id="robot-1")

    assert task["type"] == "exploration"
    assert task["target_region"] == "zone-c"
    assert task["assigned_robot"] == "robot-1"
    assert engine.tasks_emitted == 1
