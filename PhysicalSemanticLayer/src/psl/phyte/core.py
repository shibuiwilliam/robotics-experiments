"""Phyte — the canonical self-describing physical-semantic data unit.

A Phyte carries:
  - semantic_id: ontology link (what this quantity *means*)
  - frame + pose: spatial grounding in SE(3)
  - timestamp + clock_domain + time_uncertainty: temporal grounding
  - unit + dimensionality: physical dimensions
  - value: the numeric payload
  - covariance: uncertainty (shape depends on value dimensionality)
  - provenance: full source chain + confidence

No bare floats — every physical quantity travels as a Phyte.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field, model_validator

from psl.phyte.geometry import Mat4, identity_se3
from psl.phyte.provenance import Provenance
from psl.phyte.units import dimensionality_str, parse_unit


class Phyte(BaseModel):
    """Physical-semantic grounded data unit.

    All physical data flowing through PSL must be wrapped in a Phyte.
    Validators ensure dimensional consistency at construction time.

    Fields:
        semantic_id: Ontology-linked identifier (e.g. 'joint_position', 'ee_pose').
        frame: Name of the reference frame this quantity is expressed in.
        pose: 4×4 SE(3) homogeneous matrix — pose in *frame*.
        timestamp: Time value in the clock domain.
        clock_domain: Identifier for the clock source (e.g. 'sim', 'wall').
        time_uncertainty: Uncertainty on timestamp (seconds, ≥ 0).
        unit: Unit string (parsed via pint, e.g. 'rad', 'm', 'N*m').
        dimensionality: Derived from unit (e.g. '[length]'); auto-populated.
        value: The numeric payload — scalar or array.
        covariance: Covariance matrix matching value shape.
        provenance: Source chain and confidence.
    """

    semantic_id: str = Field(description="Ontology-linked meaning identifier")
    frame: str = Field(description="Reference frame name")
    pose: NDArray[np.float64] = Field(
        default_factory=identity_se3, description="4×4 SE(3) pose in frame"
    )
    timestamp: float = Field(description="Time in clock_domain")
    clock_domain: str = Field(default="sim", description="Clock source identifier")
    time_uncertainty: float = Field(default=0.0, ge=0.0, description="Time uncertainty (s)")
    unit: str = Field(description="Physical unit (pint-parseable)")
    dimensionality: str = Field(default="", description="Auto-derived from unit")
    value: NDArray[np.float64] = Field(description="Numeric payload")
    covariance: NDArray[np.float64] = Field(description="Covariance matrix")
    provenance: Provenance = Field(default_factory=Provenance)

    model_config = {
        "arbitrary_types_allowed": True,
        "frozen": True,
    }

    @model_validator(mode="before")
    @classmethod
    def _validate_and_derive(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Validate unit, derive dimensionality, check covariance shape."""
        # Ensure value and covariance are numpy arrays
        if "value" in data and not isinstance(data["value"], np.ndarray):
            data["value"] = np.atleast_1d(np.asarray(data["value"], dtype=np.float64))
        if "covariance" in data and not isinstance(data["covariance"], np.ndarray):
            data["covariance"] = np.asarray(data["covariance"], dtype=np.float64)

        # Validate unit string and derive dimensionality
        if "unit" in data:
            unit_obj = parse_unit(data["unit"])
            data["dimensionality"] = dimensionality_str(unit_obj)

        # Validate pose shape
        if "pose" in data:
            pose = np.asarray(data["pose"], dtype=np.float64)
            if pose.shape != (4, 4):
                msg = f"pose must be 4×4, got {pose.shape}"
                raise ValueError(msg)
            data["pose"] = pose

        # Validate covariance shape vs value shape
        if "value" in data and "covariance" in data:
            val = np.atleast_1d(np.asarray(data["value"]))
            cov = np.asarray(data["covariance"])
            n = val.shape[0]
            if cov.shape == ():
                # Scalar covariance — expand to (1, 1) for scalar value
                if n == 1:
                    data["covariance"] = np.array([[float(cov)]])
                else:
                    msg = f"Scalar covariance incompatible with value shape ({n},)"
                    raise ValueError(msg)
            elif cov.shape != (n, n):
                msg = f"Covariance shape {cov.shape} does not match value dim ({n},)"
                raise ValueError(msg)

        return data

    def with_frame(self, new_frame: str, new_pose: Mat4) -> Phyte:
        """Return a copy in a new reference frame with updated pose."""
        return self.model_copy(update={"frame": new_frame, "pose": new_pose})

    def with_provenance(
        self,
        source: str,
        operation: str,
        timestamp: float,
        confidence_factor: float = 1.0,
    ) -> Phyte:
        """Return a copy with an extended provenance chain."""
        new_prov = self.provenance.extend(source, operation, timestamp, confidence_factor)
        return self.model_copy(update={"provenance": new_prov})

    def with_value(
        self,
        new_value: NDArray[np.float64],
        new_covariance: NDArray[np.float64],
    ) -> Phyte:
        """Return a copy with updated value and covariance."""
        return self.model_copy(update={"value": new_value, "covariance": new_covariance})
