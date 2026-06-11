"""Tests for ablation study framework."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import EmbeddingSpace, Modality
from mws.embedding.mock import MockEmbedder
from mws.eval.ablation import run_ablation
from mws.retrieval.engine import RetrievalEngine
from mws.retrieval.query import RetrievalQuery
from mws.storage.registry import StoreRegistry


def _build_ablation_engine() -> tuple[RetrievalEngine, set[str]]:
    """Build an engine with 10 atoms for ablation testing."""
    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    embedder = MockEmbedder(seed=42)
    engine = RetrievalEngine(stores=stores, embedder=embedder)

    relevant_ids = set()
    for i in range(10):
        is_relevant = i < 5
        coord = SpatiotemporalCoord(
            x=float(i),
            y=0.0,
            z=0.0,
            timestamp=100.0 + i,
        )
        atom = ExperienceAtom(
            modality=Modality.TELEMETRY,
            coord=coord,
            text_summary="pump maintenance anomaly bearing"
            if is_relevant
            else f"unrelated noise {i}",
            entity_id="pump_07" if is_relevant else f"other_{i}",
            tags=["maintenance", "pump_07"] if is_relevant else ["noise"],
            structured_fields={"equipment_id": "pump_07"} if is_relevant else {},
        )
        atom.provenance.add("test", "sensor", "created")
        engine.ingest(atom)
        if is_relevant:
            relevant_ids.add(atom.atom_id)

    return engine, relevant_ids


def test_ablation_returns_all_configurations() -> None:
    engine, relevant_ids = _build_ablation_engine()
    query = RetrievalQuery(
        text="pump maintenance anomaly",
        tags=["maintenance", "pump_07"],
        structured_filters={"equipment_id": "pump_07"},
        top_k=10,
    )
    results = run_ablation(engine, query, relevant_ids)

    from mws.eval.ablation import DEFAULT_CONFIGS

    assert "semantic_only" in results
    assert "all_indices" in results
    assert "all_except_relational" in results  # relational control row (R2)
    assert len(results) == len(DEFAULT_CONFIGS)


def test_ablation_metrics_are_valid() -> None:
    engine, relevant_ids = _build_ablation_engine()
    query = RetrievalQuery(
        text="pump maintenance anomaly",
        tags=["maintenance"],
        top_k=10,
    )
    results = run_ablation(engine, query, relevant_ids)

    for config_name, metrics in results.items():
        assert 0.0 <= metrics["recall_at_5"] <= 1.0, f"{config_name} recall@5 out of range"
        assert 0.0 <= metrics["recall_at_10"] <= 1.0, f"{config_name} recall@10 out of range"
        assert 0.0 <= metrics["mrr"] <= 1.0, f"{config_name} MRR out of range"
        assert metrics["n_results"] >= 0


def test_more_indices_do_not_decrease_coverage() -> None:
    """With more indices enabled, we should find at least as many results."""
    engine, relevant_ids = _build_ablation_engine()
    query = RetrievalQuery(
        text="pump maintenance",
        tags=["maintenance", "pump_07"],
        structured_filters={"equipment_id": "pump_07"},
        top_k=10,
    )
    results = run_ablation(engine, query, relevant_ids)

    # More indices = more candidates for fusion = at least as many results
    assert results["all_indices"]["n_results"] >= results["semantic_only"]["n_results"]
