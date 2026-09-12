import numpy as np
import pytest

from gtwm.grounding.identity import (
    TrackedEntity,
    force_reidentify,
    resolve_identities,
    update_occluded_entities,
)

pytestmark = pytest.mark.unit


def _entity(gt_id: str, xy: tuple[float, float]) -> TrackedEntity:
    return TrackedEntity(
        entity_gt_id=gt_id,
        predicted_xy=np.array(xy),
        appearance=np.zeros(4),
        last_seen_step=0,
    )


def test_resolve_identities_matches_closest_slot() -> None:
    slot_positions = np.array([[0.0, 0.0], [10.0, 10.0]])
    slot_appearance = np.zeros((2, 4))
    entities = [_entity("gt:Pallet_0001", (0.1, 0.1)), _entity("gt:Pallet_0002", (10.1, 9.9))]
    result = resolve_identities(slot_positions, slot_appearance, entities, distance_gate=2.0)
    assert result.slot_to_entity[0] == "gt:Pallet_0001"
    assert result.slot_to_entity[1] == "gt:Pallet_0002"
    assert result.unmatched_slots == []
    assert result.unmatched_entities == []


def test_resolve_identities_gates_out_far_matches() -> None:
    slot_positions = np.array([[0.0, 0.0]])
    slot_appearance = np.zeros((1, 4))
    entities = [_entity("gt:Pallet_0001", (100.0, 100.0))]
    result = resolve_identities(slot_positions, slot_appearance, entities, distance_gate=1.0)
    assert result.slot_to_entity == {}
    assert result.unmatched_slots == [0]
    assert result.unmatched_entities == ["gt:Pallet_0001"]


def test_update_occluded_entities_holds_predicted_position() -> None:
    entities = [_entity("gt:Pallet_0001", (1.0, 1.0))]
    from gtwm.grounding.identity import AssignmentResult

    result = AssignmentResult(unmatched_slots=[], unmatched_entities=["gt:Pallet_0001"])
    predicted_next = {"gt:Pallet_0001": np.array([1.5, 1.5])}
    updated = update_occluded_entities(entities, result, predicted_next, step=1)
    assert updated[0].occluded is True
    assert np.allclose(updated[0].predicted_xy, [1.5, 1.5])


def test_force_reidentify_overrides_position() -> None:
    entities = [_entity("gt:Pallet_0001", (1.0, 1.0))]
    entities[0].occluded = True
    force_reidentify(entities, "gt:Pallet_0001", np.array([9.0, 9.0]))
    assert np.allclose(entities[0].predicted_xy, [9.0, 9.0])
    assert entities[0].occluded is False


def test_force_reidentify_unknown_entity_raises() -> None:
    with pytest.raises(KeyError):
        force_reidentify([], "gt:Pallet_9999", np.array([0.0, 0.0]))
