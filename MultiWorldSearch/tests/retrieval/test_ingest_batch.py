"""IMPROVEMENT P9/E2 — engine.ingest_batch: batched embedding requests.

Acceptance: one API request per chunk (requests/texts split in the cost
tracker), duplicate texts embedded once, cache reused, and search results
IDENTICAL to the per-atom ingest path (golden no-regression).
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import EmbeddingSpace, Modality
from mws.embedding.mock import MockEmbedder
from mws.eval.cost import CloudCostTracker
from mws.retrieval.engine import RetrievalEngine
from mws.retrieval.query import RetrievalQuery
from mws.storage.registry import StoreRegistry


def _make_atom(text: str, ts: float = 100.0) -> ExperienceAtom:
    atom = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=ts),
        text_summary=text,
        tags=[],
        structured_fields={},
    )
    atom.provenance.add("test", "sensor", "created")
    return atom


def _build_engine(batch_size: int = 64) -> tuple[RetrievalEngine, CloudCostTracker]:
    cost = CloudCostTracker()
    engine = RetrievalEngine(
        stores=StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128),
        embedder=MockEmbedder(seed=42),
        cost_tracker=cost,
        embedding_batch_size=batch_size,
    )
    return engine, cost


def test_ingest_batch_one_request_many_texts() -> None:
    engine, cost = _build_engine()
    atoms = [_make_atom(f"observation number {i}") for i in range(10)]
    engine.ingest_batch(atoms)

    assert cost.embedding_requests == 1
    assert cost.embedding_texts == 10
    assert cost.embedding_calls == 1  # compat alias = requests
    assert engine.atom_count == 10
    for atom in atoms:
        assert atom.get_embedding(EmbeddingSpace.MOCK_128) is not None


def test_ingest_batch_chunks_by_configured_size() -> None:
    engine, cost = _build_engine(batch_size=4)
    engine.ingest_batch([_make_atom(f"text {i}") for i in range(10)])
    assert cost.embedding_requests == 3  # 4 + 4 + 2
    assert cost.embedding_texts == 10


def test_ingest_batch_duplicate_texts_embedded_once() -> None:
    engine, cost = _build_engine()
    atoms = [_make_atom("identical summary"), _make_atom("identical summary", ts=101.0)]
    engine.ingest_batch(atoms)
    assert cost.embedding_texts == 1
    for atom in atoms:
        assert atom.get_embedding(EmbeddingSpace.MOCK_128) is not None


def test_ingest_batch_reuses_cache_from_prior_ingest() -> None:
    engine, cost = _build_engine()
    engine.ingest(_make_atom("already indexed text"))
    assert cost.embedding_requests == 1

    engine.ingest_batch([_make_atom("already indexed text", ts=200.0), _make_atom("fresh text")])
    # Only the fresh text needs a request; the duplicate hits the cache.
    assert cost.embedding_requests == 2
    assert cost.embedding_texts == 2


def test_ingest_batch_results_identical_to_single_ingest() -> None:
    """Golden no-regression: the batch path must rank exactly like ingest()."""
    texts = [
        "motor temperature anomaly detected",
        "conveyor belt speed normal",
        "motor vibration above threshold",
        "forklift battery low in bay 3",
        "valve pressure within limits",
    ]
    single, _ = _build_engine()
    for t in texts:
        single.ingest(_make_atom(t))
    batched, _ = _build_engine()
    batched.ingest_batch([_make_atom(t) for t in texts])

    query = RetrievalQuery(text="motor issue", top_k=5)
    single_rank = [(r.text_summary, round(r.score, 9)) for r in single.search(query)]
    batched_rank = [(r.text_summary, round(r.score, 9)) for r in batched.search(query)]
    assert single_rank == batched_rank


def test_ingest_batch_skips_atoms_with_existing_embeddings() -> None:
    engine, cost = _build_engine()
    atom = _make_atom("prefetched copy")
    result = engine.embedder.embed_document("prefetched copy")
    atom.add_embedding(result.space, result.vector, result.content_hash)

    engine.ingest_batch([atom])
    assert cost.embedding_requests == 0
    assert engine.atom_count == 1
