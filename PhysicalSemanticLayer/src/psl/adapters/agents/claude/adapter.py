"""Claude Agent adapter: agent native state ↔ Canonical IR.

Converts between:
  - Native: dict with 'task_plan' (str), 'decision' (str),
    'confidence' (float), 'referenced_entities' (list[str]),
    'timestamp' (float), 'tool_calls' (list[dict])
  - IR: IRState with Phytes for plan, decision, and entity references

Agent operates at the semantic level — significant compression from
sensor/actuator space to plan/decision space. The fidelity contract
declares this information loss explicitly.

Encoding: deterministic hash-based projection of text to unit-norm
vectors in R^d, enabling geometric operations on semantic content
while preserving reproducibility (§10 determinism).
"""

from __future__ import annotations

import hashlib

import numpy as np
from numpy.typing import NDArray

from psl.contracts.core import FidelityContract
from psl.ir.core import IRState
from psl.phyte.core import Phyte
from psl.phyte.geometry import identity_se3
from psl.phyte.provenance import Provenance, ProvenanceEntry


class ClaudeAgentAdapter:
    """Adapter for Claude Agent: agent native state ↔ IR.

    Implements the Adapter protocol (to_ir / from_ir).
    Agent state is inherently semantic — task plans and decisions
    are projected to fixed-dimensional vectors via deterministic
    hash-based encoding. This is intentionally lossy: the adapter
    operates at the plan/decision level, not the sensor level.

    Args:
        entity_id: Unique identifier for this agent instance.
    """

    def __init__(self, entity_id: str = "claude_agent") -> None:
        self._entity_id = entity_id

    @property
    def entity_id(self) -> str:
        """Unique entity identifier for this adapter."""
        return self._entity_id

    @property
    def fidelity_contract(self) -> FidelityContract:
        """Contract for native ↔ IR translation.

        Agent translation is significantly lossy: raw sensor values,
        joint-level state, and detailed covariance structure are not
        preserved. The agent operates at the semantic level.
        """
        return FidelityContract(
            adapter_id=f"claude_agent_adapter:{self._entity_id}",
            preserved_fields=[
                "semantic_id",
                "timestamp",
                "confidence",
                "referenced_entities",
            ],
            lost_fields=[
                "raw_sensor_values",
                "joint_positions",
                "covariance_details",
            ],
            uncertainty_delta=0.1,
            information_loss_estimate=0.4,
            notes=(
                "Agent operates at semantic level: significant compression "
                "from sensor to plan/decision space."
            ),
        )

    def _text_to_vector(self, text: str, dim: int) -> NDArray[np.float64]:
        """Deterministic hash-based projection of text to a unit-norm vector.

        Uses SHA-256 hash of the text as a numpy seed, then generates
        a standard-normal vector and normalizes to the unit sphere.
        This is a fixed, reproducible embedding — not learned.

        Args:
            text: Input text to encode.
            dim: Target dimensionality of the output vector.

        Returns:
            Unit-norm vector in R^dim (shape (dim,)).
        """
        hash_bytes = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(hash_bytes[:4], byteorder="big")
        rng = np.random.Generator(np.random.PCG64(seed))
        vec = rng.standard_normal(dim)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def to_ir(self, native_state: dict[str, object]) -> IRState:
        """Convert agent native state to canonical IR.

        Args:
            native_state: Dict with keys:
                'task_plan': str — the agent's current plan
                'decision': str — the agent's current decision
                'confidence': float — agent's confidence [0, 1]
                'referenced_entities': list[str] — entity IDs referenced
                'timestamp': float — timestamp in agent clock domain
                'tool_calls': list[dict] — tool calls made by agent

        Returns:
            IRState with Phytes for plan, decision, and entity references.
            All in world frame with agent clock domain.
        """
        task_plan = str(native_state["task_plan"])
        decision = str(native_state["decision"])
        confidence = float(native_state["confidence"])  # type: ignore[arg-type]
        raw_refs = native_state["referenced_entities"]
        referenced_entities: list[str] = (
            list(raw_refs) if isinstance(raw_refs, (list, tuple)) else []
        )
        timestamp = float(native_state["timestamp"])  # type: ignore[arg-type]

        base_prov = Provenance(
            chain=[
                ProvenanceEntry(
                    source=f"claude_agent:{self._entity_id}",
                    operation="plan",
                    timestamp=timestamp,
                )
            ],
            confidence=confidence,
        )

        phytes: dict[str, Phyte] = {}

        # Task plan phyte — 64-dim hash projection
        plan_vec = self._text_to_vector(task_plan, 64)
        phytes["task_plan"] = Phyte(
            semantic_id="task_plan",
            frame="world",
            pose=identity_se3(),
            timestamp=timestamp,
            clock_domain="agent",
            unit="dimensionless",
            value=plan_vec,
            covariance=np.eye(64) * 0.1,
            provenance=base_prov,
        )

        # Decision phyte — 64-dim hash projection
        decision_vec = self._text_to_vector(decision, 64)
        decision_prov = Provenance(
            chain=[
                ProvenanceEntry(
                    source=f"claude_agent:{self._entity_id}",
                    operation="decide",
                    timestamp=timestamp,
                )
            ],
            confidence=confidence,
        )
        phytes["decision"] = Phyte(
            semantic_id="decision",
            frame="world",
            pose=identity_se3(),
            timestamp=timestamp,
            clock_domain="agent",
            unit="dimensionless",
            value=decision_vec,
            covariance=np.eye(64) * 0.1,
            provenance=decision_prov,
        )

        # Referenced entity phytes — 16-dim hash projection each
        for entity_name in referenced_entities:
            ref_vec = self._text_to_vector(entity_name, 16)
            ref_prov = Provenance(
                chain=[
                    ProvenanceEntry(
                        source=f"claude_agent:{self._entity_id}",
                        operation="reference",
                        timestamp=timestamp,
                    )
                ],
                confidence=confidence,
            )
            phytes[f"agent_reference:{entity_name}"] = Phyte(
                semantic_id=f"agent_reference:{entity_name}",
                frame="world",
                pose=identity_se3(),
                timestamp=timestamp,
                clock_domain="agent",
                unit="dimensionless",
                value=ref_vec,
                covariance=np.eye(16) * 0.05,
                provenance=ref_prov,
            )

        return IRState(
            entity_id=self._entity_id,
            phytes=phytes,
            timestamp=timestamp,
            clock_domain="agent",
        )

    def from_ir(self, ir_state: IRState) -> dict[str, object]:
        """Convert canonical IR back to agent-consumable format.

        This is intentionally lossy — the agent operates at the semantic
        level and does not need full sensor-level reconstruction.

        Args:
            ir_state: Canonical IRState.

        Returns:
            Dict with:
                'summary': human-readable string of semantic IDs
                'entity_states': dict of entity → position/confidence/staleness
                'timestamp': canonical timestamp
        """
        semantic_ids = list(ir_state.phytes.keys())
        summary = ", ".join(semantic_ids)

        entity_states: dict[str, dict[str, object]] = {}
        for phyte_key, phyte in ir_state.phytes.items():
            position = phyte.value[:3].copy() if len(phyte.value) >= 3 else np.zeros(3)
            entity_states[phyte_key] = {
                "position": position,
                "confidence": phyte.provenance.confidence,
                "staleness": 0.0,
            }

        return {
            "summary": summary,
            "entity_states": entity_states,
            "timestamp": ir_state.timestamp,
        }
