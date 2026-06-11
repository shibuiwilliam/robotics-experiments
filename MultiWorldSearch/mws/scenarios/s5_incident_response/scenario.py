"""Scenario 5: Real-time Incident Response Coordination.

Safety, response speed, compliance. Tests curiosity loop (H6) and stigmergy (H2).

World: Multi-room facility with substance_X leak in room_A, occluded room_B,
two exits, and 3 robots. IncidentAgent (ADK mock) fuses SDS + floor plan +
duty roster to coordinate response. Curiosity loop detects observation gap in
room_B and dispatches recon robot.
"""

from __future__ import annotations

from typing import Any

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.logging import get_logger
from mws.core.types import ConsumerType, Modality
from mws.reactive.curiosity import CuriosityEngine
from mws.reactive.standing_query import StandingQueryEngine
from mws.retrieval.query import RetrievalQuery
from mws.scenarios.base import BaseScenario
from mws.scenarios.ground_truth import derive_relevance
from mws.scenarios.registry import register_scenario
from mws.scenarios.s5_incident_response.data import generate_s5_business_atoms
from mws.scenarios.s5_incident_response.world import SCENARIO5_XML
from mws.sim.world import MuJoCoWorld

logger = get_logger(__name__)

# Retrieval-dependent steps are complete only if their evidence tag was
# retrieved. Procedural steps (gap/recon/finalize) have no requirement.
_STEP_REQUIRED_EVIDENCE: dict[str, str] = {
    "confirm_leak": "leak",
    "retrieve_sds": "sds",
    "identify_exits": "exit",
    "check_roster": "duty_roster",
}


@register_scenario
class IncidentResponseScenario(BaseScenario):
    name = "incident_response"
    description = "Real-time incident response coordination"

    def __init__(self) -> None:
        super().__init__()
        self._world: MuJoCoWorld | None = None
        self._leak_atom: ExperienceAtom | None = None
        self._standing_query_fired: bool = False
        self._recon_dispatched: bool = False
        self._recon_observation: ExperienceAtom | None = None
        self._incident_plan: dict[str, Any] = {}
        self._agent_results: list[dict[str, Any]] = []
        self._retrieved_tags: set[str] = set()
        # Tags of evidence atoms to withhold from seed_memory (falsifiability)
        self._omit_evidence_tags: set[str] = set()

    def setup(self, seed: int, config: dict[str, Any]) -> None:
        """Phase 1: Build MuJoCo world with rooms, exits, 3 robots."""
        self._omit_evidence_tags = set(config.get("omit_evidence_tags", []))
        self._world = MuJoCoWorld(xml_path="__inline__", seed=seed)
        import mujoco

        self._world._model = mujoco.MjModel.from_xml_string(SCENARIO5_XML)
        self._world._data = mujoco.MjData(self._world._model)
        self._init_run(seed, config, world=self._world, world_id="incident_facility")
        assert self.audit is not None
        self.audit.log(
            "setup",
            "system",
            "world_created",
            details={
                "world": "incident_facility",
                "rooms": ["room_A", "room_B"],
                "exits": ["exit_1", "exit_2"],
                "robots": ["robot_1", "robot_2", "robot_3"],
            },
        )

    def seed_memory(self) -> None:
        """Phase 2: Ingest SDS, safety SOP, floor plan, duty roster."""
        assert self.audit is not None

        biz_atoms = generate_s5_business_atoms(seed=self.seed)
        if self._omit_evidence_tags:
            biz_atoms = [a for a in biz_atoms if not (set(a.tags) & self._omit_evidence_tags)]
        self._ingest_atoms(biz_atoms)
        self.audit.log(
            "seed_memory",
            "system",
            "business_data_ingested",
            details={
                "n_atoms": len(biz_atoms),
                "types": ["sds", "sop", "floor_plan", "duty_roster"],
            },
        )

    def inject(self) -> None:
        """Phase 3: Create leak event in room_A. Mark room_B as occluded."""
        assert self.audit is not None

        # Leak event atom in room_A
        self._leak_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=1.0,
                y=0.0,
                z=0.3,
                timestamp=1700001000.0,
                world_id="incident_facility",
            ),
            text_summary="HAZARD: substance_X leak detected in room_A near leak_source. "
            "Concentration rising. Immediate response required.",
            entity_id="leak_source",
            tags=["leak", "substance_X", "hazard", "room_A", "incident"],
            structured_fields={
                "substance_id": "substance_X",
                "region": "room_A",
                "event_type": "leak",
                "severity": 4,
            },
        )
        self._leak_atom.provenance.add("robot_1:chemical_sensor", "sensor", "created")

        # Register standing query BEFORE ingesting the leak atom so it fires
        # automatically via the engine's ingestion pipeline
        assert self.engine is not None
        self._sq_engine = StandingQueryEngine()
        self._sq_engine.register("leak_detection", tags=["leak", "substance_X"])
        self.engine._standing_queries = self._sq_engine

        # Ingesting the leak atom will auto-fire the standing query
        self._ingest_atom(self._leak_atom)
        self._standing_query_fired = self._sq_engine.total_fires > 0

        # Occluded region marker for room_B (no sensor coverage)
        occluded_atom = ExperienceAtom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(
                x=7.0,
                y=0.0,
                z=0.0,
                timestamp=1700000500.0,
                world_id="incident_facility",
            ),
            text_summary="Coverage gap: room_B has no recent sensor observations. "
            "Region is occluded by partition. Status unknown.",
            entity_id="room_B",
            tags=["occluded", "gap", "room_B", "no_coverage", "unknown"],
            structured_fields={
                "region": "room_B",
                "coverage_status": "none",
                "last_observation_age": 9999,
            },
        )
        occluded_atom.provenance.add("coverage_monitor", "system", "created")
        self._ingest_atom(occluded_atom)

        self.audit.log(
            "inject",
            "system",
            "hazard_injected",
            entity_id="leak_source",
            details={
                "leak_substance": "substance_X",
                "leak_location": "room_A",
                "occluded_region": "room_B",
            },
        )

    def perceive(self) -> None:
        """Phase 4: Generate body position atoms. Robot_1 detects leak."""
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
                world_id="incident_facility",
            )
            atom = ExperienceAtom(
                modality=Modality.POSE,
                coord=coord,
                text_summary=f"Body '{name}' observed at ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})",
                entity_id=name,
                tags=["sim", "pose", name],
                structured_fields={"body_name": name},
            )
            atom.provenance.add(f"mujoco:incident_facility:{name}", "sensor", "created")
            pose_atoms.append(atom)
        self._ingest_atoms(pose_atoms)  # batched embedding requests (M17)

        self.audit.log(
            "perceive",
            "sim",
            "observations_generated",
            details={"n_bodies": len(self._world.get_body_positions())},
        )

    def act(self) -> None:
        """Phase 6: Standing query, IncidentAgent queries, curiosity loop, plan."""
        assert self.engine is not None
        assert self.audit is not None

        # Standing query already fired during inject() via engine's ingestion pipeline.
        # Log the result here for audit completeness.
        self.audit.log(
            "act",
            "standing_query",
            "leak_detection",
            details={
                "fired": self._standing_query_fired,
                "fired_via": "automatic_ingestion_pipeline",
                "total_fires": self._sq_engine.total_fires if self._sq_engine else 0,
            },
        )

        # --- IncidentAgent: query MWS for SDS + exit routes + duty roster ---
        incident_query = RetrievalQuery(
            text="substance_X safety data sheet SDS evacuation exit route "
            "duty roster on-call personnel chemical leak response",
            tags=["sds", "safety", "exit", "duty_roster", "substance_X"],
            structured_filters={"substance_id": "substance_X"},
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        incident_results = self.engine.search(incident_query)
        incident_projected = self.engine.project(incident_results, incident_query)

        # Evidence actually surfaced by retrieval (union of retrieved atom tags).
        retrieved_tags: set[str] = set()
        for r in incident_results:
            atom = self.engine.get_atom(r.atom_id)
            if atom:
                retrieved_tags.update(atom.tags)
        self._retrieved_tags = retrieved_tags

        self.audit.log(
            "act",
            "incident_agent",
            "query_mws",
            indices=["semantic", "spatial", "structured"],
            projection="text+provenance",
            details={
                "n_results": len(incident_results),
                "query": incident_query.text[:60],
            },
        )

        # IncidentAgent step execution (M9): live ADK in live mode, the
        # deterministic mock otherwise. Step COMPLETION stays gated on the
        # retrieved evidence either way — the agent supplies narrative only.
        from contextlib import nullcontext

        from mws.agents.scenario_agent import create_step_agent
        from mws.core.types import CloudMode

        is_live = self.settings.cloud_mode == CloudMode.LIVE
        self.agent_mode = "live" if is_live else "mock"
        agent = create_step_agent(
            self.settings,
            name="incident_agent",
            instruction=(
                "You are an incident-response coordinator for a chemical facility. "
                "Use the provided retrieved context (SDS, floor plan, duty roster, "
                "observations) to execute each response step. Be concise and cite "
                "the retrieved facts you rely on."
            ),
            mock_step_fn=_mock_incident_step,
            call_recorder=self.llm_recorder,
        )

        def _infer_cm():
            return self.latency.track("gemini_infer") if is_live else nullcontext()

        agent_steps = [
            "confirm_leak",
            "retrieve_sds",
            "identify_exits",
            "check_roster",
            "detect_gap",
            "dispatch_recon",
            "receive_recon",
            "finalize_plan",
        ]
        for step in agent_steps:
            with _infer_cm():
                result = agent.execute_step(step, {"retrieval_results": incident_projected})
            required = _STEP_REQUIRED_EVIDENCE.get(step)
            if required is not None and required not in retrieved_tags:
                result = {
                    **result,
                    "status": "incomplete",
                    "result": f"missing required evidence '{required}' in retrieval",
                }
            self._agent_results.append({"step": step, "evidence_required": required, **result})
            self.audit.log(
                "act",
                "incident_agent",
                f"execute_{step}",
                details=result,
            )
            # Track LLM cost (real cloud call only in live mode — M8;
            # measured tokens from usage_metadata when available — M13)
            step_usage = getattr(agent, "last_usage", None) if is_live else None
            self.cost.record_llm_call(
                input_tokens=(
                    step_usage["input_tokens"]
                    if step_usage
                    else max(1, len(str(incident_projected)) // 4)
                ),
                output_tokens=(
                    step_usage["output_tokens"] if step_usage else max(1, len(str(result)) // 4)
                ),
                real=is_live,
                measured=step_usage is not None,
            )
            self.bandwidth.record_llm_request(
                prompt_chars=len(str(incident_projected)),
                response_chars=len(str(result)),
                real=is_live,
            )

        # --- Curiosity loop: detect gap in room_B via CuriosityEngine ---
        curiosity = CuriosityEngine()
        gap = curiosity.detect_gap("room_B", self._atoms, required_modalities=["telemetry"])
        self.audit.log(
            "act",
            "curiosity_loop",
            "gap_detection",
            details={
                "region": "room_B",
                "has_gap": gap is not None,
                "n_atoms_checked": len(self._atoms),
            },
        )

        # Dispatch robot_2 for recon if gap detected
        if gap is not None:
            curiosity.emit_exploration_task(gap, "robot_2")
            self._recon_dispatched = curiosity.tasks_emitted > 0
            self.audit.log(
                "act",
                "curiosity_loop",
                "dispatch_recon",
                details={"robot": "robot_2", "target": "room_B"},
            )

            # Robot_2 provides new observation (room_B is clear)
            self._recon_observation = ExperienceAtom(
                modality=Modality.TELEMETRY,
                coord=SpatiotemporalCoord(
                    x=7.0,
                    y=0.0,
                    z=0.2,
                    timestamp=1700001200.0,
                    world_id="incident_facility",
                ),
                text_summary="Recon report: room_B is clear. No substance_X detected. "
                "No personnel present. Air quality normal.",
                entity_id="room_B",
                tags=["recon", "room_B", "clear", "observation"],
                structured_fields={
                    "region": "room_B",
                    "substance_detected": False,
                    "personnel_present": False,
                    "air_quality": "normal",
                },
            )
            self._recon_observation.provenance.add("robot_2:sensor_suite", "sensor", "created")
            self._ingest_atom(self._recon_observation)
            self.audit.log(
                "act",
                "robot_2",
                "recon_complete",
                entity_id="room_B",
                details={"status": "clear", "substance_detected": False},
            )

        # --- Finalize incident plan ---
        self._incident_plan = _build_incident_plan(incident_projected, retrieved_tags)
        self.audit.log(
            "act",
            "incident_agent",
            "plan_finalized",
            details=self._incident_plan,
        )

    def evaluate(self) -> dict[str, Any]:
        """Phase 7: Compute metrics and save report."""
        assert self.engine is not None
        assert self.audit is not None

        # Ground-truth relevance
        relevant = derive_relevance(
            query_entity_ids=["leak_source", "room_B"],
            query_tags=[
                "sds",
                "safety",
                "substance_X",
                "leak",
                "exit",
                "duty_roster",
                "incident",
            ],
            atoms=self._atoms,
        )
        # Re-run query for retrieval metrics
        eval_query = RetrievalQuery(
            text="substance_X leak safety SDS exit duty roster incident response",
            tags=["sds", "safety", "substance_X", "leak"],
            structured_filters={"substance_id": "substance_X"},
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        eval_results = self.engine.search(eval_query)
        retrieved_ids = [r.atom_id for r in eval_results]
        retrieval_metrics = self._retrieval_metrics(retrieved_ids, relevant)

        # Task-specific metrics
        plan = self._incident_plan
        plan_uses_sds = plan.get("uses_sds", False)
        plan_uses_exit = plan.get("uses_exit", False)
        plan_uses_roster = plan.get("uses_roster", False)
        all_steps_complete = all(r.get("status") == "complete" for r in self._agent_results)

        metrics: dict[str, Any] = {
            "retrieval": retrieval_metrics,
            "task": {
                "standing_query_fired": self._standing_query_fired,
                "recon_dispatched": self._recon_dispatched,
                "plan_uses_sds": plan_uses_sds,
                "plan_uses_exit": plan_uses_exit,
                "plan_uses_roster": plan_uses_roster,
                "all_steps_complete": all_steps_complete,
                "agent_steps_completed": sum(
                    1 for r in self._agent_results if r.get("status") == "complete"
                ),
                "agent_steps_total": len(self._agent_results),
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

        self.audit.flush()
        self._save_results(metrics)

        logger.info(f"Run ID: {self.run_id}")
        logger.info(f"Atoms ingested: {self.engine.atom_count}")
        logger.info(f"Standing query fired: {self._standing_query_fired}")
        logger.info(f"Recon dispatched: {self._recon_dispatched}")
        logger.info(f"Plan uses SDS: {plan_uses_sds}")
        logger.info(f"Plan uses exit: {plan_uses_exit}")
        logger.info(f"Plan uses roster: {plan_uses_roster}")
        logger.info(f"Recall@10: {retrieval_metrics.get('recall_at_10', 0):.2f}")
        logger.info(f"Audit entries: {self.audit.entry_count}")

        return {"run_id": self.run_id, "metrics": metrics}


def _mock_incident_step(step: str, context: list[dict]) -> dict[str, Any]:
    """Deterministic mock IncidentAgent step execution."""
    responses: dict[str, dict[str, Any]] = {
        "confirm_leak": {
            "action": "confirm",
            "status": "complete",
            "result": "Substance_X leak confirmed in room_A at leak_source. Severity 4.",
        },
        "retrieve_sds": {
            "action": "retrieve_sds",
            "status": "complete",
            "result": "SDS retrieved: substance_X is toxic, requires ventilation, PPE level C.",
        },
        "identify_exits": {
            "action": "identify_exits",
            "status": "complete",
            "result": "Exit_1 at (0,-5) south, exit_2 at (8,0) east. Primary evacuation via exit_1.",
        },
        "check_roster": {
            "action": "check_roster",
            "status": "complete",
            "result": "T-Suzuki on call, certified for hazmat. Contact radio ch.5, ext 2200.",
        },
        "detect_gap": {
            "action": "detect_gap",
            "status": "complete",
            "result": "Coverage gap detected: room_B has no recent observations. Occluded region.",
        },
        "dispatch_recon": {
            "action": "dispatch_recon",
            "status": "complete",
            "result": "Robot_2 dispatched to room_B for reconnaissance.",
        },
        "receive_recon": {
            "action": "receive_recon",
            "status": "complete",
            "result": "Room_B is clear. No substance_X, no personnel. Air quality normal.",
        },
        "finalize_plan": {
            "action": "finalize_plan",
            "status": "complete",
            "result": "Plan: evacuate via exit_1, notify T-Suzuki, robot_3 monitors leak, "
            "PPE level C required per SDS.",
        },
    }
    return responses.get(step, {"action": step, "status": "error", "result": "unknown"})


def _build_incident_plan(context: list[dict], retrieved_tags: set[str]) -> dict[str, Any]:
    """Build a deterministic incident response plan from retrieved context.

    Which sources the plan "uses" is COMPUTED from what retrieval actually
    surfaced: if the SDS / floor-plan / roster atom was not retrieved, the plan
    cannot use it. (In live mode an LLM would generate the prose; the
    source-usage flags remain grounded in the retrieved evidence.)
    """
    uses_sds = "sds" in retrieved_tags
    uses_exit = "exit" in retrieved_tags
    uses_roster = "duty_roster" in retrieved_tags
    return {
        "evacuate_via": "exit_1" if uses_exit else None,
        "notify_personnel": "T-Suzuki" if uses_roster else None,
        "monitor_robot": "robot_3",
        "ppe_level": "C" if uses_sds else None,
        "ventilation_required": uses_sds,
        "room_B_status": "clear",
        "uses_sds": uses_sds,
        "uses_exit": uses_exit,
        "uses_roster": uses_roster,
        "context_items": len(context),
    }
