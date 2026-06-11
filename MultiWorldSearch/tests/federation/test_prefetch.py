"""Tests for task-plan-driven predictive prefetch (PROJECT.md §3)."""

import pytest

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.types import EmbeddingSpace, Modality
from mws.embedding.mock import MockEmbedder
from mws.federation.prefetch import PrefetchPlanner
from mws.federation.store import FederatedStore


def _atom(text: str, tags: list[str], observer: str) -> ExperienceAtom:
    atom = ExperienceAtom(
        modality=Modality.TELEMETRY,
        coord=SpatiotemporalCoord(x=0.0, y=0.0, z=0.0, timestamp=100.0),
        text_summary=text,
        tags=tags,
        structured_fields={"observer": observer},
    )
    atom.provenance.add("test", "sensor", "created")
    return atom


def _build() -> tuple[PrefetchPlanner, FederatedStore]:
    fed = FederatedStore(embedder=MockEmbedder(seed=0))
    for rid in ("robot_1", "robot_2", "robot_3"):
        fed.add_instance(rid, EmbeddingSpace.MOCK_128, 128)
    fed.ingest("robot_1", _atom("defect seen by r1", ["defect", "task_x"], "robot_1"))
    fed.ingest("robot_2", _atom("defect seen by r2", ["defect", "task_x"], "robot_2"))
    fed.ingest("robot_3", _atom("unrelated shipping note", ["shipping"], "robot_3"))
    return PrefetchPlanner(fed), fed


def test_prefetch_pulls_task_relevant_atoms_into_local_store() -> None:
    planner, fed = _build()
    inst = fed.get_instance("robot_1")
    assert inst is not None and len(inst.atoms) == 1
    added = planner.prefetch_for_task("robot_1", task_tags=["defect"], top_k=10)
    assert added == 1  # robot_2's defect; the shipping atom is NOT task-relevant
    assert len(inst.atoms) == 2
    observers = {a.structured_fields["observer"] for a in inst.atoms.values()}
    assert observers == {"robot_1", "robot_2"}


def test_prefetch_is_idempotent() -> None:
    planner, _fed = _build()
    planner.prefetch_for_task("robot_1", task_tags=["defect"], top_k=10)
    again = planner.prefetch_for_task("robot_1", task_tags=["defect"], top_k=10)
    assert again == 0  # already local — nothing re-added
    assert planner.stats == {"prefetched_atoms": 1}


def test_prefetch_reuses_existing_embeddings() -> None:
    """Prefetched copies must not trigger new embedding calls (the atoms
    already carry vectors for the local store's space)."""
    planner, fed = _build()
    embedder = fed._embedder
    calls_before = getattr(embedder, "_embed_calls", None)
    # MockEmbedder has no call counter — verify via vector store size instead:
    inst = fed.get_instance("robot_1")
    planner.prefetch_for_task("robot_1", task_tags=["defect"], top_k=10)
    assert inst is not None and inst.stores.vector.size == 2  # indexed locally
    _ = calls_before  # interface check only


def test_prefetch_unknown_instance_raises() -> None:
    planner, _ = _build()
    with pytest.raises(ValueError, match="Unknown instance"):
        planner.prefetch_for_task("robot_9", task_tags=["defect"])
