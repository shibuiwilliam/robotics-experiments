"""Tests for federated store — multi-instance data sharing."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import EmbeddingSpace, Modality
from mws.embedding.mock import MockEmbedder
from mws.federation.store import FederatedStore
from mws.retrieval.query import RetrievalQuery


def _make_atom(text: str, entity_id: str = "", tags: list[str] | None = None) -> ExperienceAtom:
    coord = SpatiotemporalCoord(x=1.0, y=0.0, z=0.0, timestamp=100.0)
    atom = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=coord,
        text_summary=text,
        entity_id=entity_id,
        tags=tags or [],
    )
    atom.provenance.add("test", "sensor", "created")
    return atom


def test_local_ingest_isolation() -> None:
    """Atom ingested by robot_1 is NOT in robot_2's local store."""
    embedder = MockEmbedder(seed=0)
    fed = FederatedStore(embedder=embedder)
    fed.add_instance("robot_1", EmbeddingSpace.MOCK_128, 128)
    fed.add_instance("robot_2", EmbeddingSpace.MOCK_128, 128)

    atom = _make_atom("motor vibration anomaly", tags=["anomaly"])
    fed.ingest("robot_1", atom)

    # robot_1 has it locally
    inst1 = fed.get_instance("robot_1")
    assert inst1 is not None
    assert atom.atom_id in inst1.atoms

    # robot_2 does NOT have it locally
    inst2 = fed.get_instance("robot_2")
    assert inst2 is not None
    assert atom.atom_id not in inst2.atoms


def test_federated_search_finds_cross_instance() -> None:
    """Federated search finds atoms from ALL instances."""
    embedder = MockEmbedder(seed=0)
    fed = FederatedStore(embedder=embedder)
    fed.add_instance("robot_1", EmbeddingSpace.MOCK_128, 128)
    fed.add_instance("robot_2", EmbeddingSpace.MOCK_128, 128)

    atom1 = _make_atom("pump overheating alert", tags=["pump", "anomaly"])
    atom2 = _make_atom("valve maintenance complete", tags=["valve", "maintenance"])
    fed.ingest("robot_1", atom1)
    fed.ingest("robot_2", atom2)

    query = RetrievalQuery(text="pump anomaly", tags=["anomaly"], top_k=5)
    results = fed.search(query)

    # Should find atoms from both instances
    all_ids = {r.atom_id for r in results}
    assert atom1.atom_id in all_ids  # robot_1's atom found via federation


def test_total_atom_count() -> None:
    """Total count spans all instances."""
    embedder = MockEmbedder(seed=0)
    fed = FederatedStore(embedder=embedder)
    fed.add_instance("r1", EmbeddingSpace.MOCK_128, 128)
    fed.add_instance("r2", EmbeddingSpace.MOCK_128, 128)

    fed.ingest("r1", _make_atom("a"))
    fed.ingest("r1", _make_atom("b"))
    fed.ingest("r2", _make_atom("c"))

    assert fed.total_atom_count == 3


def test_stigmergy_pattern() -> None:
    """H2: robots share data through store without direct communication."""
    embedder = MockEmbedder(seed=42)
    fed = FederatedStore(embedder=embedder)
    fed.add_instance("patrol", EmbeddingSpace.MOCK_128, 128)
    fed.add_instance("manipulator", EmbeddingSpace.MOCK_128, 128)

    # Patrol robot discovers an anomaly
    anomaly = _make_atom(
        "pump_07 vibration anomaly detected",
        entity_id="pump_07",
        tags=["anomaly", "pump_07"],
    )
    fed.ingest("patrol", anomaly)

    # Manipulator (different instance) queries — finds patrol's atom via federation
    query = RetrievalQuery(
        text="pump_07 maintenance",
        tags=["pump_07"],
        top_k=5,
    )
    results = fed.search(query)
    found_ids = {r.atom_id for r in results}
    assert anomaly.atom_id in found_ids, "Manipulator should find patrol's atom via federation"


def test_federated_search_counts_embeddings_once_per_unique_query() -> None:
    """IMPROVEMENT M12: federated query embeddings are counted by the injected
    cost tracker (exactly once per unique query) and timed in a latency bucket;
    repeated identical queries hit the content-hash cache."""
    from mws.core.types import EmbeddingSpace
    from mws.embedding.mock import MockEmbedder
    from mws.eval.cost import CloudCostTracker
    from mws.eval.latency import LatencyTracker
    from mws.federation.store import FederatedStore
    from mws.retrieval.query import RetrievalQuery

    cost = CloudCostTracker()
    latency = LatencyTracker()
    fed = FederatedStore(embedder=MockEmbedder(seed=0), cost_tracker=cost, latency_tracker=latency)
    fed.add_instance("r1", EmbeddingSpace.MOCK_128, 128)

    query = RetrievalQuery(text="defect pattern", top_k=5)
    fed.search(query)
    assert cost.embedding_calls == 1
    fed.search(query)  # identical query → cache hit, no second embed
    assert cost.embedding_calls == 1
    fed.search(RetrievalQuery(text="another query", top_k=5))
    assert cost.embedding_calls == 2
    # Latency bucket sampled per actual embed call
    assert latency.summary().get("local_embed_count") == 2.0


def test_instance_ingest_fallback_embed_is_cost_counted() -> None:
    """IMPROVEMENT P9 regression: an atom arriving WITHOUT a reusable
    embedding (e.g. a store/embedder space drift after the v2 bump) must be
    embedded through the federation's TRACKED path — never an uncounted
    direct call (the class of leak the reconciliation audit caught live)."""
    from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
    from mws.core.types import EmbeddingSpace, Modality
    from mws.embedding.mock import MockEmbedder
    from mws.eval.cost import CloudCostTracker
    from mws.federation.store import FederatedStore

    cost = CloudCostTracker()
    fed = FederatedStore(embedder=MockEmbedder(seed=0), cost_tracker=cost)
    # Deliberately tag the instance store with a DIFFERENT space (same
    # dims) than the embedder produces — reuse can never hit, forcing the
    # fallback embed.
    fed.add_instance("r1", EmbeddingSpace.GEMMA_128, 128)

    atom = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=1.0),
        text_summary="noise defect observation",
    )
    atom.provenance.add("test", "sensor", "created")
    fed.ingest("r1", atom)
    assert cost.embedding_requests == 1, "fallback embed bypassed the cost tracker"
    # Same text again (other instance) → shared content-hash cache, no new call
    fed.add_instance("r2", EmbeddingSpace.GEMMA_128, 128)
    atom2 = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=2.0),
        text_summary="noise defect observation",
    )
    atom2.provenance.add("test", "sensor", "created")
    fed.ingest("r2", atom2)
    assert cost.embedding_requests == 1
