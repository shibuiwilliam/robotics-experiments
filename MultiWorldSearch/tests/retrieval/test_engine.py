"""Tests for unified retrieval engine."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import ConsumerType, EmbeddingSpace, EntityKind, Modality, RelationType
from mws.embedding.mock import MockEmbedder
from mws.retrieval.engine import ALL_INDICES, RetrievalEngine
from mws.retrieval.fusion import reciprocal_rank_fusion
from mws.retrieval.query import RetrievalQuery
from mws.storage.registry import StoreRegistry
from mws.worldmodel.entity import EntityNode
from mws.worldmodel.relation import Relation
from mws.worldmodel.scene_graph import SceneGraph


def _build_engine() -> RetrievalEngine:
    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    embedder = MockEmbedder(seed=42)
    return RetrievalEngine(stores=stores, embedder=embedder)


def _make_atom(
    text: str,
    x: float = 0.0,
    y: float = 0.0,
    ts: float = 100.0,
    tags: list[str] | None = None,
    fields: dict | None = None,
) -> ExperienceAtom:
    coord = SpatiotemporalCoord(x=x, y=y, z=0.0, timestamp=ts)
    atom = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=coord,
        text_summary=text,
        tags=tags or [],
        structured_fields=fields or {},
    )
    atom.provenance.add("test", "sensor", "created")
    return atom


def test_ingest_and_semantic_search() -> None:
    engine = _build_engine()
    engine.ingest(_make_atom("motor temperature anomaly detected"))
    engine.ingest(_make_atom("conveyor belt speed normal"))
    engine.ingest(_make_atom("motor vibration above threshold"))

    query = RetrievalQuery(text="motor issue", top_k=2)
    results = engine.search(query)
    assert len(results) >= 1


def test_spatial_search() -> None:
    engine = _build_engine()
    engine.ingest(_make_atom("sensor A", x=0.0, y=0.0))
    engine.ingest(_make_atom("sensor B", x=1.0, y=0.0))
    engine.ingest(_make_atom("sensor C", x=100.0, y=100.0))

    query = RetrievalQuery(position=(0.0, 0.0, 0.0), spatial_radius=2.0, top_k=5)
    results = engine.search(query)
    assert len(results) <= 3


def test_temporal_search() -> None:
    engine = _build_engine()
    engine.ingest(_make_atom("event 1", ts=10.0))
    engine.ingest(_make_atom("event 2", ts=20.0))
    engine.ingest(_make_atom("event 3", ts=100.0))

    query = RetrievalQuery(time_start=5.0, time_end=25.0, top_k=5)
    results = engine.search(query)
    assert len(results) >= 1


def test_tag_search() -> None:
    engine = _build_engine()
    engine.ingest(_make_atom("motor fault", tags=["maintenance", "motor"]))
    engine.ingest(_make_atom("normal operation", tags=["routine"]))

    query = RetrievalQuery(tags=["maintenance"], top_k=5)
    results = engine.search(query)
    assert len(results) >= 1


def test_structured_search() -> None:
    engine = _build_engine()
    engine.ingest(_make_atom("conv fault", fields={"equipment_id": "CONV-01"}))
    engine.ingest(_make_atom("pump ok", fields={"equipment_id": "PUMP-02"}))

    query = RetrievalQuery(structured_filters={"equipment_id": "CONV-01"}, top_k=5)
    results = engine.search(query)
    assert len(results) >= 1


def test_projection() -> None:
    engine = _build_engine()
    engine.ingest(_make_atom("motor fault", tags=["motor"]))
    query = RetrievalQuery(text="motor", consumer=ConsumerType.LLM, top_k=5)
    results = engine.search(query)
    projected = engine.project(results, query)
    assert len(projected) >= 1
    assert "text" in projected[0]
    assert "citation" in projected[0]


def test_empty_search() -> None:
    engine = _build_engine()
    query = RetrievalQuery(top_k=5)
    results = engine.search(query)
    assert results == []


def test_embedding_cache_deduplication() -> None:
    """Verify the content-hash cache prevents redundant embeddings."""
    engine = _build_engine()
    # Ingest two atoms with identical text — should hit cache on second
    engine.ingest(_make_atom("identical text for caching"))
    engine.ingest(_make_atom("identical text for caching"))

    assert engine.cache_stats["cache_size"] == 1, "Same text should produce one cache entry"
    assert engine.cache_stats["cache_hit_rate"] > 0, "Second ingest should be a cache hit"


def test_cost_tracking() -> None:
    """Verify embedding calls are counted."""
    engine = _build_engine()
    engine.ingest(_make_atom("track this call"))

    assert engine._cost.embedding_calls == 1
    assert engine._cost.embedding_tokens > 0


def test_bandwidth_tracking() -> None:
    """Verify bandwidth meter records hypothetical cloud traffic."""
    engine = _build_engine()
    engine.ingest(_make_atom("bandwidth test"))

    bw = engine._bandwidth.summary()
    assert bw["total_upload_bytes"] > 0
    assert bw["total_download_bytes"] > 0


# --- R1/R2: fusion-weight coverage and the relational index ---


def test_fusion_weights_cover_all_indices() -> None:
    """IMPROVEMENT R1: the default fusion-weight dict must cover every index
    the engine can fire — a missing key would silently exclude that index."""
    engine = _build_engine()
    assert set(engine.fusion_weights) == set(ALL_INDICES)


def test_unlisted_index_is_excluded_not_dominant() -> None:
    """IMPROVEMENT R1: with an EXPLICIT weights dict, an index missing from it
    contributes nothing — it must never fall back to a dominant default."""
    ranked = {
        "semantic": [("good", 0.99)],
        "relational": [("rel", 0.5)],  # not listed in weights below
    }
    weights = {"semantic": 0.4, "spatial": 0.2}
    results = reciprocal_rank_fusion(ranked, weights=weights, top_n=2)
    ids = [r.atom_id for r in results]
    assert ids[0] == "good"
    assert "rel" not in ids, "unlisted index must be excluded, not weight-1.0"


def _build_engine_with_graph() -> RetrievalEngine:
    graph = SceneGraph(world_id="test")
    graph.add_entity(
        EntityNode(entity_id="pump", kind=EntityKind.OBJECT, name="pump", position=(0, 0, 0))
    )
    graph.add_entity(
        EntityNode(entity_id="valve", kind=EntityKind.OBJECT, name="valve", position=(1, 0, 0))
    )
    graph.add_relation(
        Relation(
            source_id="pump",
            target_id="valve",
            relation_type=RelationType.SPATIAL_NEAR,
            weight=0.8,
            timestamp=0.0,
        )
    )
    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    return RetrievalEngine(stores=stores, embedder=MockEmbedder(seed=42), graph=graph)


def test_relational_index_fires_via_entity_id() -> None:
    """IMPROVEMENT R2: a query with structured_filters['entity_id'] expands
    through the scene graph and surfaces atoms of RELATED entities."""
    engine = _build_engine_with_graph()
    valve_atom = _make_atom("valve loosening skill demonstration")
    valve_atom.entity_id = "valve"
    engine.ingest(valve_atom)
    noise = _make_atom("unrelated shipping record")
    noise.entity_id = "dock"
    engine.ingest(noise)

    query = RetrievalQuery(structured_filters={"entity_id": "pump"}, top_k=5)
    results = engine.search(query)
    assert any(r.atom_id == valve_atom.atom_id for r in results)
    hit = next(r for r in results if r.atom_id == valve_atom.atom_id)
    assert "relational" in hit.scores_by_index


def test_search_with_indices_controls_relational() -> None:
    """IMPROVEMENT R1: the ablation helper can disable AND enable relational."""
    engine = _build_engine_with_graph()
    valve_atom = _make_atom("valve skill")
    valve_atom.entity_id = "valve"
    engine.ingest(valve_atom)

    query = RetrievalQuery(structured_filters={"entity_id": "pump"}, top_k=5)
    with_rel = engine.search_with_indices(query, {"relational"})
    assert any(r.atom_id == valve_atom.atom_id for r in with_rel)
    without_rel = engine.search_with_indices(query, {"structured"})
    assert all(r.atom_id != valve_atom.atom_id for r in without_rel)
