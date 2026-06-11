"""Scenario 1: Maintenance Multi-Actor Handoff (flagship).

MTTR reduction through cross-modal fused retrieval, skill transfer across
heterogeneous robots, and a complete audit trail.

Hypotheses: H1 (transfer), H3 (perception tax), H8 (consumer-aware projection).
Mechanisms: cross-modal/source fusion, retrieval-augmented VLA, multi-instance
handoff, provenance/audit.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.logging import get_logger
from mws.core.types import ConsumerType, Modality
from mws.retrieval.query import RetrievalQuery
from mws.scenarios.base import BaseScenario
from mws.scenarios.ground_truth import derive_relevance
from mws.scenarios.registry import register_scenario
from mws.scenarios.s1_maintenance_handoff.data import (
    generate_s1_business_atoms,
    generate_s1_skill_demo,
)
from mws.scenarios.s1_maintenance_handoff.world import SCENARIO1_XML
from mws.sim.world import MuJoCoWorld

logger = get_logger(__name__)

# Each ops step that depends on retrieved context is "complete" only if the
# retrieval surfaced a matching evidence atom (tag). Steps with no requirement
# (procedural follow-through) are always complete. This makes step completion
# contingent on the pipeline, not an unconditional True.
_STEP_REQUIRED_EVIDENCE: dict[str, str] = {
    "assess_situation": "anomaly",
    "retrieve_sop": "sop",
    "check_inventory": "inventory",
    "verify_personnel": "personnel",
}


@register_scenario
class MaintenanceHandoffScenario(BaseScenario):
    name = "maintenance_handoff"
    description = "Equipment maintenance multi-actor handoff"

    def __init__(self) -> None:
        super().__init__()
        self._world: MuJoCoWorld | None = None
        self._anomaly_atoms: list[ExperienceAtom] = []
        self._ops_results: list[dict[str, Any]] = []
        self._vla_action: dict[str, Any] = {}
        # Tags of evidence atoms to withhold from seed_memory (for falsifiability)
        self._omit_evidence_tags: set[str] = set()

    def setup(self, seed: int, config: dict[str, Any]) -> None:
        self._omit_evidence_tags = set(config.get("omit_evidence_tags", []))
        # Build MuJoCo world first
        self._world = MuJoCoWorld(xml_path="__inline__", seed=seed)
        import mujoco

        self._world._model = mujoco.MjModel.from_xml_string(SCENARIO1_XML)
        self._world._data = mujoco.MjData(self._world._model)

        # Pass world to _init_run so scene graph is built and wired into engine
        self._init_run(seed, config, world=self._world, world_id="maintenance_workshop")
        assert self.audit is not None
        self.audit.log(
            "setup", "system", "world_created", details={"world": "maintenance_workshop"}
        )

    def seed_memory(self) -> None:
        """Inject precondition atoms: business data + past VLA skill demo."""
        assert self.audit is not None

        # Business data (optionally withholding some evidence for falsifiability)
        biz_atoms = generate_s1_business_atoms(seed=self.seed)
        if self._omit_evidence_tags:
            biz_atoms = [a for a in biz_atoms if not (set(a.tags) & self._omit_evidence_tags)]
        self._ingest_atoms(biz_atoms)
        self.audit.log(
            "seed_memory",
            "system",
            "business_data_ingested",
            details={"n_atoms": len(biz_atoms), "types": ["cmms", "erp", "sop", "shift"]},
        )

        # Past VLA skill demo (taught by patrol_robot)
        skill = generate_s1_skill_demo(seed=self.seed)
        self._ingest_atom(skill)
        self.audit.log(
            "seed_memory",
            "system",
            "skill_demo_ingested",
            entity_id="valve_03",
            details={"skill": "loosen_valve", "source": "patrol_robot"},
        )

    def inject(self) -> None:
        """Inject compound anomaly: vibration↑ + temperature↑ on pump_07."""
        assert self.audit is not None
        rng = np.random.default_rng(self.seed)

        # Vibration anomaly atom
        vib_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=2.0, y=1.0, z=0.4, timestamp=1700001000.0, world_id="maintenance_workshop"
            ),
            text_summary="ANOMALY: pump_07 vibration 4.2 mm/s RMS, threshold 3.0. "
            "Bearing wear suspected. Immediate inspection required.",
            entity_id="pump_07",
            tags=["anomaly", "vibration", "pump_07", "maintenance", "bearing"],
            structured_fields={
                "equipment_id": "pump_07",
                "sensor": "vibration",
                "value": 4.2,
                "threshold": 3.0,
                "severity": 3,
            },
            payload={"vibration_rms": 4.2, "spectrum": rng.standard_normal(64).tolist()},
        )
        vib_atom.provenance.add("patrol_robot:vibration_sensor", "sensor", "created")
        self._ingest_atom(vib_atom)
        self._anomaly_atoms.append(vib_atom)

        # Temperature anomaly atom
        temp_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=2.0, y=1.0, z=0.8, timestamp=1700001000.0, world_id="maintenance_workshop"
            ),
            text_summary="ANOMALY: pump_07 temperature 92°C, threshold 75°C. "
            "Overheating detected. Correlated with vibration anomaly.",
            entity_id="pump_07",
            tags=["anomaly", "temperature", "pump_07", "maintenance", "overheating"],
            structured_fields={
                "equipment_id": "pump_07",
                "sensor": "temperature",
                "value": 92.0,
                "threshold": 75.0,
                "severity": 3,
            },
        )
        temp_atom.provenance.add("patrol_robot:temp_sensor", "sensor", "created")
        self._ingest_atom(temp_atom)
        self._anomaly_atoms.append(temp_atom)

        self.audit.log(
            "inject",
            "patrol_robot",
            "anomaly_detected",
            entity_id="pump_07",
            details={"anomalies": ["vibration_high", "temperature_high"]},
        )

    def perceive(self) -> None:
        """Run MuJoCo simulation, extract observation atoms (pose + telemetry + contacts)."""
        assert self._world is not None
        assert self.audit is not None
        assert self.engine is not None

        from mws.sim.sensors import extract_observation_atoms, extract_trajectory_atom

        # Record trajectory over multiple steps
        positions_over_time = []
        for _ in range(10):
            self._world.step(10)
            positions_over_time.append(self._world.get_qpos())

        # Extract all observation atoms (pose + telemetry + contacts)
        obs_atoms = extract_observation_atoms(
            self._world, world_id="maintenance_workshop", seed=self.seed
        )
        self._ingest_atoms(obs_atoms)

        # Record a trajectory atom with blob store persistence (Fix 6)
        traj_atom = extract_trajectory_atom(
            positions_over_time,
            world_id="maintenance_workshop",
            entity_id="patrol_robot",
            seed=self.seed,
            blob_store=self.engine.stores.blob,
        )
        self._ingest_atom(traj_atom)

        self.audit.log(
            "perceive",
            "sim",
            "observations_generated",
            details={
                "n_obs_atoms": len(obs_atoms),
                "n_contacts": sum(1 for a in obs_atoms if a.modality == Modality.CONTACT),
                "trajectory_stored": traj_atom.payload_ref is not None,
            },
        )

    def act(self) -> None:
        """OpsAgent queries MWS for maintenance context; Manipulator recalls skill."""
        assert self.engine is not None
        assert self.audit is not None

        # --- OpsAgent: fused retrieval for maintenance decision ---
        ops_query = RetrievalQuery(
            text="pump_07 anomaly vibration temperature maintenance procedure bearing",
            tags=["maintenance", "pump_07", "anomaly"],
            # entity_id fires the RELATIONAL index: scene-graph neighbors of
            # pump_07 (e.g. valve_03) pull in their atoms (skill demo, SOP).
            structured_filters={"equipment_id": "pump_07", "entity_id": "pump_07"},
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        ops_results = self.engine.search(ops_query)
        ops_projected = self.engine.project(ops_results, ops_query)
        self.audit.log(
            "act",
            "ops_agent",
            "query",
            entity_id="pump_07",
            indices=["semantic", "symbolic", "structured", "temporal"],
            projection="text+provenance",
            details={"n_results": len(ops_results), "query": ops_query.text[:60]},
        )

        # Evidence actually surfaced by retrieval: the union of tags over the
        # retrieved atoms. A step is only "complete" if its required evidence is
        # present here (procedural steps have no requirement).
        retrieved_tags: set[str] = set()
        for r in ops_results:
            atom = self.engine.get_atom(r.atom_id)
            if atom:
                retrieved_tags.update(atom.tags)

        # OpsAgent plan — uses ADK in live mode, deterministic mock in mock mode
        from mws.agents.ops_agent import create_ops_agent

        agent = create_ops_agent(self.settings, call_recorder=self.llm_recorder)
        # gemini_infer latency is tracked ONLY in live mode (mock agents are
        # local dictionaries — counting them as cloud inference would be false).
        from contextlib import nullcontext

        from mws.core.types import CloudMode

        is_live = self.settings.cloud_mode == CloudMode.LIVE
        self.agent_mode = "live" if is_live else "mock"

        def _infer_cm():
            return self.latency.track("gemini_infer") if is_live else nullcontext()

        with _infer_cm():
            plan_steps = agent.plan({"retrieval_results": ops_projected})
        # Count the planning LLM call itself (previously only execute_step calls
        # were counted, undercounting total LLM usage — IMPROVEMENT.md M1).
        plan_usage = getattr(agent, "last_usage", None) if is_live else None
        self.cost.record_llm_call(
            input_tokens=(
                plan_usage["input_tokens"] if plan_usage else max(1, len(str(ops_projected)) // 4)
            ),
            output_tokens=(
                plan_usage["output_tokens"] if plan_usage else max(1, len(str(plan_steps)) // 4)
            ),
            real=is_live,  # actual ADK round-trip only in live mode (M8)
            measured=plan_usage is not None,  # real usage_metadata tokens (M13)
        )
        self.bandwidth.record_llm_request(
            prompt_chars=len(str(ops_projected)),
            response_chars=len(str(plan_steps)),
            real=is_live,
        )
        for step in plan_steps:
            with _infer_cm():
                result = agent.execute_step(step, {"retrieval_results": ops_projected})
            # Gate completion on retrieved evidence.
            required = _STEP_REQUIRED_EVIDENCE.get(step)
            evidence_present = required is None or required in retrieved_tags
            if not evidence_present:
                result = {
                    **result,
                    "status": "incomplete",
                    "result": f"missing required evidence '{required}' in retrieval",
                }
            self._ops_results.append({"step": step, "evidence_required": required, **result})
            self.audit.log(
                "act", "ops_agent", f"execute_{step}", entity_id="pump_07", details=result
            )

            # Track LLM cost
            step_usage = getattr(agent, "last_usage", None) if is_live else None
            self.cost.record_llm_call(
                input_tokens=(
                    step_usage["input_tokens"]
                    if step_usage
                    else max(1, len(str(ops_projected)) // 4)
                ),
                output_tokens=(
                    step_usage["output_tokens"] if step_usage else max(1, len(str(result)) // 4)
                ),
                real=is_live,
                measured=step_usage is not None,
            )
            self.bandwidth.record_llm_request(
                prompt_chars=len(str(ops_projected)),
                response_chars=len(str(result)),
                real=is_live,
            )

        # --- Manipulator: retrieval-augmented VLA (shared policy, R6) ---
        from mws.vla.policy import RetrievalAugmentedPolicy

        policy = RetrievalAugmentedPolicy(self.engine, seed=self.seed)
        action = policy.act(
            observation={"position": (2.5, 1.0, 0.3)},
            task="loosen valve_03 skill demonstration pump_07 maintenance",
            tags=["skill_demo", "valve_03", "maintenance"],
            # The VLA scans a wider candidate set for a REPLAYABLE trajectory —
            # success now requires an actual trajectory payload (not just any
            # hit), so the policy must look past the first few fused results.
            top_k=8,
            spatial_radius=3.0,
            action_type="loosen_valve",
        )
        self.audit.log(
            "act",
            "mobile_manipulator",
            "query",
            entity_id="valve_03",
            indices=["spatial", "semantic", "symbolic"],
            projection="pose+tensor",
            details={"n_results": action["n_results"], "query": str(action["task"])[:60]},
        )

        # Skill transfer succeeds only when the policy actually replayed a
        # recalled trajectory (used_retrieval) — not merely when search
        # returned something.
        self._vla_action = {
            **action,
            "target": "valve_03",
            "success": bool(action["used_retrieval"]),
        }
        self.audit.log(
            "act",
            "mobile_manipulator",
            "execute_skill",
            entity_id="valve_03",
            details={
                "type": self._vla_action["type"],
                "success": self._vla_action["success"],
                "confidence": self._vla_action["confidence"],
                "trajectory_similarity": round(self._vla_action["trajectory_similarity"], 3),
            },
        )

    def evaluate(self) -> dict[str, Any]:
        """Compute metrics and save report."""
        assert self.engine is not None
        assert self.audit is not None

        # Ground-truth relevance for OpsAgent query
        relevant = derive_relevance(
            query_entity_ids=["pump_07", "valve_03"],
            query_tags=["maintenance", "pump_07", "anomaly", "bearing", "sop"],
            atoms=self._atoms,
        )
        # Re-run the query to get IDs
        ops_query = RetrievalQuery(
            text="pump_07 anomaly vibration temperature maintenance procedure bearing",
            tags=["maintenance", "pump_07", "anomaly"],
            # entity_id fires the RELATIONAL index: scene-graph neighbors of
            # pump_07 (e.g. valve_03) pull in their atoms (skill demo, SOP).
            structured_filters={"equipment_id": "pump_07", "entity_id": "pump_07"},
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        ops_results = self.engine.search(ops_query)
        retrieved_ids = [r.atom_id for r in ops_results]
        retrieval_metrics = self._retrieval_metrics(retrieved_ids, relevant)

        # Ablation study: compare single-index vs multi-index fusion
        from mws.eval.ablation import run_ablation

        ablation_results = run_ablation(self.engine, ops_query, relevant)

        # H3 perception tax (IMPROVEMENT M7): gap between the sim-privileged
        # oracle upper bound and the real pipeline, per retrieval metric.
        from mws.eval.oracle import perception_tax

        tax_results = perception_tax(self._atoms, relevant, retrieval_metrics)

        # Task metrics
        all_steps_complete = all(r.get("status") == "complete" for r in self._ops_results)
        skill_transfer = self._vla_action.get("success", False)
        # How many fused results carried a relational (scene-graph) contribution
        # — evidence that the 6th index genuinely fires (IMPROVEMENT R2).
        relational_hits = sum(1 for r in ops_results if "relational" in r.scores_by_index)

        metrics = {
            "retrieval": retrieval_metrics,
            "ablation": ablation_results,
            "perception_tax": tax_results,
            "task": {
                "ops_steps_completed": sum(
                    1 for r in self._ops_results if r.get("status") == "complete"
                ),
                "ops_steps_total": len(self._ops_results),
                "all_steps_complete": all_steps_complete,
                "skill_transfer_success": skill_transfer,
                "vla_confidence": self._vla_action.get("confidence", 0.0),
                "relational_hits": relational_hits,
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

        # Flush audit BEFORE saving so entry count is captured above
        self.audit.flush()
        # Save
        self._save_results(metrics)

        # Print summary
        logger.info(f"Run ID: {self.run_id}")
        logger.info(f"Atoms ingested: {self.engine.atom_count}")
        logger.info(f"Recall@5: {retrieval_metrics.get('recall_at_5', 0):.2f}")
        logger.info(f"Recall@10: {retrieval_metrics.get('recall_at_10', 0):.2f}")
        logger.info(f"MRR: {retrieval_metrics.get('mrr', 0):.2f}")
        logger.info(f"All ops steps complete: {all_steps_complete}")
        logger.info(f"Skill transfer success: {skill_transfer}")
        logger.info(f"Audit entries: {self.audit.entry_count}")

        return {"run_id": self.run_id, "metrics": metrics}


def _mock_ops_step(step: str, context: list[dict]) -> dict[str, Any]:
    """Deterministic mock OpsAgent step execution."""
    responses: dict[str, dict[str, Any]] = {
        "assess_situation": {
            "action": "assess",
            "status": "complete",
            "result": "Compound anomaly confirmed: vibration + temperature on pump_07.",
        },
        "retrieve_sop": {
            "action": "retrieve_sop",
            "status": "complete",
            "result": "SOP-PUMP-07 retrieved. Procedure: isolate, close valve_03, replace bearing.",
        },
        "check_inventory": {
            "action": "check_inventory",
            "status": "complete",
            "result": "P-BEAR-07 bearing: 3 units at shelf-C2. SK-07 seal: 5 units.",
        },
        "verify_personnel": {
            "action": "verify_personnel",
            "status": "complete",
            "result": "T-Yamada on call. Certified for pump maintenance.",
        },
        "dispatch_manipulator": {
            "action": "dispatch",
            "status": "complete",
            "result": "MobileManipulator dispatched to pump_07/valve_03 with skill recall.",
        },
        "monitor_repair": {
            "action": "monitor",
            "status": "complete",
            "result": "Repair in progress. VLA executing loosen_valve skill on valve_03.",
        },
        "close_work_order": {
            "action": "close",
            "status": "complete",
            "result": "WO-102 created and closed. Vibration now 1.8 mm/s. Temperature 55°C.",
        },
    }
    return responses.get(step, {"action": step, "status": "error", "result": "unknown"})
