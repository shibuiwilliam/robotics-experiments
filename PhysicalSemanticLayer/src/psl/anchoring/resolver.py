"""Document → physical anchoring (mechanism 10, inverse grounding).

Resolves symbolic references from business data (bin names, work orders,
asset IDs) to Phytes grounded in physical frames. This is the "upward"
direction of dual grounding — from documents to physics.

The resolver does NOT access ground truth. It reads from pseudo_cloud
and produces Phytes with appropriate uncertainty reflecting the
anchoring quality.
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np

from psl.phyte.core import Phyte
from psl.phyte.geometry import make_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry


class PhysicalAnchorResolver:
    """Resolves symbolic document references to physical Phytes.

    This is the PSL module for mechanism 10. It takes a symbolic
    reference (e.g. "Bin C") and returns a Phyte with position,
    frame, uncertainty, and provenance tracking the resolution chain.

    Args:
        db_conn: SQLite connection to pseudo-cloud data.
        position_uncertainty_m: Default spatial uncertainty for
            document-derived positions (metres). Documents give
            approximate locations, not precise sensor readings.
    """

    def __init__(
        self,
        db_conn: sqlite3.Connection,
        position_uncertainty_m: float = 0.05,
    ) -> None:
        self._conn = db_conn
        self._pos_uncertainty = position_uncertainty_m

    def resolve_bin(self, bin_id: str, timestamp: float = 0.0) -> Phyte | None:
        """Resolve a bin name to a Phyte with grounded position.

        Args:
            bin_id: Symbolic bin identifier (e.g. "C", "QA_TRAY").
            timestamp: When this resolution was performed.

        Returns:
            Phyte grounded in world frame, or None if bin not found.
            Covariance reflects document-level spatial uncertainty.
        """
        row = self._conn.execute(
            "SELECT frame, position_json, description FROM bin_locations WHERE bin_id = ?",
            (bin_id,),
        ).fetchone()
        if row is None:
            return None

        frame: str = row[0]
        position = np.array(json.loads(row[1]), dtype=np.float64)

        pose = make_se3(np.eye(3), position)
        cov = np.eye(3) * self._pos_uncertainty**2

        prov = Provenance(
            chain=[
                ProvenanceEntry(
                    source="pseudo_cloud:bin_locations",
                    operation=f"resolve_bin({bin_id})",
                    timestamp=timestamp,
                )
            ],
            confidence=0.9,  # Documents are trusted but not sensor-precise
        )

        return Phyte(
            semantic_id=f"bin_location:{bin_id}",
            frame=frame,
            pose=pose,
            timestamp=timestamp,
            clock_domain="wall",
            time_uncertainty=1.0,  # Document data has loose temporal grounding
            unit="m",
            value=position,
            covariance=cov,
            provenance=prov,
        )

    def resolve_work_order(self, wo_id: str, timestamp: float = 0.0) -> dict[str, Phyte | None]:
        """Resolve a work order to grounded source/target Phytes.

        Args:
            wo_id: Work order ID (e.g. "WO-42").
            timestamp: When this resolution was performed.

        Returns:
            Dict with 'source' and 'target' Phyte (or None if not found).
        """
        row = self._conn.execute(
            "SELECT source_bin, target_location FROM work_orders WHERE work_order_id = ?",
            (wo_id,),
        ).fetchone()
        if row is None:
            return {"source": None, "target": None}

        source_bin: str = row[0]
        target_loc: str = row[1]

        source_phyte = self.resolve_bin(source_bin, timestamp)
        target_phyte = self.resolve_bin(target_loc, timestamp)

        # Extend provenance to note work-order origin
        if source_phyte is not None:
            source_phyte = source_phyte.with_provenance(
                source="pseudo_cloud:work_orders",
                operation=f"resolve_wo({wo_id}).source",
                timestamp=timestamp,
                confidence_factor=1.0,
            )
        if target_phyte is not None:
            target_phyte = target_phyte.with_provenance(
                source="pseudo_cloud:work_orders",
                operation=f"resolve_wo({wo_id}).target",
                timestamp=timestamp,
                confidence_factor=1.0,
            )

        return {"source": source_phyte, "target": target_phyte}

    def resolve_item_location(self, item_id: str, timestamp: float = 0.0) -> Phyte | None:
        """Resolve an inventory item to its bin's physical location.

        Args:
            item_id: Inventory item ID.
            timestamp: When this resolution was performed.

        Returns:
            Phyte for the item's bin location, or None.
        """
        row = self._conn.execute(
            "SELECT bin FROM inventory WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None

        bin_id: str = row[0]
        phyte = self.resolve_bin(bin_id, timestamp)
        if phyte is not None:
            phyte = phyte.with_provenance(
                source="pseudo_cloud:inventory",
                operation=f"resolve_item({item_id})->bin({bin_id})",
                timestamp=timestamp,
                confidence_factor=0.95,  # Slight confidence loss for indirection
            )
        return phyte
