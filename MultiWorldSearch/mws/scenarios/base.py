"""BaseScenario — the 8-phase lifecycle for all MWS verification scenarios.

All scenarios inherit from BaseScenario and implement the lifecycle phases.
Each phase is deterministic under a fixed seed. The lifecycle:
1. setup(seed, config)  — build MuJoCo world + business data, issue RUN_ID
2. seed_memory()        — inject precondition atoms (past skills, history)
3. inject()             — inject the condition under test (anomaly, drift, etc.)
4. perceive()           — sensors → observation atoms → storage
5. index()              — offline indexing (mock default)
6. act()                — consumers query MWS and take actions
7. evaluate()           — compute metrics, write artifacts to runs/<RUN_ID>/
8. teardown()           — release resources
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from mws.core.atom import ExperienceAtom
from mws.core.config import MWSSettings, get_settings
from mws.core.logging import get_logger
from mws.embedding.factory import create_embedder
from mws.eval.bandwidth import BandwidthMeter
from mws.eval.cost import CloudCostTracker
from mws.eval.latency import LatencyTracker
from mws.eval.report import create_manifest, save_report
from mws.retrieval.engine import RetrievalEngine
from mws.scenarios.audit import AuditLogger
from mws.scenarios.ground_truth import compute_retrieval_metrics
from mws.sim.scene import build_scene_graph_from_world
from mws.sim.world import MuJoCoWorld
from mws.storage.registry import StoreRegistry
from mws.worldmodel.scene_graph import SceneGraph

logger = get_logger(__name__)


class BaseScenario(ABC):
    """Abstract base for all MWS verification scenarios."""

    # Subclasses must set these
    name: str = ""
    description: str = ""

    def __init__(self) -> None:
        self.run_id: str = ""
        self.seed: int = 0
        self.settings: MWSSettings = get_settings()
        self.latency = LatencyTracker()
        self.cost = CloudCostTracker()
        self.bandwidth = BandwidthMeter()
        self.audit: AuditLogger | None = None
        self.engine: RetrievalEngine | None = None
        self.scene_graph: SceneGraph | None = None
        self._atoms: list[ExperienceAtom] = []
        self._metrics: dict[str, Any] = {}
        self._task_results: dict[str, Any] = {}
        # How this scenario's "agent" actually ran (M9 machine-readable):
        #   "live"    — real ADK calls to gemini-3.5-flash
        #   "mock"    — deterministic local agent (no cloud)
        #   "modeled" — no LLM agent at all; llm-cost entries are cost-model only
        self.agent_mode: str = "modeled"

    # --- Lifecycle phases (subclasses override) ---

    @abstractmethod
    def setup(self, seed: int, config: dict[str, Any]) -> None:
        """Phase 1: Build world + business data, issue RUN_ID."""

    @abstractmethod
    def seed_memory(self) -> None:
        """Phase 2: Inject precondition atoms (past skills, history records)."""

    @abstractmethod
    def inject(self) -> None:
        """Phase 3: Inject the condition under test (anomaly, drift, etc.)."""

    @abstractmethod
    def perceive(self) -> None:
        """Phase 4: Sensors → observation atoms → storage."""

    def index(self) -> None:
        """Phase 5: Offline indexing. Default: no-op (ingest handles it in mock)."""
        pass  # Intentionally empty — offline indexing is handled by ingest in mock mode

    @abstractmethod
    def act(self) -> None:
        """Phase 6: Consumers query MWS and take actions."""

    @abstractmethod
    def evaluate(self) -> dict[str, Any]:
        """Phase 7: Compute metrics, return results dict."""

    def teardown(self) -> None:
        """Phase 8: Release resources. Default: flush audit + drop per-run
        external stores (e.g. the Elasticsearch index for this run)."""
        if self.audit:
            self.audit.flush()
        if self.engine is not None:
            self.engine.stores.close()

    # --- Infrastructure helpers ---

    def _init_run(
        self,
        seed: int,
        config: dict[str, Any],
        world: MuJoCoWorld | None = None,
        world_id: str = "default",
    ) -> None:
        """Common setup: RUN_ID, settings, engine, scene graph, audit.

        If a *world* is provided the scene graph is built automatically and
        passed to the :class:`RetrievalEngine` so that relational queries
        work out of the box.
        """
        self.seed = seed
        self.run_id = f"{self.name}-{seed}-{uuid.uuid4().hex[:8]}"
        self.settings = get_settings()
        self.settings.seed = seed

        # Apply config overrides
        if "seed" in config:
            self.seed = config["seed"]

        # Init instrumentation
        self.latency = LatencyTracker()
        self.cost = CloudCostTracker()
        self.bandwidth = BandwidthMeter()

        # Init audit
        run_dir = self.settings.run_dir / self.run_id
        self.audit = AuditLogger(run_dir, self.run_id)

        # Real-LLM call recorder (IMPROVEMENT M16): live agents append every
        # real call's prompt/response/tokens to runs/<RUN_ID>/llm_calls.jsonl.
        # File creation is lazy — mock runs (no real calls) produce no file.
        from mws.core.llm_log import LLMCallRecorder

        self.llm_recorder = LLMCallRecorder(run_dir / "llm_calls.jsonl")

        # Build scene graph from the MuJoCo world (if provided)
        self.scene_graph = None
        if world is not None:
            self.scene_graph = build_scene_graph_from_world(world, world_id=world_id)

        # Init retrieval engine. One real embedding model: gemini-embedding-2
        # in live mode; the deterministic MockEmbedder stand-in in mock mode.
        embedder = create_embedder(self.settings)
        # Single source of truth for the index's space/dims is the EMBEDDER —
        # tagging stores from settings can drift (e.g. a stale env var after a
        # space bump) and silently break embedding reuse (caught by the
        # reconciliation audit during P9/E1).
        stores = StoreRegistry(
            embedding_space=embedder.space,
            embedding_dims=embedder.dims,
            vector_backend=self.settings.vector_backend,
            elasticsearch_url=self.settings.elasticsearch_url,
        )
        self.engine = RetrievalEngine(
            stores=stores,
            embedder=embedder,
            cost_tracker=self.cost,
            bandwidth_meter=self.bandwidth,
            graph=self.scene_graph,
            # Per-call latency buckets (local_ann / local_embed / gemini_embed)
            # feed the CLAUDE.md §10 decomposition with real p50/p95 samples.
            latency_tracker=self.latency,
            embedding_batch_size=self.settings.embedding_batch_size,
        )
        self._atoms = []
        self._metrics = {}
        self._task_results = {}

    def _ingest_atom(self, atom: ExperienceAtom) -> None:
        """Ingest an atom into the engine and track it."""
        assert self.engine is not None
        self.engine.ingest(atom)
        self._atoms.append(atom)

    def _ingest_atoms(self, atoms: list[ExperienceAtom]) -> None:
        """Ingest a batch of atoms — batched embedding requests (E2)."""
        assert self.engine is not None
        self.engine.ingest_batch(atoms)
        self._atoms.extend(atoms)

    def _save_results(self, metrics: dict[str, Any]) -> None:
        """Write manifest + metrics + audit to runs/<RUN_ID>/."""
        manifest = create_manifest(
            run_id=self.run_id,
            scenario=self.name,
            seed=self.seed,
        )
        save_report(
            run_id=self.run_id,
            manifest=manifest,
            metrics=metrics,
        )

    def _retrieval_metrics(
        self,
        retrieved_ids: list[str],
        relevant_ids: set[str],
    ) -> dict[str, float]:
        """Compute standard retrieval metrics."""
        return compute_retrieval_metrics(retrieved_ids, relevant_ids)

    # --- Main execution ---

    def run(
        self,
        seed: int = 0,
        config: dict[str, Any] | None = None,
        settings: MWSSettings | None = None,
    ) -> dict[str, Any]:
        """Execute the full 8-phase lifecycle.

        Returns dict with run_id, metrics, audit entries.
        """
        if settings is not None:
            self.settings = settings

        effective_config = config or {}

        logger.info(f"=== {self.name}: {self.description} ===", seed=seed)

        with self.latency.track("setup"):
            self.setup(seed, effective_config)

        with self.latency.track("seed_memory"):
            self.seed_memory()

        with self.latency.track("inject"):
            self.inject()

        with self.latency.track("perceive"):
            self.perceive()

        with self.latency.track("index"):
            self.index()

        with self.latency.track("act"):
            self.act()

        with self.latency.track("evaluate"):
            result = self.evaluate()

        self.teardown()

        logger.info(f"=== {self.name} COMPLETE ===", run_id=self.run_id)
        return result
