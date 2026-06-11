"""Tests for QoR-aware query routing (IMPROVEMENT R8)."""

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import EmbeddingSpace, Modality
from mws.embedding.mock import MockEmbedder
from mws.federation.router import QoRRouter
from mws.federation.store import FederatedStore
from mws.retrieval.query import RetrievalQuery


def _atom(text: str, tags: list[str]) -> ExperienceAtom:
    atom = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=100.0),
        text_summary=text,
        tags=tags,
    )
    atom.provenance.add("test", "sensor", "created")
    return atom


def _build() -> tuple[QoRRouter, FederatedStore]:
    fed = FederatedStore(embedder=MockEmbedder(seed=0))
    for rid in ("robot_1", "robot_2"):
        fed.add_instance(rid, EmbeddingSpace.MOCK_128, 128)
    fed.ingest("robot_1", _atom("local defect on my route", ["defect", "local"]))
    fed.ingest("robot_2", _atom("remote defect elsewhere", ["defect", "remote"]))
    return QoRRouter(fed), fed


def test_strict_freshness_routes_local() -> None:
    """qor_freshness=strict with a local instance hits ONLY the local store."""
    router, _ = _build()
    query = RetrievalQuery(
        tags=["defect"],
        structured_filters={"qor_freshness": "strict"},
        top_k=5,
    )
    results = router.route(query, local_instance_id="robot_1")
    assert len(results) == 1  # robot_2's atom must NOT appear
    assert "local" in results[0].text_summary
    assert router.stats == {"local_hits": 1, "federated_hits": 0}


def test_default_routes_federated() -> None:
    """Without strict freshness, the query fans out to all instances."""
    router, _ = _build()
    query = RetrievalQuery(text="defect", tags=["defect"], top_k=5)
    results = router.route(query, local_instance_id="robot_1")
    ids = {r.text_summary for r in results}
    assert any("remote" in t for t in ids), "federated view must include robot_2"
    assert router.stats["federated_hits"] == 1


def test_strict_falls_back_to_federated_when_local_empty() -> None:
    """Strict freshness falls back to the federation if the local store has
    nothing relevant."""
    router, fed = _build()
    fed.add_instance("robot_3", EmbeddingSpace.MOCK_128, 128)  # empty local store
    query = RetrievalQuery(
        tags=["defect"],
        structured_filters={"qor_freshness": "strict"},
        top_k=5,
    )
    results = router.route(query, local_instance_id="robot_3")
    assert len(results) >= 1
    assert router.stats["federated_hits"] == 1
