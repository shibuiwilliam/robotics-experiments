"""Cloud data adapter: business data ↔ Canonical IR.

Converts between:
  - Native: dict with 'work_orders' (list[dict]), 'inventory' (list[dict]),
    'bin_positions' (dict[str, list[float]]), 'timestamp' (float)
  - IR: IRState with Phytes for bin positions and work order status

Document-derived positions carry larger uncertainty (0.05m) than
sensor-derived positions, reflecting the imprecision of business records.
"""

from __future__ import annotations

import numpy as np

from psl.contracts.core import FidelityContract
from psl.ir.core import IRState
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3, make_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry

# Document-derived position uncertainty (larger than sensor uncertainty)
DOC_POSITION_UNCERTAINTY = 0.05  # meters


class CloudDataAdapter:
    """Adapter for cloud/business data: native ↔ IR.

    Translates business records (work orders, inventory, bin positions)
    into Phytes with spatial grounding and provenance tracking.

    Args:
        entity_id: Unique identifier for this cloud data source.
    """

    def __init__(self, entity_id: str = "pseudo_cloud") -> None:
        self._entity_id = entity_id

    @property
    def entity_id(self) -> str:
        return self._entity_id

    @property
    def fidelity_contract(self) -> FidelityContract:
        return FidelityContract(
            adapter_id=f"cloud_data_adapter:{self._entity_id}",
            preserved_fields=["position", "status", "item_reference", "timestamp"],
            lost_fields=["full_document_text", "formatting", "audit_history"],
            uncertainty_delta=DOC_POSITION_UNCERTAINTY,
            information_loss_estimate=0.3,
            notes="Business data compressed to spatial Phytes. "
            "Document text and formatting lost. Positions from records "
            "carry 0.05m uncertainty (larger than sensor-derived).",
        )

    def to_ir(self, native_state: dict[str, object]) -> IRState:
        """Convert business data to canonical IR.

        Args:
            native_state: Dict with keys:
                'bin_positions': dict mapping bin_id → [x, y, z] in meters
                'work_orders': list of dicts with 'id', 'status', 'item_id', etc.
                'inventory': list of dicts with 'item_id', 'name', 'bin', 'quantity'
                'timestamp': float (wall clock time or sim time)

        Returns:
            IRState with Phytes for bins and work orders.
        """
        timestamp = float(native_state.get("timestamp", 0.0))  # type: ignore[arg-type]
        bin_positions = native_state.get("bin_positions", {})
        work_orders = native_state.get("work_orders", [])

        base_prov = Provenance(
            chain=[
                ProvenanceEntry(
                    source=f"cloud_db:{self._entity_id}",
                    operation="read_business_data",
                    timestamp=timestamp,
                )
            ],
            confidence=0.85,
        )

        phytes: dict[str, Phyte] = {}

        # Bin positions → spatial Phytes
        if isinstance(bin_positions, dict):
            for bin_id, pos in bin_positions.items():
                pos_arr = np.asarray(pos, dtype=np.float64)
                if pos_arr.shape == (3,):
                    R = np.eye(3)
                    pose = make_se3(R, pos_arr)
                    phytes[f"bin:{bin_id}"] = Phyte(
                        semantic_id=f"bin:{bin_id}",
                        frame="world",
                        pose=pose,
                        timestamp=timestamp,
                        clock_domain="wall",
                        unit="m",
                        value=pos_arr,
                        covariance=np.eye(3) * DOC_POSITION_UNCERTAINTY**2,
                        provenance=base_prov,
                    )

        # Work orders → status Phytes
        if isinstance(work_orders, list):
            for wo in work_orders:
                if not isinstance(wo, dict):
                    continue
                wo_id = str(wo.get("id", "unknown"))
                status = str(wo.get("status", "unknown"))
                # Encode status as numeric: pending=0, in_progress=1, completed=2, failed=3, cancelled=4
                status_map = {
                    "pending": 0,
                    "in_progress": 1,
                    "completed": 2,
                    "failed": 3,
                    "cancelled": 4,
                }
                status_val = float(status_map.get(status, -1))
                priority = float(wo.get("priority", 0))

                phytes[f"work_order:{wo_id}"] = Phyte(
                    semantic_id=f"work_order:{wo_id}",
                    frame="world",
                    pose=identity_se3(),
                    timestamp=timestamp,
                    clock_domain="wall",
                    unit="dimensionless",
                    value=np.array([status_val, priority]),
                    covariance=np.eye(2) * 0.01,
                    provenance=base_prov.extend(
                        source=f"work_order:{wo_id}",
                        operation="encode_status",
                        timestamp=timestamp,
                    ),
                )

        return IRState(
            entity_id=self._entity_id,
            phytes=phytes,
            timestamp=timestamp,
            clock_domain="wall",
        )

    def from_ir(self, ir_state: IRState) -> dict[str, object]:
        """Convert IR back to business-consumable format.

        Args:
            ir_state: IRState with cloud Phytes.

        Returns:
            Dict with 'bin_positions', 'work_order_statuses', 'timestamp'.
        """
        bin_positions: dict[str, list[float]] = {}
        wo_statuses: dict[str, str] = {}
        status_rmap = {0: "pending", 1: "in_progress", 2: "completed", 3: "failed", 4: "cancelled"}

        for name, phyte in ir_state.phytes.items():
            if name.startswith("bin:"):
                bin_id = name[4:]
                bin_positions[bin_id] = phyte.value.tolist()
            elif name.startswith("work_order:"):
                wo_id = name[11:]
                status_val = int(phyte.value[0])
                wo_statuses[wo_id] = status_rmap.get(status_val, "unknown")

        return {
            "bin_positions": bin_positions,
            "work_order_statuses": wo_statuses,
            "timestamp": ir_state.timestamp,
        }
