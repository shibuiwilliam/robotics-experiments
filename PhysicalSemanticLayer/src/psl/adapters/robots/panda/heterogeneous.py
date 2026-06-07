"""Heterogeneous Panda adapter — wraps a schema transform around the base adapter.

This adapter simulates a "different" robot that speaks a different schema
(different units, frame, naming) but is physically the same robot.
The adapter knows the transform and undoes it during to_ir / applies it during from_ir.
"""

from __future__ import annotations

from psl.adapters.robots.panda.adapter import PandaAdapter
from psl.contracts.core import FidelityContract
from psl.ir.core import IRState
from sim.schema_gen.generator import (
    SchemaTransform,
    apply_schema_transform,
    invert_schema_transform,
)


class HeterogeneousPandaAdapter:
    """A Panda adapter with a schema transform applied.

    Wraps the base PandaAdapter, undoing the schema transform on to_ir()
    and applying it on from_ir(). This simulates a robot with a different
    native representation.

    Args:
        entity_id: Unique identifier.
        transform: The schema transform this robot "speaks".
    """

    def __init__(
        self,
        entity_id: str = "panda_hetero",
        transform: SchemaTransform | None = None,
    ) -> None:
        self._entity_id = entity_id
        self._transform = transform or SchemaTransform()
        self._base_adapter = PandaAdapter(entity_id=entity_id)

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def transform(self) -> SchemaTransform:
        return self._transform

    @property
    def fidelity_contract(self) -> FidelityContract:
        base_contract = self._base_adapter.fidelity_contract
        # Heterogeneity adds uncertainty proportional to the transform's noise
        extra_uncertainty = self._transform.sensor_noise_std
        extra_loss = min(0.5, self._transform.sensor_noise_std * 10)
        return FidelityContract(
            adapter_id=f"hetero_panda:{self._entity_id}",
            preserved_fields=base_contract.preserved_fields,
            lost_fields=base_contract.lost_fields
            + (["precision"] if extra_uncertainty > 0 else []),
            uncertainty_delta=base_contract.uncertainty_delta + extra_uncertainty,
            information_loss_estimate=min(
                1.0, base_contract.information_loss_estimate + extra_loss
            ),
            notes=f"Heterogeneous adapter with transform: "
            f"scale={self._transform.unit_scale}, "
            f"rot_z={self._transform.frame_rotation_z_rad:.2f}rad, "
            f"noise={self._transform.sensor_noise_std}",
        )

    def to_ir(self, native_state: dict[str, object]) -> IRState:
        """Convert heterogeneous native state → canonical IR.

        Undoes the schema transform, then delegates to the base adapter.
        """
        canonical_state = invert_schema_transform(native_state, self._transform)
        return self._base_adapter.to_ir(canonical_state)

    def from_ir(self, ir_state: IRState) -> dict[str, object]:
        """Convert canonical IR → heterogeneous native state.

        Gets base native state, then applies the schema transform.
        """
        base_native = self._base_adapter.from_ir(ir_state)
        return apply_schema_transform(base_native, self._transform)
