"""Scenario 6: Counterfactual Simulation for Safety Decisions.

Risk reduction — avoid accidents by simulating before acting.
Hypothesis: H6 (curiosity loop reduces failures).
World: Hazardous configuration — stacked cargo that could topple, pressurized valve.
Business: SOP risk limits, equipment specs with safe operating parameters.
Inject: Hazardous config + sim fork/rollout mechanism.
Actors: SafetyAgent/VLA queries MWS with "is this action safe?"
Query: VLA -> intent="is this operation safe?", indices={semantic,structured}+counterfactual(sim),
       projection=numeric+provenance.
Success: Rollout predicts collapse -> avoids unsafe action; result atoms stored & retrievable.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.logging import get_logger
from mws.core.types import ConsumerType, Modality
from mws.retrieval.query import RetrievalQuery
from mws.scenarios.base import BaseScenario
from mws.scenarios.registry import register_scenario
from mws.scenarios.s6_counterfactual_safety.data import generate_s6_sop_atoms
from mws.scenarios.s6_counterfactual_safety.world import SCENARIO6_XML
from mws.scenarios.safety_rules import (
    DEFAULT_RISK_SENSITIVITY,
    DEFAULT_RISK_THRESHOLD,
    assess_risk,
)
from mws.sim.world import MuJoCoWorld

logger = get_logger(__name__)

# Scenario defaults (overridable via config). The hazard values exceed the SOP
# limits by default, so the computed risk is high and the agent AVOIDs — but
# loosen a limit (or lower an observed value) and the verdict flips to PROCEED.
DEFAULTS: dict[str, float] = {
    "max_stack_height": 2,
    "max_pressure_psi": 100,
    "current_stack_height": 3,
    "current_pressure_psi": 120,
    "risk_threshold": DEFAULT_RISK_THRESHOLD,
    "risk_sensitivity": DEFAULT_RISK_SENSITIVITY,
}


@register_scenario
class CounterfactualSafetyScenario(BaseScenario):
    name = "counterfactual_safety"
    description = "Counterfactual simulation for safety decisions"

    def __init__(self) -> None:
        super().__init__()
        self._world: MuJoCoWorld | None = None
        self._hazard_atoms: list[ExperienceAtom] = []
        self._counterfactual_atoms: list[ExperienceAtom] = []
        self._collapse_avoided: bool = False
        self._safety_decisions: list[dict[str, Any]] = []
        # Tunables (populated in setup)
        self._max_stack_height: int = int(DEFAULTS["max_stack_height"])
        self._max_pressure_psi: int = int(DEFAULTS["max_pressure_psi"])
        self._current_stack_height: int = int(DEFAULTS["current_stack_height"])
        self._current_pressure_psi: int = int(DEFAULTS["current_pressure_psi"])
        self._risk_threshold: float = DEFAULTS["risk_threshold"]
        self._risk_sensitivity: float = DEFAULTS["risk_sensitivity"]

    def setup(self, seed: int, config: dict[str, Any]) -> None:
        """Phase 1: Build MuJoCo world with stacked cargo and pressurized valve."""
        self._max_stack_height = int(config.get("max_stack_height", DEFAULTS["max_stack_height"]))
        self._max_pressure_psi = int(config.get("max_pressure_psi", DEFAULTS["max_pressure_psi"]))
        self._current_stack_height = int(
            config.get("current_stack_height", DEFAULTS["current_stack_height"])
        )
        self._current_pressure_psi = int(
            config.get("current_pressure_psi", DEFAULTS["current_pressure_psi"])
        )
        self._risk_threshold = float(config.get("risk_threshold", DEFAULTS["risk_threshold"]))
        self._risk_sensitivity = float(config.get("risk_sensitivity", DEFAULTS["risk_sensitivity"]))

        self._world = MuJoCoWorld(xml_path="__inline__", seed=seed)
        import mujoco

        self._world._model = mujoco.MjModel.from_xml_string(SCENARIO6_XML)
        self._world._data = mujoco.MjData(self._world._model)
        self._init_run(seed, config, world=self._world, world_id="safety_workshop")
        assert self.audit is not None
        self.audit.log(
            "setup",
            "system",
            "world_created",
            details={"world": "hazardous_warehouse"},
        )

    def seed_memory(self) -> None:
        """Phase 2: Ingest SOP with risk limits and equipment specs."""
        assert self.audit is not None

        sop_atoms = generate_s6_sop_atoms(
            seed=self.seed,
            max_stack_height=self._max_stack_height,
            max_pressure_psi=self._max_pressure_psi,
        )
        self._ingest_atoms(sop_atoms)
        self.audit.log(
            "seed_memory",
            "system",
            "sop_and_specs_ingested",
            details={
                "n_atoms": len(sop_atoms),
                "types": ["sop", "equipment_spec"],
            },
        )

    def inject(self) -> None:
        """Phase 3: Inject hazardous configuration atoms.

        - Cargo stacked 3-high (above 2-box limit)
        - Valve at 120 PSI (above 100 PSI limit)
        """
        assert self.audit is not None

        # Hazardous cargo stack observation
        cargo_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=1.0,
                y=1.0,
                z=0.75,
                timestamp=1700001000.0,
                world_id="hazardous_warehouse",
            ),
            text_summary=(
                f"HAZARD: Cargo stack at (1,1) has {self._current_stack_height} boxes stacked. "
                f"Maximum safe stack height is {self._max_stack_height} boxes. "
                "Topple risk to be assessed by counterfactual rollout."
            ),
            entity_id="cargo_stack_A",
            tags=["hazard", "cargo", "stacking", "topple_risk", "safety"],
            structured_fields={
                "hazard_type": "excessive_stack",
                "current_stack_height": self._current_stack_height,
                "max_safe_height": self._max_stack_height,
            },
        )
        cargo_atom.provenance.add("safety_agent:vision", "sensor", "created")
        self._ingest_atom(cargo_atom)
        self._hazard_atoms.append(cargo_atom)

        # Hazardous valve pressure observation
        valve_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=3.0,
                y=2.0,
                z=0.4,
                timestamp=1700001000.0,
                world_id="hazardous_warehouse",
            ),
            text_summary=(
                f"HAZARD: Pressurized valve PV-01 reading {self._current_pressure_psi} PSI. "
                f"Maximum safe operating pressure is {self._max_pressure_psi} PSI. "
                "Overpressure risk to be assessed by counterfactual rollout."
            ),
            entity_id="PV-01",
            tags=["hazard", "valve", "pressure", "overpressure", "safety"],
            structured_fields={
                "hazard_type": "overpressure",
                "equipment_id": "PV-01",
                "current_pressure_psi": self._current_pressure_psi,
                "max_safe_pressure_psi": self._max_pressure_psi,
            },
        )
        valve_atom.provenance.add("safety_agent:pressure_gauge", "sensor", "created")
        self._ingest_atom(valve_atom)
        self._hazard_atoms.append(valve_atom)

        self.audit.log(
            "inject",
            "safety_agent",
            "hazards_detected",
            details={
                "hazards": ["cargo_stack_3_high", "valve_120_psi"],
                "n_hazard_atoms": len(self._hazard_atoms),
            },
        )

    def perceive(self) -> None:
        """Phase 4: Generate body position atoms from MuJoCo."""
        assert self._world is not None
        assert self.audit is not None

        self._world.step(100)
        pose_atoms: list[ExperienceAtom] = []
        for name, pos in self._world.get_body_positions().items():
            if not name:
                continue
            coord = SpatiotemporalCoord(
                x=float(pos[0]),
                y=float(pos[1]),
                z=float(pos[2]),
                timestamp=self._world.time,
                world_id="hazardous_warehouse",
            )
            atom = ExperienceAtom(
                modality=Modality.POSE,
                coord=coord,
                text_summary=f"Body '{name}' observed at ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})",
                entity_id=name,
                tags=["sim", "pose", name],
                structured_fields={"body_name": name},
            )
            atom.provenance.add(f"mujoco:hazardous_warehouse:{name}", "sensor", "created")
            pose_atoms.append(atom)
        self._ingest_atoms(pose_atoms)  # batched embedding requests (M17)

        self.audit.log(
            "perceive",
            "sim",
            "observations_generated",
            details={"n_bodies": len(self._world.get_body_positions())},
        )

    def act(self) -> None:
        """Phase 6: SafetyAgent queries MWS, runs counterfactual rollouts, decides.

        1. Propose action: "move top box from stack"
        2. Query MWS for safety assessment including SOP limits
        3. Counterfactual rollout for cargo: predicts collapse
        4. Counterfactual rollout for valve: predicts pressure release risk
        5. Decision: avoid unsafe cargo action, recommend pressure reduction for valve
        6. Store counterfactual result atoms into MWS
        """
        assert self.engine is not None
        assert self.audit is not None

        rng = np.random.default_rng(self.seed)

        # --- Step 1: SafetyAgent proposes action ---
        proposed_action = "move top box from cargo stack at (1,1)"
        self.audit.log(
            "act",
            "safety_agent",
            "propose_action",
            details={"proposed_action": proposed_action},
        )

        # --- Step 2: Query MWS for safety assessment ---
        safety_query = RetrievalQuery(
            text=("cargo stack safety limit stacking risk topple hazard SOP maximum height boxes"),
            tags=["safety", "cargo", "stacking", "sop", "hazard"],
            structured_filters={"hazard_type": "excessive_stack"},
            consumer=ConsumerType.CONTROL_LOOP,  # numeric risk values for safety decisions
            top_k=10,
        )
        safety_results = self.engine.search(safety_query)
        safety_projected = self.engine.project(safety_results, safety_query)
        self.audit.log(
            "act",
            "safety_agent",
            "safety_query",
            entity_id="cargo_stack_A",
            indices=["semantic", "structured", "counterfactual"],
            projection="numeric+provenance",
            details={
                "n_results": len(safety_results),
                "query": safety_query.text[:60],
            },
        )

        # Track cost
        self.cost.record_llm_call(
            input_tokens=max(1, len(str(safety_projected)) // 4),
            output_tokens=50,
        )
        self.bandwidth.record_llm_request(
            prompt_chars=len(str(safety_projected)),
            response_chars=200,
        )

        # --- Step 3: Counterfactual rollout for cargo (RULE-BASED risk) ---
        # The SOP limit is read back from the retrieved atoms; the risk score is
        # COMPUTED from how far the observed stack exceeds that limit, and the
        # decision follows from risk vs threshold. No literal risk_score.
        cargo_limit = self._retrieve_limit(safety_results, "max_stack_height", "max_safe_height")
        if cargo_limit is None:
            cargo_limit = float(self._max_stack_height)
        cargo_risk = assess_risk(
            float(self._current_stack_height),
            cargo_limit,
            sensitivity=self._risk_sensitivity,
            threshold=self._risk_threshold,
        )
        cargo_safe = not cargo_risk.exceeds_limit
        cargo_outcome = "collapse_predicted" if cargo_risk.decision == "AVOID" else "stable"
        cargo_cf_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=1.0,
                y=1.0,
                z=0.75,
                timestamp=1700001100.0,
                world_id="hazardous_warehouse",
            ),
            text_summary=(
                f"COUNTERFACTUAL ROLLOUT: Simulated moving top box from "
                f"{self._current_stack_height}-high stack (SOP limit {cargo_limit:.0f}). "
                f"Computed risk {cargo_risk.risk_score:.2f} → "
                f"{'collapse predicted, UNSAFE' if cargo_risk.decision == 'AVOID' else 'within limit, stable'}."
            ),
            entity_id="cargo_stack_A",
            tags=[
                "counterfactual",
                "rollout",
                "cargo",
                "collapse" if cargo_risk.decision == "AVOID" else "stable",
                "safety",
            ],
            structured_fields={
                "rollout_type": "counterfactual",
                "action_proposed": "move_top_box",
                "outcome": cargo_outcome,
                "current_stack_height": self._current_stack_height,
                "max_safe_height": cargo_limit,
                "risk_score": round(cargo_risk.risk_score, 4),
                "sop_violated": "SOP-CARGO-STACK" if cargo_risk.exceeds_limit else "",
                "safe": cargo_safe,
            },
            payload={
                "sim_trajectory": rng.standard_normal((5, 3)).tolist(),
                # Rule-based, computed from limit exceedance (see safety_rules).
                # NOT a literal and NOT a Monte-Carlo frequency.
                "risk_score": cargo_risk.risk_score,
            },
        )
        cargo_cf_atom.provenance.add(
            "safety_agent:sim_fork", "agent", "created", {"method": "counterfactual_rollout"}
        )
        self._ingest_atom(cargo_cf_atom)
        self._counterfactual_atoms.append(cargo_cf_atom)

        self.audit.log(
            "act",
            "safety_agent",
            "counterfactual_cargo",
            entity_id="cargo_stack_A",
            details={
                "outcome": cargo_outcome,
                "risk_score": round(cargo_risk.risk_score, 4),
                "limit": cargo_limit,
                "observed": self._current_stack_height,
                "safe": cargo_safe,
            },
        )

        # --- Step 4: Counterfactual rollout for valve ---
        valve_query = RetrievalQuery(
            text=(
                "valve pressure safety limit overpressure hazard SOP maximum operating pressure PSI"
            ),
            tags=["safety", "valve", "pressure", "sop", "hazard"],
            structured_filters={"equipment_id": "PV-01"},
            consumer=ConsumerType.VLA,
            top_k=10,
        )
        valve_results = self.engine.search(valve_query)
        self.engine.project(valve_results, valve_query)
        self.audit.log(
            "act",
            "safety_agent",
            "valve_safety_query",
            entity_id="PV-01",
            indices=["semantic", "structured", "counterfactual"],
            projection="numeric+provenance",
            details={"n_results": len(valve_results)},
        )

        valve_limit = self._retrieve_limit(
            valve_results, "max_pressure_psi", "max_safe_pressure_psi"
        )
        if valve_limit is None:
            valve_limit = float(self._max_pressure_psi)
        valve_risk = assess_risk(
            float(self._current_pressure_psi),
            valve_limit,
            sensitivity=self._risk_sensitivity,
            threshold=self._risk_threshold,
        )
        valve_safe = not valve_risk.exceeds_limit
        valve_outcome = (
            "pressure_release_predicted" if valve_risk.decision == "AVOID" else "within_limit"
        )
        valve_cf_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=3.0,
                y=2.0,
                z=0.4,
                timestamp=1700001100.0,
                world_id="hazardous_warehouse",
            ),
            text_summary=(
                f"COUNTERFACTUAL ROLLOUT: Simulated operating valve PV-01 at "
                f"{self._current_pressure_psi} PSI (SOP limit {valve_limit:.0f}). "
                f"Computed risk {valve_risk.risk_score:.2f} → "
                f"{'pressure release predicted, UNSAFE' if valve_risk.decision == 'AVOID' else 'within limit'}."
            ),
            entity_id="PV-01",
            tags=[
                "counterfactual",
                "rollout",
                "valve",
                "pressure_release" if valve_risk.decision == "AVOID" else "within_limit",
                "safety",
            ],
            structured_fields={
                "rollout_type": "counterfactual",
                "action_proposed": "operate_valve",
                "outcome": valve_outcome,
                "current_pressure_psi": self._current_pressure_psi,
                "max_safe_pressure_psi": valve_limit,
                "risk_score": round(valve_risk.risk_score, 4),
                "sop_violated": "SOP-VALVE-PRESSURE" if valve_risk.exceeds_limit else "",
                "safe": valve_safe,
            },
            payload={
                "sim_pressure_curve": rng.standard_normal(10).tolist(),
                # Rule-based, computed from limit exceedance (see safety_rules).
                "risk_score": valve_risk.risk_score,
            },
        )
        valve_cf_atom.provenance.add(
            "safety_agent:sim_fork", "agent", "created", {"method": "counterfactual_rollout"}
        )
        self._ingest_atom(valve_cf_atom)
        self._counterfactual_atoms.append(valve_cf_atom)

        self.audit.log(
            "act",
            "safety_agent",
            "counterfactual_valve",
            entity_id="PV-01",
            details={
                "outcome": valve_outcome,
                "risk_score": round(valve_risk.risk_score, 4),
                "limit": valve_limit,
                "observed": self._current_pressure_psi,
                "safe": valve_safe,
            },
        )

        # --- Step 5: Safety decisions (DERIVED from computed risk) ---
        # Decision 1: cargo — AVOID iff the computed risk crossed threshold.
        cargo_decision = {
            "action": "move_top_box",
            "target": "cargo_stack_A",
            "decision": cargo_risk.decision,
            "risk_score": round(cargo_risk.risk_score, 4),
            "reason": (
                f"Counterfactual risk {cargo_risk.risk_score:.2f} "
                f"{'>=' if cargo_risk.decision == 'AVOID' else '<'} threshold "
                f"{self._risk_threshold:.2f}; stack {self._current_stack_height} vs "
                f"limit {cargo_limit:.0f}."
            ),
            "alternative": "Secure lower boxes first, then remove top box with support.",
        }
        self._safety_decisions.append(cargo_decision)
        # Collapse is avoided only when the agent actually chose to AVOID.
        self._collapse_avoided = cargo_risk.decision == "AVOID"

        self.audit.log(
            "act",
            "safety_agent",
            "decision_cargo",
            entity_id="cargo_stack_A",
            details=cargo_decision,
        )

        # Decision 2: valve — AVOID iff the computed risk crossed threshold.
        valve_decision = {
            "action": "operate_valve",
            "target": "PV-01",
            "decision": valve_risk.decision,
            "risk_score": round(valve_risk.risk_score, 4),
            "reason": (
                f"Counterfactual risk {valve_risk.risk_score:.2f} "
                f"{'>=' if valve_risk.decision == 'AVOID' else '<'} threshold "
                f"{self._risk_threshold:.2f}; pressure {self._current_pressure_psi} vs "
                f"limit {valve_limit:.0f}."
            ),
            "alternative": "Initiate controlled pressure reduction below the limit first.",
        }
        self._safety_decisions.append(valve_decision)

        self.audit.log(
            "act",
            "safety_agent",
            "decision_valve",
            entity_id="PV-01",
            details=valve_decision,
        )

        # Track cost for counterfactual reasoning
        self.cost.record_llm_call(input_tokens=200, output_tokens=100)
        self.bandwidth.record_llm_request(prompt_chars=800, response_chars=400)

    def _retrieve_limit(
        self,
        results: list[Any],
        *field_names: str,
    ) -> float | None:
        """Read an SOP/spec limit back from retrieved atoms (pipeline output).

        Scans the retrieved atoms' structured fields for the first matching
        limit field. Returns None if no retrieved atom carries the limit, so a
        broken retrieval surfaces as a missing limit rather than a silent
        constant.
        """
        assert self.engine is not None
        for r in results:
            atom = self.engine.get_atom(r.atom_id)
            if atom is None:
                continue
            for field_name in field_names:
                if field_name in atom.structured_fields:
                    return float(atom.structured_fields[field_name])
        return None

    def evaluate(self) -> dict[str, Any]:
        """Phase 7: Compute metrics.

        - collapse_avoidance: True if unsafe cargo action was avoided
        - counterfactual_atoms_stored: count of result atoms stored
        - counterfactual_atoms_retrievable: verify retrieval by subsequent query
        """
        assert self.engine is not None
        assert self.audit is not None

        # Verify counterfactual atoms are retrievable
        cf_query = RetrievalQuery(
            text="counterfactual rollout safety collapse pressure prediction",
            tags=["counterfactual", "rollout"],
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        cf_results = self.engine.search(cf_query)
        cf_result_ids = {r.atom_id for r in cf_results}
        stored_cf_ids = {a.atom_id for a in self._counterfactual_atoms}
        retrievable_count = len(cf_result_ids & stored_cf_ids)

        # A hazard is "addressed" when an unsafe (limit-exceeding) action is
        # AVOIDed. With the rule engine, a hazard that is within limit yields a
        # PROCEED decision and is not counted as requiring avoidance.
        unsafe_decisions = [
            d for d in self._safety_decisions if d.get("risk_score", 0.0) >= self._risk_threshold
        ]
        all_hazards_addressed = all(d["decision"] == "AVOID" for d in unsafe_decisions)

        metrics: dict[str, Any] = {
            "task": {
                "collapse_avoidance": self._collapse_avoided,
                "counterfactual_atoms_stored": len(self._counterfactual_atoms),
                "counterfactual_atoms_retrievable": retrievable_count,
                "all_hazards_addressed": all_hazards_addressed,
                "risk_threshold": self._risk_threshold,
                "decisions": [
                    {
                        "target": d["target"],
                        "decision": d["decision"],
                        "risk_score": d.get("risk_score"),
                    }
                    for d in self._safety_decisions
                ],
            },
            "system": {
                **self.latency.summary(),
                **self.cost.summary(),
                **self.bandwidth.summary(),
                **self.engine.cache_stats,
                "total_atoms": self.engine.atom_count,
                "cloud_mode": str(self.settings.cloud_mode),
                "agent_mode": self.agent_mode,
            },
            "audit": {
                "total_entries": self.audit.entry_count,
            },
        }

        # Flush audit before saving
        self.audit.flush()
        self._save_results(metrics)

        # Print summary
        logger.info(f"Run ID: {self.run_id}")
        logger.info(f"Atoms ingested: {self.engine.atom_count}")
        logger.info(f"Collapse avoidance: {self._collapse_avoided}")
        logger.info(f"Counterfactual atoms stored: {len(self._counterfactual_atoms)}")
        logger.info(f"Counterfactual atoms retrievable: {retrievable_count}")
        logger.info(f"Safety decisions: {len(self._safety_decisions)}")
        logger.info(f"Audit entries: {self.audit.entry_count}")

        return {"run_id": self.run_id, "metrics": metrics}
