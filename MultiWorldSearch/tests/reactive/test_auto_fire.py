"""Test that standing queries fire automatically when atoms are ingested via the engine."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import EmbeddingSpace, Modality
from mws.embedding.mock import MockEmbedder
from mws.reactive.standing_query import StandingQueryEngine
from mws.retrieval.engine import RetrievalEngine
from mws.storage.registry import StoreRegistry


def test_standing_query_fires_on_engine_ingest() -> None:
    """Standing query registered before ingest fires automatically — no manual call needed."""
    sq = StandingQueryEngine()
    sq.register("leak_alert", tags=["leak", "hazard"])

    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    embedder = MockEmbedder(seed=0)
    engine = RetrievalEngine(stores=stores, embedder=embedder, standing_query_engine=sq)

    # Ingest a matching atom — should auto-fire
    coord = SpatiotemporalCoord(x=0, y=0, z=0, timestamp=100.0)
    atom = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=coord,
        text_summary="Substance leak detected",
        tags=["leak", "hazard", "room_A"],
    )
    engine.ingest(atom)

    assert sq.total_fires == 1, f"Standing query should have fired once, got {sq.total_fires}"
    assert sq.fired_log[0]["atom_id"] == atom.atom_id


def test_non_matching_atom_does_not_fire() -> None:
    """Non-matching atoms should not trigger standing queries."""
    sq = StandingQueryEngine()
    sq.register("leak_alert", tags=["leak", "hazard"])

    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    embedder = MockEmbedder(seed=0)
    engine = RetrievalEngine(stores=stores, embedder=embedder, standing_query_engine=sq)

    coord = SpatiotemporalCoord(x=0, y=0, z=0, timestamp=100.0)
    atom = ExperienceAtom(
        modality=Modality.POSE,
        coord=coord,
        text_summary="Normal body position",
        tags=["sim", "pose"],
    )
    engine.ingest(atom)

    assert sq.total_fires == 0
