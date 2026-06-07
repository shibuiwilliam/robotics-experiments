"""Fidelity Contracts — declare what translation preserves and loses.

Each conversion edge (adapter, transform) carries a FidelityContract
declaring what information is preserved, what is lost, and how
uncertainty increases. This enables end-to-end fidelity computation
across multi-hop translation paths.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FidelityContract(BaseModel):
    """Declaration of a translation's fidelity characteristics.

    Fields:
        adapter_id: Which adapter/transform this contract belongs to.
        preserved_fields: Phyte fields that survive losslessly.
        lost_fields: Phyte fields that are dropped or degraded.
        uncertainty_delta: Expected increase in covariance norm per translation.
        information_loss_estimate: Estimated mutual information loss [0, 1].
        notes: Human-readable description of lossy aspects.
    """

    adapter_id: str = Field(description="Adapter or transform identifier")
    preserved_fields: list[str] = Field(
        default_factory=list,
        description="Phyte fields preserved losslessly (e.g., 'value', 'unit')",
    )
    lost_fields: list[str] = Field(
        default_factory=list,
        description="Phyte fields dropped or degraded (e.g., 'covariance')",
    )
    uncertainty_delta: float = Field(
        default=0.0,
        ge=0.0,
        description="Expected covariance norm increase per translation",
    )
    information_loss_estimate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Estimated mutual information loss [0, 1]",
    )
    notes: str = Field(default="", description="Human-readable lossy-aspect description")

    model_config = {"frozen": True}

    def compose(self, other: FidelityContract) -> FidelityContract:
        """Compose two contracts (for multi-hop paths).

        The composed contract accumulates losses and uncertainty.

        Args:
            other: The next contract in the chain.

        Returns:
            A new FidelityContract representing the composed translation.
        """
        # Preserved = intersection; lost = union
        preserved = [f for f in self.preserved_fields if f in other.preserved_fields]
        lost = list(set(self.lost_fields) | set(other.lost_fields))

        return FidelityContract(
            adapter_id=f"{self.adapter_id} -> {other.adapter_id}",
            preserved_fields=preserved,
            lost_fields=lost,
            uncertainty_delta=self.uncertainty_delta + other.uncertainty_delta,
            information_loss_estimate=min(
                1.0,
                self.information_loss_estimate + other.information_loss_estimate,
            ),
            notes=f"Composed: [{self.notes}] then [{other.notes}]",
        )
