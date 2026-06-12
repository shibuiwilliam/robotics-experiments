"""Scenario 3: Collective Weak Signal Discovery.

Proactive quality — detect weak signals across a swarm of robots.
Individual observations of micro-defects are minor, but consolidated
across lot_L they reveal a significant supplier quality pattern.

Hypotheses: H5 (consolidation preserves recall while compressing).
Actors: Swarm (robot_1, robot_2, robot_3), QualityAgent (ADK mock).
"""

from __future__ import annotations

from typing import Any

import mujoco

from mws.consolidation.dedup import deduplicate
from mws.consolidation.engine import ConsolidationEngine
from mws.consolidation.ttl import prune_by_ttl
from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.logging import get_logger
from mws.core.types import ConsumerType, Modality
from mws.embedding.factory import create_embedder
from mws.federation.prefetch import PrefetchPlanner
from mws.federation.router import QoRRouter
from mws.federation.store import FederatedStore
from mws.reactive.standing_query import StandingQueryEngine
from mws.retrieval.engine import RetrievalEngine
from mws.retrieval.query import RetrievalQuery
from mws.scenarios.base import BaseScenario
from mws.scenarios.ground_truth import derive_relevance
from mws.scenarios.registry import register_scenario
from mws.scenarios.s3_collective_weak_signal.data import (
    LOT_ASSIGNMENTS,
    LOT_L_OBJECTS,
    generate_s3_business_atoms,
)
from mws.scenarios.s3_collective_weak_signal.world import SCENARIO3_XML
from mws.sim.world import MuJoCoWorld
from mws.storage.registry import StoreRegistry

logger = get_logger(__name__)

# Deterministic weak-signal injection schedule:
# Each tuple: (robot_id, timestamp_offset, object_id, defect_description)
_WEAK_SIGNAL_SCHEDULE = [
    (
        "robot_1",
        100.0,
        "obj_01",
        "Minor surface scratch detected on obj_01 during routine patrol. "
        "Barely visible, within tolerance. Logged for reference.",
    ),
    (
        "robot_2",
        200.0,
        "obj_04",
        "Faint discoloration on obj_04 edge. Possibly cosmetic. No immediate action required.",
    ),
    (
        "robot_3",
        300.0,
        "obj_07",
        "Slight dimensional variation on obj_07 detected by caliper sensor. "
        "Within spec but at upper bound.",
    ),
]

# Uncorrelated NOISE defects on non-lot_L objects (lot_M / lot_N). These are
# sporadic, not a supplier pattern. They make lot discovery non-trivial: the
# consolidation must still surface lot_L as the dominant pattern ABOVE this
# noise floor, and signal purity drops as noise rises. The pool alternates
# lot_M / lot_N so light noise spreads thinly (no single lot rivals lot_L),
# while heavy noise can tie/overtake lot_L and make discovery fail.
_NOISE_POOL = [
    ("robot_2", 150.0, "obj_02", "Dust smudge wiped from obj_02; no defect."),
    ("robot_3", 250.0, "obj_03", "Reflection artifact on obj_03; re-scan clean."),
    ("robot_1", 350.0, "obj_05", "Transient sensor glitch near obj_05; nothing found."),
    ("robot_2", 450.0, "obj_06", "Minor packaging crease on obj_06; cosmetic only."),
    ("robot_3", 550.0, "obj_08", "Lighting flicker over obj_08; no anomaly."),
    ("robot_1", 650.0, "obj_09", "Calibration blip at obj_09; within spec."),
]
DEFAULT_N_NOISE_DEFECTS = 2
# A lot is "discovered" only if it strictly dominates and clears this floor.
DEFAULT_ALERT_THRESHOLD = 2


@register_scenario
class CollectiveWeakSignalScenario(BaseScenario):
    name = "collective_weak_signal"
    description = "Collective weak signal discovery across swarm"

    def __init__(self) -> None:
        super().__init__()
        self._world: MuJoCoWorld | None = None
        self._defect_atoms: list[ExperienceAtom] = []
        self._quality_results: list[dict[str, Any]] = []
        self._standing_query: dict[str, Any] | None = None
        self._lot_l_discovered: bool = False
        self._federated: FederatedStore | None = None
        self._federation_found_all: bool = False
        self._lot_defect_counts: dict[str, int] = {}
        self._discovery_margin: int = 0
        self._n_defects_retrieved: int = 0
        self._router: QoRRouter | None = None
        self._prefetcher: PrefetchPlanner | None = None
        self._local_observer_coverage: int = 0
        # Tunables (populated in setup)
        self._n_noise_defects: int = DEFAULT_N_NOISE_DEFECTS
        self._alert_threshold: int = DEFAULT_ALERT_THRESHOLD
        self._defect_top_k: int = 20
        self._consolidation_max_age: float = 250.0

    def setup(self, seed: int, config: dict[str, Any]) -> None:
        self._n_noise_defects = int(config.get("n_noise_defects", DEFAULT_N_NOISE_DEFECTS))
        self._alert_threshold = int(config.get("alert_threshold", DEFAULT_ALERT_THRESHOLD))
        # Falsifiability knob (R4): a small top_k starves the consolidation of
        # retrieved defects and the lot discovery must fail.
        self._defect_top_k = int(config.get("defect_top_k", 20))
        # H5 metabolism: episodic atoms older than this (seconds) are TTL-pruned
        # in the consolidated store (keeping the latest per entity).
        self._consolidation_max_age = float(config.get("consolidation_max_age", 250.0))
        # Falsifiability knob: disable task-plan prefetch → the strict local
        # pre-check sees only robot_1's own observations (coverage 1, not 3).
        self._prefetch_enabled = bool(config.get("prefetch_enabled", True))
        self._world = MuJoCoWorld(xml_path="__inline__", seed=seed)
        self._world._model = mujoco.MjModel.from_xml_string(SCENARIO3_XML)
        self._world._data = mujoco.MjData(self._world._model)
        self._init_run(seed, config, world=self._world, world_id="inspection_floor")

        # Create federated store: each robot has its own local store (H2 stigmergy)
        # Use same embedder/space/dims as the main engine for consistency
        from mws.core.types import CloudMode

        fed_role = "teacher" if self.settings.cloud_mode == CloudMode.LIVE else "student"
        fed_embedder = create_embedder(self.settings, role=fed_role)
        self._federated = FederatedStore(
            embedder=fed_embedder,
            cost_tracker=self.cost,
            latency_tracker=self.latency,
            bandwidth_meter=self.bandwidth,
        )
        for rid in ("robot_1", "robot_2", "robot_3"):
            # Instance stores are tagged with the EMBEDDER's space/dims —
            # tagging from settings can drift after a space bump and silently
            # defeat embedding reuse (uncounted re-embeds; caught by the
            # reconciliation audit during P9/E1).
            self._federated.add_instance(
                rid,
                fed_embedder.space,
                fed_embedder.dims,
            )
        # QoR-aware routing over the federation (IMPROVEMENT R8): strict
        # freshness prefers the local instance store; default/authority goes
        # to the full federated view.
        self._router = QoRRouter(self._federated)
        # Task-plan-driven predictive prefetch (PROJECT.md §3): before the
        # quality patrol, pull task-relevant atoms into robot_1's local store.
        self._prefetcher = PrefetchPlanner(self._federated)

        assert self.audit is not None
        self.audit.log(
            "setup",
            "system",
            "world_created",
            details={
                "world": "inspection_floor",
                "n_objects": 10,
                "n_robots": 3,
                "federation": True,
            },
        )

    def seed_memory(self) -> None:
        """Inject MES lot records and supplier registry."""
        assert self.audit is not None

        biz_atoms = generate_s3_business_atoms(seed=self.seed)
        self._ingest_atoms(biz_atoms)
        self.audit.log(
            "seed_memory",
            "system",
            "business_data_ingested",
            details={
                "n_atoms": len(biz_atoms),
                "types": ["mes_lot_record", "supplier_registry"],
            },
        )

    def inject(self) -> None:
        """Inject scattered weak-signal observations from 3 robots at different times.

        Each robot independently notices a minor defect on a lot_L object.
        Individually each is below alarm threshold — collectively they reveal
        a lot-correlated quality issue.
        """
        assert self.audit is not None
        base_ts = 1700000000.0

        # Batched ingestion (IMPROVEMENT M17): build all atoms first, embed
        # them in batched requests via _ingest_atoms, THEN copy into the
        # federated per-robot stores (which reuse the attached embeddings).
        pending: list[ExperienceAtom] = []
        fed_pairs: list[tuple[str, ExperienceAtom]] = []

        for robot_id, ts_offset, obj_id, description in _WEAK_SIGNAL_SCHEDULE:
            lot = LOT_ASSIGNMENTS[obj_id]
            obj_idx = int(obj_id.split("_")[1])
            atom = ExperienceAtom(
                modality=Modality.TELEMETRY,
                coord=SpatiotemporalCoord(
                    x=float(obj_idx - 1),
                    y=0.0,
                    z=0.15,
                    timestamp=base_ts + ts_offset,
                    world_id="inspection_floor",
                ),
                text_summary=description,
                entity_id=obj_id,
                tags=[
                    "defect",
                    "micro_defect",
                    "weak_signal",
                    obj_id,
                    lot,
                    robot_id,
                ],
                structured_fields={
                    "object_id": obj_id,
                    "lot": lot,
                    "defect_type": "micro_defect",
                    "severity": 1,
                    "observer": robot_id,
                },
            )
            atom.provenance.add(f"{robot_id}:visual_sensor", "sensor", "created")
            pending.append(atom)
            fed_pairs.append((robot_id, atom))
            self._defect_atoms.append(atom)

            self.audit.log(
                "inject",
                robot_id,
                "weak_signal_observed",
                entity_id=obj_id,
                details={
                    "lot": lot,
                    "severity": 1,
                    "timestamp_offset": ts_offset,
                },
            )

        # --- Uncorrelated noise defects on non-lot_L lots ---
        for robot_id, ts_offset, obj_id, description in _NOISE_POOL[: self._n_noise_defects]:
            lot = LOT_ASSIGNMENTS[obj_id]
            obj_idx = int(obj_id.split("_")[1])
            atom = ExperienceAtom(
                modality=Modality.TELEMETRY,
                coord=SpatiotemporalCoord(
                    x=float(obj_idx - 1),
                    y=0.0,
                    z=0.15,
                    timestamp=base_ts + ts_offset,
                    world_id="inspection_floor",
                ),
                text_summary=description,
                entity_id=obj_id,
                tags=["defect", "micro_defect", "weak_signal", obj_id, lot, robot_id],
                structured_fields={
                    "object_id": obj_id,
                    "lot": lot,
                    "defect_type": "micro_defect",
                    "severity": 1,
                    "observer": robot_id,
                    "noise": True,
                },
            )
            atom.provenance.add(f"{robot_id}:visual_sensor", "sensor", "created")
            pending.append(atom)
            fed_pairs.append((robot_id, atom))
            self._defect_atoms.append(atom)
            self.audit.log(
                "inject",
                robot_id,
                "noise_defect_observed",
                entity_id=obj_id,
                details={"lot": lot, "severity": 1, "noise": True},
            )

        self._ingest_atoms(pending)
        if self._federated is not None:
            for robot_id, atom in fed_pairs:
                self._federated.ingest(robot_id, atom)

    def perceive(self) -> None:
        """Run MuJoCo simulation and generate body position atoms for all objects."""
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
                world_id="inspection_floor",
            )
            atom = ExperienceAtom(
                modality=Modality.POSE,
                coord=coord,
                text_summary=(
                    f"Body '{name}' observed at ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})"
                ),
                entity_id=name,
                tags=["sim", "pose", name],
                structured_fields={"body_name": name},
            )
            atom.provenance.add(f"mujoco:inspection_floor:{name}", "sensor", "created")
            pose_atoms.append(atom)
        self._ingest_atoms(pose_atoms)  # batched embedding requests (M17)

        self.audit.log(
            "perceive",
            "sim",
            "observations_generated",
            details={"n_bodies": len(self._world.get_body_positions())},
        )

    def act(self) -> None:
        """QualityAgent queries for defect patterns, discovers lot_L correlation,
        and registers a standing query for ongoing lot_L monitoring.
        """
        assert self.engine is not None
        assert self.audit is not None

        # --- Step 1: Query for defect observations across all indices ---
        defect_query = RetrievalQuery(
            text="micro defect weak signal quality anomaly lot inspection",
            tags=["defect", "micro_defect", "weak_signal"],
            structured_filters={"defect_type": "micro_defect"},
            consumer=ConsumerType.LLM,
            top_k=self._defect_top_k,
        )
        defect_results = self.engine.search(defect_query)
        defect_projected = self.engine.project(defect_results, defect_query)
        self.audit.log(
            "act",
            "quality_agent",
            "query_defects",
            indices=["semantic", "temporal", "symbolic", "structured"],
            projection="aggregate",
            details={
                "n_results": len(defect_results),
                "query": defect_query.text[:60],
            },
        )

        # Track mock LLM cost
        self.cost.record_llm_call(
            input_tokens=max(1, len(str(defect_projected)) // 4),
            output_tokens=50,
        )
        self.bandwidth.record_llm_request(
            prompt_chars=len(str(defect_projected)),
            response_chars=200,
        )

        # --- Step 1b: Federated search via QoR routing (H2 stigmergy, R8) ---
        fed_results: list[Any] = []
        if self._federated is not None and self._router is not None:
            # Predictive prefetch: the quality agent's task plan (lot patrol)
            # is known BEFORE the queries run, so the planner pulls the
            # task-relevant subset of the federation into robot_1's local
            # store. Without this, the strict local pre-check below can only
            # see robot_1's own observations.
            prefetched = 0
            if self._prefetcher is not None and self._prefetch_enabled:
                prefetched = self._prefetcher.prefetch_for_task(
                    "robot_1", task_tags=["defect", "weak_signal"], top_k=10
                )
                self.audit.log(
                    "act",
                    "quality_agent",
                    "predictive_prefetch",
                    details={"instance": "robot_1", "prefetched_atoms": prefetched},
                )

            # Strict-freshness pre-check: robot_1 consults its OWN local store
            # (lowest latency) for recent defect signals across the swarm.
            local_query = RetrievalQuery(
                text="recent defect observations on this robot",
                tags=["defect", "weak_signal"],
                structured_filters={"qor_freshness": "strict"},
                consumer=ConsumerType.CONTROL_LOOP,
                top_k=5,
            )
            local_results = self._router.route(local_query, local_instance_id="robot_1")
            # Coverage: how many DISTINCT observers the local store can answer
            # for. With prefetch this reaches the full swarm (3); without it,
            # only robot_1 itself (1) — the prefetch effect, falsifiable.
            all_fed = self._federated.all_atoms()
            observers = {
                str(a.structured_fields.get("observer", ""))
                for r in local_results
                if (a := all_fed.get(r.atom_id)) is not None and a.structured_fields.get("observer")
            }
            self._local_observer_coverage = len(observers)
            self.audit.log(
                "act",
                "quality_agent",
                "qor_local_precheck",
                details={
                    "n_results": len(local_results),
                    "scope": "local(robot_1)",
                    "qor": {"freshness": "strict"},
                    "local_observer_coverage": self._local_observer_coverage,
                },
            )
            # Pattern discovery needs the BROAD view → default QoR routes the
            # main defect query to the full federated fan-out.
            fed_results = self._router.route(defect_query, local_instance_id="robot_1")
            fed_robot_ids = set()
            for r in fed_results:
                all_fed_atoms = self._federated.all_atoms()
                atom = all_fed_atoms.get(r.atom_id)
                if atom:
                    observer = atom.structured_fields.get("observer", "")
                    if observer:
                        fed_robot_ids.add(str(observer))
            self._federation_found_all = fed_robot_ids == {"robot_1", "robot_2", "robot_3"}
            self.audit.log(
                "act",
                "quality_agent",
                "federated_search",
                details={
                    "n_results": len(fed_results),
                    "robot_sources": sorted(fed_robot_ids),
                    "found_all_instances": self._federation_found_all,
                },
            )

        # --- Step 2: Analyze lot correlation via ConsolidationEngine ---
        # The clustering input is what RETRIEVAL surfaced — the engine results
        # plus the federated fan-out — not scenario-local state (IMPROVEMENT
        # R4). If the search misses defects (e.g. small top_k), the discovery
        # genuinely fails.
        retrieved_ids = {r.atom_id for r in defect_results}
        if self._federated is not None:
            retrieved_ids.update(r.atom_id for r in fed_results)
        defect_atoms = [
            atom
            for atom_id in sorted(retrieved_ids)  # sorted for determinism
            if (atom := self.engine.get_atom(atom_id)) is not None
            and atom.structured_fields.get("defect_type") == "micro_defect"
        ]
        self._n_defects_retrieved = len(defect_atoms)
        consolidation = ConsolidationEngine(seed=self.seed)
        clusters = consolidation.cluster_by_tags(defect_atoms)

        # Count defects per lot from clustered atoms
        lot_defect_counts: dict[str, int] = {}
        for _key, cluster_atoms in clusters.items():
            for atom in cluster_atoms:
                lot = str(atom.structured_fields.get("lot", ""))
                if lot:
                    lot_defect_counts[lot] = lot_defect_counts.get(lot, 0) + 1

        # Discover the pattern: lot_L must STRICTLY dominate the noise floor and
        # clear the alert threshold. With competing-lot noise this can fail.
        self._lot_defect_counts = dict(lot_defect_counts)
        sorted_counts = sorted(lot_defect_counts.values(), reverse=True)
        top_count = sorted_counts[0] if sorted_counts else 0
        second_count = sorted_counts[1] if len(sorted_counts) > 1 else 0
        self._discovery_margin = top_count - second_count
        max_lot = (
            max(lot_defect_counts, key=lambda k: lot_defect_counts[k]) if lot_defect_counts else ""
        )
        lot_l_count = lot_defect_counts.get("lot_L", 0)
        self._lot_l_discovered = (
            max_lot == "lot_L"
            and lot_l_count >= self._alert_threshold
            and self._discovery_margin > 0  # strictly dominates the runner-up
        )

        analysis = {
            "action": "lot_correlation_analysis",
            "lot_defect_counts": lot_defect_counts,
            "dominant_lot": max_lot,
            "pattern_discovered": self._lot_l_discovered,
            "conclusion": (
                f"Lot {max_lot} shows concentrated micro-defects "
                f"({lot_defect_counts.get(max_lot, 0)} observations). "
                "Recommend supplier review and enhanced inspection."
                if self._lot_l_discovered
                else "No clear lot-level pattern detected."
            ),
        }
        self._quality_results.append(analysis)
        self.audit.log(
            "act",
            "quality_agent",
            "lot_analysis",
            details=analysis,
        )

        # --- Step 3: Register standing query for lot_L monitoring ---
        sq_engine = StandingQueryEngine()
        sq_engine.register("lot_L_monitor", tags=["defect", "lot_L"])
        self._standing_query_engine = sq_engine
        self._standing_query_registered = sq_engine.registered_count > 0

        # Keep legacy dict for backward compatibility with evaluate()
        self._standing_query = {
            "type": "standing_query",
            "target_lot": max_lot if self._lot_l_discovered else "lot_L",
            "frequency": "continuous",
            "registered": self._standing_query_registered,
            "alert_threshold": 2,
        }
        self._quality_results.append(
            {
                "action": "register_standing_query",
                "status": "complete",
                "standing_query": self._standing_query,
            }
        )
        self.audit.log(
            "act",
            "quality_agent",
            "register_standing_query",
            details={
                "target_lot": self._standing_query["target_lot"],
                "frequency": "continuous",
                "alert_threshold": 2,
            },
        )

    def _measure_consolidation(
        self,
        defect_query: RetrievalQuery,
        lot_l_ids: set[str],
        recall_before: float,
    ) -> dict[str, Any]:
        """H5 metabolism (IMPROVEMENT R7): consolidate → dedup → TTL-prune the
        episodic store, re-index the survivors + semantic summaries into a
        consolidated store, and measure compression vs lot_L recall retention.

        Relevance after consolidation = surviving lot_L defect atoms plus the
        semantic summaries that distilled them (a summary inherits the
        relevance of the episodes it consolidates).
        """
        assert self.engine is not None
        episodic = list(self._atoms)
        n_before = len(episodic)

        consolidation = ConsolidationEngine(seed=self.seed)
        summaries = consolidation.consolidate(episodic)
        deduped = deduplicate(episodic)
        current_time = max(a.coord.timestamp for a in episodic) if episodic else 0.0
        kept = prune_by_ttl(deduped, max_age=self._consolidation_max_age, current_time=current_time)
        consolidated_atoms = [*kept, *summaries]
        n_after = len(consolidated_atoms)
        compression_ratio = 1.0 - (n_after / n_before) if n_before else 0.0

        # Re-index the consolidated memory (reuses existing embeddings; new
        # summary atoms are embedded — and counted — through the same trackers).
        post_stores = StoreRegistry(
            embedding_space=self.engine.embedder.space,
            embedding_dims=self.engine.embedder.dims,
        )
        post_engine = RetrievalEngine(
            stores=post_stores,
            embedder=self.engine.embedder,
            cost_tracker=self.cost,
            bandwidth_meter=self.bandwidth,
        )
        for atom in consolidated_atoms:
            post_engine.ingest(atom)

        post_ids = {r.atom_id for r in post_engine.search(defect_query)}
        summary_lot_l_ids = {s.atom_id for s in summaries if "lot_L" in s.tags}
        surviving_ids = {a.atom_id for a in consolidated_atoms}
        relevant_after = (lot_l_ids | summary_lot_l_ids) & surviving_ids
        recall_after = (
            len(post_ids & relevant_after) / len(relevant_after) if relevant_after else 0.0
        )
        recall_retention = recall_after / recall_before if recall_before > 0 else 0.0

        return {
            "n_atoms_before": n_before,
            "n_after_dedup": len(deduped),
            "n_after_ttl": len(kept),
            "n_summaries": len(summaries),
            "n_atoms_after": n_after,
            "compression_ratio": round(compression_ratio, 4),
            "ttl_max_age_s": self._consolidation_max_age,
            "lot_l_recall_before": round(recall_before, 4),
            "lot_l_recall_after": round(recall_after, 4),
            "recall_retention": round(recall_retention, 4),
        }

    def golden_eval(self) -> tuple[RetrievalQuery, set[str]]:
        """Golden evaluation pair (defect query + sim-truth relevance) —
        single source for evaluate() and the fusion-tuning harness."""
        relevant = derive_relevance(
            query_entity_ids=list(LOT_L_OBJECTS),
            query_tags=["defect", "micro_defect", "weak_signal", "lot_L"],
            atoms=self._atoms,
        )
        defect_query = RetrievalQuery(
            text="micro defect weak signal quality anomaly lot inspection",
            tags=["defect", "micro_defect", "weak_signal"],
            structured_filters={"defect_type": "micro_defect"},
            consumer=ConsumerType.LLM,
            top_k=20,
        )
        return defect_query, relevant

    def evaluate(self) -> dict[str, Any]:
        """Compute cluster purity, recall, and scenario-specific metrics."""
        assert self.engine is not None
        assert self.audit is not None

        # --- Signal purity: of ALL logged micro-defects, the fraction that are
        # the true lot_L pattern. This is 1.0 only with zero noise; it drops as
        # uncorrelated noise defects accumulate. (Precision of the surfaced
        # pattern, not a structurally-guaranteed constant.)
        defect_in_lot_l = sum(
            1 for atom in self._defect_atoms if atom.structured_fields.get("lot") == "lot_L"
        )
        total_defect_atoms = len(self._defect_atoms)
        signal_purity = defect_in_lot_l / total_defect_atoms if total_defect_atoms > 0 else 0.0

        # --- Retrieval metrics for defect query ---
        defect_query, relevant = self.golden_eval()
        defect_results = self.engine.search(defect_query)
        retrieved_ids = [r.atom_id for r in defect_results]
        retrieval_metrics = self._retrieval_metrics(retrieved_ids, relevant)

        # --- Recall of lot_L defect detection ---
        # How many of the lot_L signal defects (excluding noise) were retrieved?
        lot_l_ids = {
            a.atom_id for a in self._defect_atoms if a.structured_fields.get("lot") == "lot_L"
        }
        retrieved_lot_l = len(lot_l_ids & set(retrieved_ids))
        lot_l_recall = retrieved_lot_l / len(lot_l_ids) if lot_l_ids else 0.0

        # --- H5: consolidation metabolism — compression vs recall retention ---
        consolidation_metrics = self._measure_consolidation(defect_query, lot_l_ids, lot_l_recall)

        metrics: dict[str, Any] = {
            "retrieval": retrieval_metrics,
            "task": {
                "signal_purity": signal_purity,
                "lot_l_discovered": self._lot_l_discovered,
                "lot_l_recall": lot_l_recall,
                "lot_defect_counts": self._lot_defect_counts,
                "discovery_margin": self._discovery_margin,
                "n_defects_retrieved": self._n_defects_retrieved,
                "n_noise_defects": self._n_noise_defects,
                "defect_atoms_total": total_defect_atoms,
                "defect_atoms_lot_l": defect_in_lot_l,
                "standing_query_registered": self._standing_query is not None
                and self._standing_query.get("registered", False),
                "federation_found_all": self._federation_found_all,
                "qor_router": self._router.stats if self._router else {},
                "prefetch": {
                    "enabled": self._prefetch_enabled,
                    **(self._prefetcher.stats if self._prefetcher else {}),
                    "local_observer_coverage": self._local_observer_coverage,
                },
                "quality_steps_completed": len(self._quality_results),
            },
            "consolidation": consolidation_metrics,
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
        logger.info(f"Signal purity: {signal_purity:.2f} (noise={self._n_noise_defects})")
        logger.info(f"Lot L discovered: {self._lot_l_discovered} (margin={self._discovery_margin})")
        logger.info(f"Lot L recall: {lot_l_recall:.2f}")
        logger.info(f"Recall@10: {retrieval_metrics.get('recall_at_10', 0):.2f}")
        logger.info(f"Standing query registered: {self._standing_query is not None}")
        logger.info(f"Audit entries: {self.audit.entry_count}")

        return {"run_id": self.run_id, "metrics": metrics}
