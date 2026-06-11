"""Tests for ground-truth label auto-derivation."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import Modality
from mws.eval.labels import derive_relevance_labels


def test_entity_match() -> None:
    coord = SpatiotemporalCoord(x=0, y=0, z=0, timestamp=0)
    atoms = [
        ExperienceAtom(modality=Modality.TELEMETRY, coord=coord, entity_id="motor-01"),
        ExperienceAtom(modality=Modality.TELEMETRY, coord=coord, entity_id="pump-02"),
    ]
    relevant = derive_relevance_labels("motor-01", [], atoms)
    assert atoms[0].atom_id in relevant
    assert atoms[1].atom_id not in relevant


def test_tag_overlap() -> None:
    coord = SpatiotemporalCoord(x=0, y=0, z=0, timestamp=0)
    atoms = [
        ExperienceAtom(
            modality=Modality.TELEMETRY, coord=coord, tags=["maintenance", "motor", "conv"]
        ),
        ExperienceAtom(modality=Modality.TELEMETRY, coord=coord, tags=["unrelated"]),
    ]
    relevant = derive_relevance_labels("", ["maintenance", "motor"], atoms)
    assert atoms[0].atom_id in relevant
    assert atoms[1].atom_id not in relevant
