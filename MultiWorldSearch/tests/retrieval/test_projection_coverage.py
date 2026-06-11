"""Tests for consumer-aware projection coverage — all 5 types produce different outputs."""

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
        structured_fields={"equipment_id": "CONV-01", "value": 85.0},
    )
    atom.provenance.add("sensor-01", "sensor", "created")
    return atom


def test_all_five_projections_are_structurally_different() -> None:
    """Each consumer type produces a dict with different keys."""
    atom = _make_atom()
    projections = {}
    for ct in ConsumerType:
        projections[ct] = project_for_consumer(atom, ct)

    # All should have the base keys
    for ct, proj in projections.items():
        assert "atom_id" in proj, f"{ct} missing atom_id"
        assert "modality" in proj, f"{ct} missing modality"

    # Each should have unique keys not in others
    vla_keys = set(projections[ConsumerType.VLA].keys())
    llm_keys = set(projections[ConsumerType.LLM].keys())
    control_keys = set(projections[ConsumerType.CONTROL_LOOP].keys())
    dashboard_keys = set(projections[ConsumerType.DASHBOARD].keys())
    audit_keys = set(projections[ConsumerType.AUDIT].keys())

    # VLA has "payload", LLM has "citation", AUDIT has "provenance"
    assert "payload" in vla_keys
    assert "citation" in llm_keys
    assert "provenance" in audit_keys
    assert "freshness" in dashboard_keys
    assert "values" in control_keys

    # They should NOT all be identical
    assert vla_keys != llm_keys
    assert llm_keys != audit_keys
    assert control_keys != dashboard_keys


def test_vla_projection_has_position() -> None:
    atom = _make_atom()
    proj = project_for_consumer(atom, ConsumerType.VLA)
    assert "position" in proj


def test_control_loop_projection_is_minimal() -> None:
    atom = _make_atom()
    proj = project_for_consumer(atom, ConsumerType.CONTROL_LOOP)
    assert "values" in proj
    assert "text" not in proj  # control loop is low-overhead


def test_audit_projection_has_full_provenance() -> None:
    atom = _make_atom()
    proj = project_for_consumer(atom, ConsumerType.AUDIT)
    assert "provenance" in proj
    assert len(proj["provenance"]) == 1
    assert "access" in proj
