"""Scenario 2: Physical vs Record Reconciliation.

Inventory accuracy and physical-digital truth gap resolution.
Multi-observer fusion beats single observer (H9), freshness-aware
retrieval surfaces stale WMS data (H4).

Hypotheses: H4 (freshness), H9 (multi-observer fusion > single observer).
Mechanisms: provenance-weighted multi-observer estimate, discrepancy
ticket generation, WMS write-back.
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

# ---------------------------------------------------------------------------
# MuJoCo world XML (inline, following s1 pattern)
# ---------------------------------------------------------------------------
SCENARIO2_XML = """
<mujoco model="warehouse_inventory">
  <option timestep="0.002"/>
  <worldbody>
    <light pos="0 0 4" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="10 10 0.1" rgba="0.9 0.9 0.9 1"/>

    <!-- Shelf A: holds items (ground truth ~30) -->
    <body name="shelf_A" pos="1 0 1.0">
      <geom type="box" size="1.0 0.3 1.0" rgba="0.6 0.5 0.4 1"/>
    </body>

    <!-- Shelf B -->
    <body name="shelf_B" pos="3 0 1.0">
      <geom type="box" size="1.0 0.3 1.0" rgba="0.6 0.5 0.4 1"/>
    </body>

    <!-- Shelf C -->
    <body name="shelf_C" pos="5 0 1.0">
      <geom type="box" size="1.0 0.3 1.0" rgba="0.6 0.5 0.4 1"/>
    </body>

    <!-- Misplaced asset: forklift_12 on floor (registry says in_maintenance) -->
    <body name="forklift_12" pos="4 3 0.3">
      <geom type="box" size="0.5 0.3 0.3" rgba="0.9 0.6 0.1 1"/>
    </body>

    <!-- Robot A: inventory scanner (lower noise, higher trust) -->
    <body name="robot_A" pos="-1 0 0.2">
      <joint name="robot_A_x" type="slide" axis="1 0 0"/>
      <joint name="robot_A_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.8 0.3 1"/>
    </body>

    <!-- Robot B: inventory scanner (higher noise, lower trust) -->
    <body name="robot_B" pos="-1 2 0.2">
      <joint name="robot_B_x" type="slide" axis="1 0 0"/>
      <joint name="robot_B_y" type="slide" axis="0 1 0"/>
      <geom type="capsule" size="0.12 0.2" rgba="0.3 0.3 0.8 1"/>
    </body>
  </worldbody>
</mujoco>
"""

# ---------------------------------------------------------------------------
# Tunable defaults (overridable via the scenario `config` dict / YAML).
#
# These are NOT the conclusion. The observed counts are *drawn* from the
# ground truth plus seeded Gaussian sensor noise (see `inject`), and whether
# provenance-weighted fusion beats the best single observer (H9) is *measured*
# by a Monte-Carlo over many seeded draws (see `evaluate`). Change the sigmas
# or the weighting and the verdict can flip — i.e. this is a falsifiable
# experiment, not a hard-coded result.
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, float] = {
    "ground_truth_count": 30.0,
    "wms_drifted_count": 50.0,
    # Sensor noise std-devs. robot_A is the higher-quality (lower-sigma) sensor.
    # Chosen so that *inverse-variance* fusion robustly beats the best single
    # observer, while *equal* weighting does not (verified in tests). With very
    # asymmetric sensors (e.g. 0.5 vs 2.0) optimal fusion only marginally beats
    # trusting the good sensor alone, making the statistical verdict flaky.
    "sigma_a": 1.0,
    "sigma_b": 2.0,
    # Number of seeded draws used to decide fusion-beats-single statistically.
    "n_fusion_trials": 1024.0,
}


def inverse_variance_weight(sigma: float) -> float:
    """Statistically optimal (BLUE) weight for an observer with noise std `sigma`."""
    return 1.0 / (sigma * sigma)


def weighted_fuse(values: list[float], weights: list[float]) -> float:
    """Provenance/precision-weighted average of observer estimates."""
    total_weight = sum(weights)
    if total_weight == 0:
        raise ValueError("total fusion weight must be > 0")
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total_weight


def monte_carlo_fusion_advantage(
    ground_truth: float,
    sigmas: list[float],
    n_trials: int,
    seed: int,
    weights: list[float] | None = None,
) -> dict[str, Any]:
    """Measure whether weighted fusion beats the best single observer.

    For each of `n_trials` seeded draws, each observer i reports
    ``ground_truth + N(0, sigma_i)``. We compare the mean absolute error of the
    fused estimate against the mean absolute error of the *a-priori best*
    observer (the one with the smallest sigma — i.e. you would trust your best
    sensor, not pick the post-hoc luckiest one).

    `weights=None` uses inverse-variance (optimal) weighting. Passing explicit
    weights (e.g. equal) lets callers show that the *correct* weighting is
    load-bearing — with equal weights, fusion can fail to beat the best sensor.
    """
    rng = np.random.default_rng(seed)
    if weights is None:
        weights = [inverse_variance_weight(s) for s in sigmas]
    best_idx = min(range(len(sigmas)), key=lambda i: sigmas[i])

    fusion_errors: list[float] = []
    single_errors: list[float] = []
    for _ in range(n_trials):
        obs = [ground_truth + rng.normal(0, s) for s in sigmas]
        fused = weighted_fuse(obs, weights)
        fusion_errors.append(abs(fused - ground_truth))
        single_errors.append(abs(obs[best_idx] - ground_truth))

    mean_fusion_error = float(np.mean(fusion_errors))
    mean_best_single_error = float(np.mean(single_errors))
    return {
        "mean_fusion_error": mean_fusion_error,
        "mean_best_single_error": mean_best_single_error,
        "fusion_beats_single": mean_fusion_error < mean_best_single_error,
        "n_trials": n_trials,
    }


@register_scenario
class PhysicalRecordReconciliationScenario(BaseScenario):
    name = "physical_record_reconciliation"
    description = "Physical vs record reconciliation"

    def __init__(self) -> None:
        super().__init__()
        self._world: MuJoCoWorld | None = None
        self._wms_atoms: list[ExperienceAtom] = []
        self._observation_atoms: list[ExperienceAtom] = []
        self._discrepancy_ticket: dict[str, Any] | None = None
        self._write_back: dict[str, Any] | None = None
        self._fusion_estimate: float = 0.0
        self._fusion_failed: bool = False
        self._n_observations_retrieved: int = 0
        self._single_observer_estimates: list[float] = []
        self._single_observer_variances: list[float] = []
        self._suppress_observations: bool = False
        # Tunables (populated from config in setup())
        self._gt: float = DEFAULTS["ground_truth_count"]
        self._wms_drift: float = DEFAULTS["wms_drifted_count"]
        self._sigma_a: float = DEFAULTS["sigma_a"]
        self._sigma_b: float = DEFAULTS["sigma_b"]
        self._n_fusion_trials: int = int(DEFAULTS["n_fusion_trials"])

    # ------------------------------------------------------------------
    # Phase 1: setup
    # ------------------------------------------------------------------
    def setup(self, seed: int, config: dict[str, Any]) -> None:
        # Read tunables from config (falling back to DEFAULTS).
        self._gt = float(config.get("ground_truth_count", DEFAULTS["ground_truth_count"]))
        self._wms_drift = float(config.get("wms_drifted_count", DEFAULTS["wms_drifted_count"]))
        self._sigma_a = float(config.get("sigma_a", DEFAULTS["sigma_a"]))
        self._sigma_b = float(config.get("sigma_b", DEFAULTS["sigma_b"]))
        self._n_fusion_trials = int(config.get("n_fusion_trials", DEFAULTS["n_fusion_trials"]))
        # Falsifiability knob: skip creating observer atoms so retrieval cannot
        # surface them → fusion must fail (protocol-gated, IMPROVEMENT R3).
        self._suppress_observations = bool(config.get("suppress_observations", False))

        self._world = MuJoCoWorld(xml_path="__inline__", seed=seed)
        import mujoco

        self._world._model = mujoco.MjModel.from_xml_string(SCENARIO2_XML)
        self._world._data = mujoco.MjData(self._world._model)
        self._init_run(seed, config, world=self._world, world_id="warehouse")
        assert self.audit is not None
        self.audit.log(
            "setup",
            "system",
            "world_created",
            details={"world": "warehouse_inventory"},
        )

    # ------------------------------------------------------------------
    # Phase 2: seed_memory — business data from data.py generators
    # ------------------------------------------------------------------
    def seed_memory(self) -> None:
        assert self.audit is not None

        from mws.scenarios.s2_physical_record_reconciliation.data import (
            generate_s2_asset_records,
            generate_s2_wms_records,
        )

        wms_atoms = generate_s2_wms_records(
            n_shelves=1,
            drift_magnitude=int(self._wms_drift - self._gt),
            seed=self.seed,
        )
        self._ingest_atoms(wms_atoms)  # batched embedding requests (M17)
        self._wms_atoms.extend(wms_atoms)

        asset_atoms = generate_s2_asset_records(seed=self.seed)
        self._ingest_atoms(asset_atoms)

        self.audit.log(
            "seed_memory",
            "system",
            "business_data_ingested",
            details={
                "n_atoms": len(wms_atoms) + len(asset_atoms),
                "types": ["wms", "asset_registry"],
                "wms_count": self._wms_drift,
                "source": "data.py generators",
            },
        )

    # ------------------------------------------------------------------
    # Phase 3: inject — multi-observer observations with deterministic noise
    # ------------------------------------------------------------------
    def inject(self) -> None:
        assert self.audit is not None
        rng = np.random.default_rng(self.seed)

        obs_ts = 1700001000.0

        # --- Draw the actual sensor readings from ground truth + noise ---
        # The observed counts are NOT constants: each is ground_truth plus a
        # seeded Gaussian sample. Change the seed and the readings change.
        observed_a = self._gt + rng.normal(0, self._sigma_a)
        observed_b = self._gt + rng.normal(0, self._sigma_b)
        var_a, var_b = self._sigma_a**2, self._sigma_b**2
        # Trust = normalized inverse-variance (precision). robot_A, being the
        # lower-noise sensor, earns higher trust — derived, not assigned.
        w_a, w_b = inverse_variance_weight(self._sigma_a), inverse_variance_weight(self._sigma_b)
        norm = w_a + w_b
        trust_a, trust_b = w_a / norm, w_b / norm

        if self._suppress_observations:
            # Falsifiability path (R3): the readings were drawn but never become
            # atoms, so retrieval cannot surface them and fusion must fail.
            self.audit.log(
                "inject",
                "system",
                "observations_suppressed",
                details={"reason": "falsifiability test", "drawn_but_not_ingested": 2},
            )
            self._inject_forklift_observation(trust_a)
            return

        # Robot A observation (lower noise → higher precision/trust)
        robot_a_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=1.0, y=0.0, z=0.5, timestamp=obs_ts, world_id="warehouse_inventory"
            ),
            text_summary=(
                f"Robot A inventory scan: shelf_A SKU-WIDGET-A count={observed_a:.1f}. "
                f"Sensor noise sigma {self._sigma_a}. High-precision scanner."
            ),
            entity_id="shelf_A",
            tags=["observation", "inventory", "shelf_A", "SKU-WIDGET-A", "robot_A"],
            structured_fields={
                "sku": "SKU-WIDGET-A",
                "location": "shelf_A",
                "observed_count": round(observed_a, 3),
                "observer": "robot_A",
                "noise_variance": var_a,
            },
        )
        robot_a_atom.provenance.add("robot_A:scanner", "sensor", "created")
        robot_a_atom.trust = trust_a
        self._ingest_atom(robot_a_atom)
        self._observation_atoms.append(robot_a_atom)

        # Robot B observation (higher noise → lower precision/trust)
        robot_b_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=1.0, y=0.0, z=0.5, timestamp=obs_ts + 5.0, world_id="warehouse_inventory"
            ),
            text_summary=(
                f"Robot B inventory scan: shelf_A SKU-WIDGET-A count={observed_b:.1f}. "
                f"Sensor noise sigma {self._sigma_b}. Standard scanner."
            ),
            entity_id="shelf_A",
            tags=["observation", "inventory", "shelf_A", "SKU-WIDGET-A", "robot_B"],
            structured_fields={
                "sku": "SKU-WIDGET-A",
                "location": "shelf_A",
                "observed_count": round(observed_b, 3),
                "observer": "robot_B",
                "noise_variance": var_b,
            },
        )
        robot_b_atom.provenance.add("robot_B:scanner", "sensor", "created")
        robot_b_atom.trust = trust_b
        self._ingest_atom(robot_b_atom)
        self._observation_atoms.append(robot_b_atom)

        self._inject_forklift_observation(trust_a)

        self.audit.log(
            "inject",
            "system",
            "observations_injected",
            details={
                "robot_A_count": round(observed_a, 3),
                "robot_B_count": round(observed_b, 3),
                "ground_truth": self._gt,
                "forklift_observed": True,
            },
        )

    def _inject_forklift_observation(self, trust: float) -> None:
        """Robot A observes forklift_12 on the floor (not in maintenance bay)."""
        assert self.audit is not None
        forklift_obs_atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=SpatiotemporalCoord(
                x=4.0, y=3.0, z=0.3, timestamp=1700001010.0, world_id="warehouse_inventory"
            ),
            text_summary=(
                "Robot A observation: forklift_12 detected on warehouse floor at (4,3,0.3). "
                "Asset registry says status=in_maintenance, expected at maintenance_bay. "
                "Status mismatch detected."
            ),
            entity_id="forklift_12",
            tags=[
                "observation",
                "asset",
                "forklift_12",
                "misplaced",
                "status_mismatch",
                "robot_A",
            ],
            structured_fields={
                "asset_id": "forklift_12",
                "observed_location": "warehouse_floor",
                "observed_status": "on_floor",
                "observer": "robot_A",
            },
        )
        forklift_obs_atom.provenance.add("robot_A:camera", "sensor", "created")
        forklift_obs_atom.trust = trust
        self._ingest_atom(forklift_obs_atom)
        self._observation_atoms.append(forklift_obs_atom)

    # ------------------------------------------------------------------
    # Phase 4: perceive — MuJoCo sim observations
    # ------------------------------------------------------------------
    def perceive(self) -> None:
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
                world_id="warehouse_inventory",
            )
            atom = ExperienceAtom(
                modality=Modality.POSE,
                coord=coord,
                text_summary=f"Body '{name}' observed at ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})",
                entity_id=name,
                tags=["sim", "pose", name],
                structured_fields={"body_name": name},
            )
            atom.provenance.add(f"mujoco:warehouse_inventory:{name}", "sensor", "created")
            pose_atoms.append(atom)
        self._ingest_atoms(pose_atoms)  # batched embedding requests (M17)

        self.audit.log(
            "perceive",
            "sim",
            "observations_generated",
            details={"n_bodies": len(self._world.get_body_positions())},
        )

    # ------------------------------------------------------------------
    # Phase 6: act — ReconAgent queries, fuses, generates ticket, writes back
    # ------------------------------------------------------------------
    def act(self) -> None:
        assert self.engine is not None
        assert self.audit is not None

        # --- Query MWS for inventory observations + WMS records ---
        recon_query = RetrievalQuery(
            text="shelf_A SKU-WIDGET-A inventory count observation wms",
            tags=["inventory", "shelf_A", "SKU-WIDGET-A"],
            structured_filters={"sku": "SKU-WIDGET-A"},
            consumer=ConsumerType.LLM,
            top_k=10,
        )
        recon_results = self.engine.search(recon_query)
        recon_projected = self.engine.project(recon_results, recon_query)
        self.audit.log(
            "act",
            "recon_agent",
            "query",
            entity_id="shelf_A",
            indices=["structured", "semantic", "temporal"],
            projection="text+provenance",
            details={
                "n_results": len(recon_results),
                "query": recon_query.text[:60],
                "qor": {"freshness": "strict"},
            },
        )

        # Track mock LLM cost
        self.cost.record_llm_call(
            input_tokens=max(1, len(str(recon_projected)) // 4),
            output_tokens=100,
        )
        self.bandwidth.record_llm_request(
            prompt_chars=len(str(recon_projected)),
            response_chars=400,
        )

        # --- Multi-observer fusion over the RETRIEVED observations ---
        # The fusion inputs come from the search results just issued — the
        # ReconAgent obtains observer readings through the shared retrieval
        # protocol, not from scenario-local state (IMPROVEMENT R3). Counts and
        # variances are read back from the retrieved atoms and fused with
        # inverse-variance (precision) weights.
        counts: list[float] = []
        variances: list[float] = []
        for r in recon_results:
            atom = self.engine.get_atom(r.atom_id)
            if atom is None:
                continue
            sf = atom.structured_fields
            if (
                sf.get("location") == "shelf_A"
                and "observed_count" in sf
                and "noise_variance" in sf
            ):
                counts.append(float(sf["observed_count"]))
                variances.append(float(sf["noise_variance"]))

        self._single_observer_estimates = list(counts)
        self._single_observer_variances = list(variances)
        self._n_observations_retrieved = len(counts)
        if counts:
            weights = [1.0 / v for v in variances]
            self._fusion_estimate = weighted_fuse(counts, weights)
        else:
            # No observer readings surfaced by retrieval → fusion cannot run.
            # This is a genuine failure mode (falsifiability), not an error.
            weights = []
            self._fusion_failed = True
            logger.warning("S2 fusion: no observation atoms retrieved; fusion skipped")

        self.audit.log(
            "act",
            "recon_agent",
            "multi_observer_fusion",
            entity_id="shelf_A",
            details={
                "observed_counts": [round(c, 3) for c in counts],
                "observation_variances": variances,
                "inverse_variance_weights": [round(w, 4) for w in weights],
                "fusion_estimate": self._fusion_estimate if counts else None,
                "fusion_failed": self._fusion_failed,
                "wms_count": self._wms_drift,
                "source": "retrieval (recon_results)",
            },
        )

        if self._fusion_failed:
            # Without a fused estimate there is nothing to reconcile: no
            # discrepancy ticket, no write-back. Continue to the (independent)
            # forklift mismatch check below.
            self._reconcile_forklift()
            return

        # --- Compare with WMS record, generate discrepancy ticket ---
        wms_count = self._wms_drift
        discrepancy = abs(wms_count - self._fusion_estimate)

        self._discrepancy_ticket = {
            "ticket_id": f"DISC-{self.run_id[:8]}",
            "type": "inventory_discrepancy",
            "entity_id": "shelf_A",
            "sku": "SKU-WIDGET-A",
            "wms_count": wms_count,
            "fusion_estimate": self._fusion_estimate,
            "discrepancy": discrepancy,
            "observers": ["robot_A", "robot_B"],
            "action": "correct_wms",
            "status": "open",
        }

        # Ingest discrepancy ticket as atom
        ticket_atom = ExperienceAtom(
            modality=Modality.TICKET,
            coord=SpatiotemporalCoord(
                x=1.0, y=0.0, z=1.0, timestamp=1700002000.0, world_id="warehouse_inventory"
            ),
            text_summary=(
                f"DISCREPANCY TICKET: shelf_A SKU-WIDGET-A. "
                f"WMS shows {wms_count}, multi-observer fusion estimate {self._fusion_estimate:.1f}. "
                f"Discrepancy: {discrepancy:.1f} units. Action: correct WMS record."
            ),
            entity_id="shelf_A",
            tags=["ticket", "discrepancy", "inventory", "shelf_A", "SKU-WIDGET-A"],
            structured_fields={
                "ticket_id": self._discrepancy_ticket["ticket_id"],
                "type": "inventory_discrepancy",
                "sku": "SKU-WIDGET-A",
                "wms_count": wms_count,
                "fusion_estimate": round(self._fusion_estimate, 1),
                "discrepancy": round(discrepancy, 1),
            },
        )
        ticket_atom.provenance.add("recon_agent", "agent", "created")
        self._ingest_atom(ticket_atom)

        self.audit.log(
            "act",
            "recon_agent",
            "discrepancy_ticket_generated",
            entity_id="shelf_A",
            details=self._discrepancy_ticket,
        )

        # --- Write-back: correct WMS with fusion estimate ---
        corrected_count = round(self._fusion_estimate)
        self._write_back = {
            "system": "wms",
            "sku": "SKU-WIDGET-A",
            "location": "shelf_A",
            "old_count": wms_count,
            "new_count": corrected_count,
            "source": "multi_observer_fusion",
            "observers": ["robot_A", "robot_B"],
            "status": "applied",
        }

        # Ingest write-back record
        wb_atom = ExperienceAtom(
            modality=Modality.STRUCTURED_RECORD,
            coord=SpatiotemporalCoord(
                x=1.0, y=0.0, z=1.0, timestamp=1700002001.0, world_id="warehouse_inventory"
            ),
            text_summary=(
                f"WMS WRITE-BACK: shelf_A SKU-WIDGET-A count corrected from "
                f"{wms_count} to {corrected_count}. Source: multi-observer fusion."
            ),
            entity_id="shelf_A",
            tags=["wms", "write_back", "inventory", "shelf_A", "SKU-WIDGET-A", "correction"],
            structured_fields={
                "sku": "SKU-WIDGET-A",
                "location": "shelf_A",
                "old_count": wms_count,
                "new_count": corrected_count,
                "system": "wms",
                "action": "write_back",
            },
        )
        wb_atom.provenance.add("recon_agent", "agent", "created")
        self._ingest_atom(wb_atom)

        self.audit.log(
            "act",
            "recon_agent",
            "wms_write_back",
            entity_id="shelf_A",
            details=self._write_back,
        )

        self._reconcile_forklift()

    def _reconcile_forklift(self) -> None:
        """Asset-status reconciliation for forklift_12 (independent of fusion)."""
        assert self.engine is not None
        assert self.audit is not None

        # --- Query for forklift status mismatch ---
        forklift_query = RetrievalQuery(
            text="forklift_12 status location observation asset",
            tags=["forklift_12"],
            structured_filters={"asset_id": "forklift_12"},
            consumer=ConsumerType.LLM,
            top_k=5,
        )
        forklift_results = self.engine.search(forklift_query)
        self.engine.project(forklift_results, forklift_query)
        self.audit.log(
            "act",
            "recon_agent",
            "query_forklift",
            entity_id="forklift_12",
            details={"n_results": len(forklift_results)},
        )

        # Generate forklift discrepancy ticket
        forklift_ticket_atom = ExperienceAtom(
            modality=Modality.TICKET,
            coord=SpatiotemporalCoord(
                x=4.0, y=3.0, z=0.3, timestamp=1700002002.0, world_id="warehouse_inventory"
            ),
            text_summary=(
                "DISCREPANCY TICKET: forklift_12 found on warehouse floor at (4,3). "
                "Asset registry says status=in_maintenance, expected at maintenance_bay. "
                "Action: update asset registry status and location."
            ),
            entity_id="forklift_12",
            tags=["ticket", "discrepancy", "asset", "forklift_12", "status_mismatch"],
            structured_fields={
                "ticket_id": f"DISC-FL-{self.run_id[:8]}",
                "type": "asset_status_mismatch",
                "asset_id": "forklift_12",
                "registry_status": "in_maintenance",
                "observed_status": "on_floor",
                "registry_location": "maintenance_bay",
                "observed_location": "warehouse_floor",
            },
        )
        forklift_ticket_atom.provenance.add("recon_agent", "agent", "created")
        self._ingest_atom(forklift_ticket_atom)

        self.audit.log(
            "act",
            "recon_agent",
            "forklift_discrepancy_ticket",
            entity_id="forklift_12",
            details={"status_mismatch": True},
        )

    # ------------------------------------------------------------------
    # Phase 7: evaluate — metrics
    # ------------------------------------------------------------------
    def evaluate(self) -> dict[str, Any]:
        assert self.engine is not None
        assert self.audit is not None

        # --- Single-draw fusion accuracy (this seed's actual observations) ---
        fusion_error = abs(self._fusion_estimate - self._gt) if not self._fusion_failed else None
        single_errors = [abs(est - self._gt) for est in self._single_observer_estimates]
        # A-priori best single observer = the lowest-variance (highest-trust)
        # sensor, NOT the post-hoc luckiest one.
        if self._single_observer_variances:
            best_idx = min(
                range(len(self._single_observer_variances)),
                key=lambda i: self._single_observer_variances[i],
            )
            best_single_error = single_errors[best_idx]
        else:
            best_single_error = min(single_errors) if single_errors else 0.0

        # --- Statistical verdict for H9 (the honest, falsifiable claim) ---
        # Over many seeded draws, does inverse-variance fusion beat the best
        # single observer in mean absolute error? This CAN be False (e.g. with
        # equal weighting, or near-degenerate sensors).
        mc = monte_carlo_fusion_advantage(
            ground_truth=self._gt,
            sigmas=[self._sigma_a, self._sigma_b],
            n_trials=self._n_fusion_trials,
            seed=self.seed,
        )
        # Contrast: equal weighting (wrong) — shown to demonstrate the verdict
        # is computed and that correct precision weighting is load-bearing.
        mc_equal = monte_carlo_fusion_advantage(
            ground_truth=self._gt,
            sigmas=[self._sigma_a, self._sigma_b],
            n_trials=self._n_fusion_trials,
            seed=self.seed,
            weights=[1.0, 1.0],
        )
        # The H9 verdict requires the fusion to have actually RUN on retrieved
        # observations this run (protocol-gated); the statistical MC result
        # alone cannot rescue a run where retrieval surfaced nothing (R3).
        fusion_beats_single = mc["fusion_beats_single"] and not self._fusion_failed

        # Discrepancy ticket check
        ticket_generated = self._discrepancy_ticket is not None
        ticket_discrepancy = (
            self._discrepancy_ticket["discrepancy"] if self._discrepancy_ticket is not None else 0.0
        )

        # Write-back check
        write_back_applied = (
            self._write_back is not None and self._write_back["status"] == "applied"
        )
        write_back_count = self._write_back["new_count"] if self._write_back else 0
        write_back_error = abs(write_back_count - self._gt)

        metrics: dict[str, Any] = {
            # G3: pose-fusion/reconciliation scenario (H9) — the headline metric
            # is fusion error vs single-observer, not retrieval recall; no oracle
            # retrieval relevance set, so perception tax is N/A by design.
            "perception_tax": {
                "applicable": False,
                "reason": "multi-observer fusion scenario (H9); metric is fusion error, no retrieval relevance set",
            },
            "fusion": {
                "fusion_estimate": self._fusion_estimate if not self._fusion_failed else None,
                "fusion_error": fusion_error,
                "fusion_failed": self._fusion_failed,
                "n_observations_retrieved": self._n_observations_retrieved,
                "single_observer_errors": single_errors,
                "best_single_error": best_single_error,
                "fusion_beats_single": fusion_beats_single,
                "ground_truth": self._gt,
                "statistical": {
                    "weighting": "inverse_variance",
                    "mean_fusion_error": mc["mean_fusion_error"],
                    "mean_best_single_error": mc["mean_best_single_error"],
                    "n_trials": mc["n_trials"],
                    "equal_weighting_beats_single": mc_equal["fusion_beats_single"],
                    "equal_weighting_mean_fusion_error": mc_equal["mean_fusion_error"],
                },
            },
            "discrepancy": {
                "ticket_generated": ticket_generated,
                "wms_count": self._wms_drift,
                "fusion_estimate": self._fusion_estimate,
                "discrepancy_amount": ticket_discrepancy,
            },
            "write_back": {
                "applied": write_back_applied,
                "old_count": self._wms_drift,
                "new_count": write_back_count,
                "write_back_error": write_back_error,
                "ground_truth": self._gt,
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

        # Flush audit BEFORE saving so entry count is captured
        self.audit.flush()
        self._save_results(metrics)

        # Print summary
        logger.info(f"Run ID: {self.run_id}")
        logger.info(f"Atoms ingested: {self.engine.atom_count}")
        if self._fusion_failed:
            logger.info("Fusion: FAILED (no observation atoms retrieved)")
        else:
            logger.info(f"Fusion estimate: {self._fusion_estimate:.2f}")
            logger.info(f"Fusion error: {fusion_error:.2f}")
        logger.info(f"Best single-observer error: {best_single_error:.2f}")
        logger.info(f"Fusion beats single: {fusion_beats_single}")
        logger.info(f"Discrepancy ticket: {ticket_generated}")
        logger.info(f"Write-back applied: {write_back_applied} (count={write_back_count})")
        logger.info(f"Audit entries: {self.audit.entry_count}")

        return {"run_id": self.run_id, "metrics": metrics}
