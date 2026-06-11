"""Tests for ExperienceAtom schema."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import EmbeddingSpace, Modality


def test_atom_creation() -> None:
    coord = SpatiotemporalCoord(x=1.0, y=2.0, z=3.0, timestamp=1700000000.0)
    atom = ExperienceAtom(modality=Modality.TELEMETRY, coord=coord)
    assert atom.atom_id
    assert atom.modality == Modality.TELEMETRY
    assert atom.trust == 1.0
    assert atom.freshness == 1.0


def test_atom_serialization_roundtrip() -> None:
    coord = SpatiotemporalCoord(x=1.0, y=2.0, z=0.0, timestamp=1700000000.0)
    atom = ExperienceAtom(
        modality=Modality.STRUCTURED_RECORD,
        coord=coord,
        text_summary="Motor temperature anomaly",
        tags=["maintenance", "motor"],
        structured_fields={"equipment_id": "CONV-01", "severity": 3},
    )
    data = atom.model_dump()
    restored = ExperienceAtom.model_validate(data)
    assert restored.atom_id == atom.atom_id
    assert restored.text_summary == atom.text_summary
    assert restored.structured_fields["equipment_id"] == "CONV-01"


def test_atom_embedding_add_get() -> None:
    coord = SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=0.0)
    atom = ExperienceAtom(modality=Modality.TELEMETRY, coord=coord)
    vec = [0.1] * 128
    atom.add_embedding(EmbeddingSpace.MOCK_128, vec, content_hash="abc123")
    result = atom.get_embedding(EmbeddingSpace.MOCK_128)
    assert result is not None
    assert len(result) == 128
    assert atom.get_embedding(EmbeddingSpace.GEMINI_3072) is None


def test_atom_provenance() -> None:
    coord = SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=0.0)
    atom = ExperienceAtom(modality=Modality.CONTACT, coord=coord)
    atom.provenance.add(
        source_id="sensor-001",
        source_type="sensor",
        action="created",
        details={"sensor_model": "force_torque"},
    )
    assert atom.provenance.origin is not None
    assert atom.provenance.origin.source_id == "sensor-001"
