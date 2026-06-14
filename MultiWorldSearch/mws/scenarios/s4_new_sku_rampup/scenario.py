"""Scenario 4: New SKU Rampup via Swarm Transfer.

Fast rampup -- one demonstration reused by the entire swarm.

Hypothesis: H1 (transfer: shared memory improves unexperienced instances)
World: Unknown SKU class, unexplored site
Business: Product master (SKU -> destination mapping)
Inject: New SKU class. A/B toggle: demo atom present vs absent (independent variable)
Actors: InstanceA demonstrates skill, SwarmB retrieves and reuses
Query: SwarmB -> indices={semantic, symbolic} + procedural(skill),
       filters={sku_class}, projection=pose+tensor
Success: Transfer gain (with-demo success rate > without-demo),
         cold start time reduction
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
from mws.sim.world import MuJoCoWorld

logger = get_logger(__name__)

# Minimal MuJoCo world: pick station + drop station + swarm robots
SCENARIO4_XML = """
<mujoco model="sku_rampup_site">
  <option timestep="0.002"/>
  <worldbody>
    <light pos="0 0 4" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="5 5 0.1" rgba="0.9 0.9 0.9 1"/>

    <!-- pick_station_A: where SKUs arrive -->
    <body name="pick_station_A" pos="1 0 0.5">
      <geom type="box" size="0.5 0.5 0.5" rgba="0.4 0.6 0.8 1"/>
    </body>

    <!-- drop_station_B: destination for sku_X100 -->
    <body name="drop_station_B" pos="4 0 0.5">
      <geom type="box" size="0.5 0.5 0.5" rgba="0.8 0.6 0.4 1"/>
    </body>

    <!-- instance_A: demonstrator robot -->
    <body name="instance_A" pos="1 -1 0.3">
      <joint name="instA_x" type="slide" axis="1 0 0"/>
      <joint name="instA_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.8 0.3 1"/>
    </body>

    <!-- swarm_B_0: first swarm member -->
    <body name="swarm_B_0" pos="2 -1 0.3">
      <joint name="swB0_x" type="slide" axis="1 0 0"/>
      <joint name="swB0_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.3 0.8 1"/>
    </body>

    <!-- swarm_B_1: second swarm member -->
    <body name="swarm_B_1" pos="3 -1 0.3">
      <joint name="swB1_x" type="slide" axis="1 0 0"/>
      <joint name="swB1_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.3 0.8 1"/>
    </body>
  </worldbody>
</mujoco>
"""


def _generate_product_master(seed: int = 0) -> ExperienceAtom:
    """Product master record: sku_X100 -> destination=drop_station_B."""
    coord = SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=1700000000.0)
    atom = ExperienceAtom(
        modality=Modality.STRUCTURED_RECORD,
        coord=coord,
        text_summary=(
            "Product master: SKU sku_X100 (class=fragile_electronics). "
            "Destination: drop_station_B. Handling: two-finger pinch grip, "
            "max force 5N, orientation upright."
        ),
        entity_id="sku_X100",
        tags=["product_master", "sku_X100", "fragile_electronics", "drop_station_B"],
        structured_fields={
            "sku_id": "sku_X100",
            "sku_class": "fragile_electronics",
            "destination": "drop_station_B",
            "max_grip_force_N": 5.0,
            "orientation": "upright",
        },
    )
    atom.provenance.add("product_master_system", "system", "created")
    return atom


def _generate_skill_demo(seed: int = 0) -> ExperienceAtom:
    """Skill demo atom: picking sku_X100 demonstrated by instance_A."""
    rng = np.random.default_rng(seed)
    trajectory = rng.standard_normal((8, 6)).tolist()

    coord = SpatiotemporalCoord(
        x=1.0, y=0.0, z=0.5, timestamp=1700001000.0, world_id="sku_rampup_site"
    )
    atom = ExperienceAtom(
        modality=Modality.SKILL_DEMO,
        coord=coord,
        text_summary=(
            "VLA skill demonstration: pick sku_X100 from pick_station_A. "
            "Two-finger pinch grip, 8-step trajectory, max force 5N. "
            "Demonstrated by instance_A for swarm transfer."
        ),
        entity_id="sku_X100",
        tags=[
            "skill_demo",
            "pick_sku",
            "sku_X100",
            "fragile_electronics",
            "pick_station_A",
        ],
        structured_fields={
            "skill_name": "pick_sku_X100",
            "sku_id": "sku_X100",
            "sku_class": "fragile_electronics",
            "source_station": "pick_station_A",
            "trajectory_length": 8,
            "max_grip_force_N": 5.0,
        },
        payload={"trajectory": trajectory},
    )
    atom.provenance.add("instance_A", "agent", "created", {"method": "demonstration"})
    return atom


@register_scenario
class NewSkuRampupScenario(BaseScenario):
    name = "new_sku_rampup"
    description = "New SKU/site immediate rampup via swarm transfer"

    def __init__(self) -> None:
        super().__init__()
        self._is_ab_secondary = False  # prevent infinite recursion in A/B
        self._world: MuJoCoWorld | None = None
        self._config: dict[str, Any] = {}
        self._with_demo: bool = True
        self._skill_demo_atom: ExperienceAtom | None = None
        self._swarm_results: list[dict[str, Any]] = []
        # Execution-model tunables (populated in setup). Success is decided by
        # whether the executed pick respects the product-master force limit, not
        # merely by whether a demo was retrieved.
        self._exec_noise_sigma: float = 0.3
        self._demo_safety_margin: float = 1.0
        self._exploration_force_mean: float = 8.0
        self._exploration_force_sigma: float = 2.0
        self._sim_threshold: float = 0.5
        self._force_limit_override: float | None = None

    def setup(self, seed: int, config: dict[str, Any]) -> None:
        self._config = config
        self._with_demo = config.get("with_demo", True)
        self._exec_noise_sigma = float(config.get("exec_noise_sigma", 0.3))
        self._demo_safety_margin = float(config.get("demo_safety_margin", 1.0))
        self._exploration_force_mean = float(config.get("exploration_force_mean", 8.0))
        self._exploration_force_sigma = float(config.get("exploration_force_sigma", 2.0))
        self._sim_threshold = float(config.get("sim_threshold", 0.5))
        self._force_limit_override = (
            float(config["force_limit_n"]) if "force_limit_n" in config else None
        )

        # Build MuJoCo world first, then pass to _init_run for scene graph
        import mujoco

        self._world = MuJoCoWorld(xml_path="__inline__", seed=seed)
        self._world._model = mujoco.MjModel.from_xml_string(SCENARIO4_XML)
        self._world._data = mujoco.MjData(self._world._model)
        self._init_run(seed, config, world=self._world, world_id="sku_rampup")

        assert self.audit is not None
        self.audit.log(
            "setup",
            "system",
            "world_created",
            details={"world": "sku_rampup_site", "with_demo": self._with_demo},
        )

    def seed_memory(self) -> None:
        """Ingest product master record (sku_X100 -> drop_station_B)."""
        assert self.audit is not None

        product_master = _generate_product_master(seed=self.seed)
        self._ingest_atom(product_master)
        self.audit.log(
            "seed_memory",
            "system",
            "product_master_ingested",
            entity_id="sku_X100",
            details={"sku_id": "sku_X100", "destination": "drop_station_B"},
        )

    def inject(self) -> None:
        """Inject skill demo atom if with_demo=True (independent variable)."""
        assert self.audit is not None

        if self._with_demo:
            self._skill_demo_atom = _generate_skill_demo(seed=self.seed)
            self._ingest_atom(self._skill_demo_atom)
            self.audit.log(
                "inject",
                "instance_A",
                "skill_demo_ingested",
                entity_id="sku_X100",
                details={
                    "skill": "pick_sku_X100",
                    "with_demo": True,
                },
            )
        else:
            self.audit.log(
                "inject",
                "system",
                "no_demo_control_group",
                details={"with_demo": False},
            )

    def perceive(self) -> None:
        """Run MuJoCo simulation, generate body position atoms."""
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
                world_id="sku_rampup_site",
            )
            atom = ExperienceAtom(
                modality=Modality.POSE,
                coord=coord,
                text_summary=f"Body '{name}' at ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})",
                entity_id=name,
                tags=["sim", "pose", name],
                structured_fields={"body_name": name},
            )
            atom.provenance.add(f"mujoco:sku_rampup_site:{name}", "sensor", "created")
            pose_atoms.append(atom)
        self._ingest_atoms(pose_atoms)  # batched embedding requests (M17)

        self.audit.log(
            "perceive",
            "sim",
            "observations_generated",
            details={"n_bodies": len(self._world.get_body_positions())},
        )

    def act(self) -> None:
        """SwarmB queries for sku_X100 handling skills and attempts the pick.

        The skill recall + trajectory replay goes through the shared
        RetrievalAugmentedPolicy (mws/vla/policy.py) — the same VLA component
        every scenario uses (IMPROVEMENT R6). The scenario only models the
        PHYSICAL execution (grip force) on top of the policy's action.
        """
        assert self.engine is not None
        assert self.audit is not None

        from mws.vla.policy import RetrievalAugmentedPolicy

        swarm_members = ["swarm_B_0", "swarm_B_1"]
        rng = np.random.default_rng(self.seed)
        policy = RetrievalAugmentedPolicy(self.engine, seed=self.seed)

        for member in swarm_members:
            # Query: SwarmB -> semantic+symbolic+procedural, filter by sku_class
            action = policy.act(
                observation={"position": (1.0, 0.0, 0.5)},
                task="pick sku_X100 fragile_electronics skill demonstration handling",
                tags=["skill_demo", "sku_X100", "fragile_electronics"],
                structured_filters={"sku_class": "fragile_electronics"},
                top_k=5,
                action_type="pick",
            )
            has_skill = bool(action["used_retrieval"])
            trajectory_similarity = float(action["trajectory_similarity"])
            skill_fields = action.get("skill_fields") or {}

            # Retrieve the product-master grip-force limit from MWS (pipeline).
            force_limit = self._force_limit_override
            if force_limit is None:
                force_limit = 5.0  # fallback
                fl_query = RetrievalQuery(
                    text="sku_X100 product master grip force handling",
                    tags=["product_master", "sku_X100"],
                    structured_filters={"sku_id": "sku_X100"},
                    consumer=ConsumerType.CONTROL_LOOP,
                    top_k=3,
                )
                for r in self.engine.search(fl_query):
                    atom = self.engine.get_atom(r.atom_id)
                    if atom and "max_grip_force_N" in atom.structured_fields:
                        force_limit = float(atom.structured_fields["max_grip_force_N"])
                        break

            # EXECUTE the pick. With a recalled skill, the gripper applies the
            # force the demo was CALIBRATED at (skill atom's recorded grip force
            # minus a safety margin) — a FIXED value, independent of the current
            # runtime limit. If the runtime limit is later tightened below this,
            # replaying the old demo over-grips and fails.
            if has_skill:
                demo_force = float(skill_fields.get("max_grip_force_N", force_limit))
                nominal_force = max(0.0, demo_force - self._demo_safety_margin)
                applied_force = nominal_force + rng.normal(0, self._exec_noise_sigma)
            else:
                applied_force = abs(
                    rng.normal(self._exploration_force_mean, self._exploration_force_sigma)
                )

            # Success requires BOTH: a usable replayed trajectory (similarity
            # above threshold) AND keeping grip force within the fragile-item
            # limit. Retrieval is necessary but not sufficient — a recalled demo
            # executed over-limit still fails (see falsifiability tests).
            within_force = applied_force <= force_limit
            success = has_skill and trajectory_similarity >= self._sim_threshold and within_force

            result_entry = {
                "member": member,
                "has_skill_demo": has_skill,
                "applied_force_N": round(float(applied_force), 3),
                "force_limit_N": force_limit,
                "within_force_limit": within_force,
                "trajectory_similarity": round(trajectory_similarity, 3),
                "confidence": action["confidence"],
                "success": success,
                "n_results": action["n_results"],
            }
            self._swarm_results.append(result_entry)

            self.audit.log(
                "act",
                member,
                "query_and_pick",
                entity_id="sku_X100",
                indices=["semantic", "symbolic", "procedural"],
                projection="pose+tensor",
                details=result_entry,
            )

            # Track mock cost (policy action context stands in for the prompt)
            self.cost.record_llm_call(
                input_tokens=max(1, len(str(action)) // 4),
                output_tokens=max(1, len(str(result_entry)) // 4),
            )
            self.bandwidth.record_llm_request(
                prompt_chars=len(str(action)),
                response_chars=len(str(result_entry)),
            )

    def evaluate(self) -> dict[str, Any]:
        """Compute transfer gain and report metrics."""
        assert self.engine is not None
        assert self.audit is not None

        n_total = len(self._swarm_results)
        n_success = sum(1 for r in self._swarm_results if r["success"])
        success_rate = n_success / n_total if n_total > 0 else 0.0
        avg_confidence = (
            sum(r["confidence"] for r in self._swarm_results) / n_total if n_total > 0 else 0.0
        )

        # Run the opposite arm to produce actual A/B comparison data
        # Guard prevents infinite recursion when the second arm also tries to run A/B
        opposite_rate = 0.0
        if not self._is_ab_secondary:
            opposite = NewSkuRampupScenario()
            opposite._is_ab_secondary = True  # prevent recursion
            # Inherit the SAME world constraints; flip only the demo availability
            # so the A/B comparison is fair.
            opposite_config = {**self._config, "with_demo": not self._with_demo}
            opposite_result = opposite.run(
                seed=self.seed, config=opposite_config, settings=self.settings
            )
            opposite_rate = opposite_result["metrics"]["task"]["success_rate"]

        with_demo_rate = success_rate if self._with_demo else opposite_rate
        without_demo_rate = opposite_rate if self._with_demo else success_rate
        transfer_gain = with_demo_rate - without_demo_rate

        metrics = {
            # G3: task-success scenario (skill transfer A/B) — no oracle
            # retrieval set is defined, so perception tax is N/A by design.
            "perception_tax": {
                "applicable": False,
                "reason": "task-success scenario (skill-transfer A/B); no retrieval relevance set",
            },
            "task": {
                "with_demo": self._with_demo,
                "success_rate": success_rate,
                "avg_confidence": avg_confidence,
                "n_swarm_members": n_total,
                "n_successes": n_success,
                "swarm_results": self._swarm_results,
            },
            "transfer": {
                "with_demo_success_rate": with_demo_rate,
                "without_demo_success_rate": without_demo_rate,
                "transfer_gain": transfer_gain,
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
        logger.info(f"With demo: {self._with_demo}")
        logger.info(f"Success rate: {success_rate:.2f}")
        logger.info(f"Avg confidence: {avg_confidence:.2f}")
        logger.info(f"Atoms ingested: {self.engine.atom_count}")
        logger.info(f"Audit entries: {self.audit.entry_count}")

        return {"run_id": self.run_id, "metrics": metrics}

    @classmethod
    def run_ab_experiment(
        cls,
        seed: int = 0,
        settings: Any = None,
    ) -> dict[str, Any]:
        """Run the A/B experiment: with-demo vs without-demo.

        Returns combined results including transfer_gain.
        """
        # Run WITH demo
        scenario_with = cls()
        result_with = scenario_with.run(seed=seed, config={"with_demo": True}, settings=settings)

        # Run WITHOUT demo
        scenario_without = cls()
        result_without = scenario_without.run(
            seed=seed, config={"with_demo": False}, settings=settings
        )

        rate_with = result_with["metrics"]["task"]["success_rate"]
        rate_without = result_without["metrics"]["task"]["success_rate"]
        transfer_gain = rate_with - rate_without

        return {
            "with_demo": result_with,
            "without_demo": result_without,
            "transfer_gain": transfer_gain,
            "success_rate_with": rate_with,
            "success_rate_without": rate_without,
            "h1_confirmed": transfer_gain > 0,
        }
