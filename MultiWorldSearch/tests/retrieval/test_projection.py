"""Tests for consumer-aware projection."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import ConsumerType, Modality
from mws.retrieval.projection import project_for_consumer


def _make_atom() -> ExperienceAtom:
    coord = SpatiotemporalCoord(x=1.0, y=2.0, z=0.0, timestamp=100.0)
    atom = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=coord,
        text_summary="Motor temperature 85C, above threshold",
        tags=["maintenance", "motor"],
        structured_fields={"equipment_id": "CONV-01"},
    )
    atom.provenance.add("sensor-01", "sensor", "created")
    return atom


def test_llm_projection() -> None:
    atom = _make_atom()
    proj = project_for_consumer(atom, ConsumerType.LLM)
    assert "text" in proj
    assert "citation" in proj
    assert "sensor" in proj["citation"]


def test_vla_projection() -> None:
    atom = _make_atom()
    proj = project_for_consumer(atom, ConsumerType.VLA)
    assert "position" in proj
    assert "payload" in proj


def test_audit_projection() -> None:
    atom = _make_atom()
    proj = project_for_consumer(atom, ConsumerType.AUDIT)
    assert "provenance" in proj
    assert len(proj["provenance"]) == 1
