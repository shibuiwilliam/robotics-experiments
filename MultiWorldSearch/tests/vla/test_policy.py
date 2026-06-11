"""Tests for retrieval-augmented VLA policy."""

from mws.core.types import EmbeddingSpace
from mws.embedding.mock import MockEmbedder
from mws.retrieval.engine import RetrievalEngine
from mws.storage.registry import StoreRegistry
from mws.vla.policy import RetrievalAugmentedPolicy
from mws.vla.skill_atoms import create_skill_demo_atom


def test_policy_act() -> None:
    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    embedder = MockEmbedder(seed=0)
    engine = RetrievalEngine(stores=stores, embedder=embedder)

    # Ingest a skill demo
    skill = create_skill_demo_atom("bearing_replacement", "conveyor_motor", seed=0)
    engine.ingest(skill)

    policy = RetrievalAugmentedPolicy(retrieval_engine=engine, seed=0)
    action = policy.act(
        observation={"position": (2.0, 0.0, 0.5)},
        task="replace bearing",
    )
    assert "action_vector" in action
    assert action["type"] == "maintenance_action"


def test_trajectory_similarity_with_skill() -> None:
    """When a skill demo is retrieved, trajectory_similarity must be > 0.9."""
    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    embedder = MockEmbedder(seed=0)
    engine = RetrievalEngine(stores=stores, embedder=embedder)

    skill = create_skill_demo_atom("bearing_replacement", "conveyor_motor", seed=0)
    engine.ingest(skill)

    policy = RetrievalAugmentedPolicy(retrieval_engine=engine, seed=0)
    action = policy.act(
        observation={"position": (2.5, 1.0, 0.3)},
        task="bearing_replacement conveyor_motor skill_demo",
    )
    assert action["used_retrieval"] is True
    assert action["trajectory_similarity"] > 0.9, (
        f"trajectory_similarity={action['trajectory_similarity']:.3f}, expected > 0.9"
    )
    assert action["trajectory"] is not None


def test_no_skill_falls_back_to_random() -> None:
    """Without a matching skill, VLA uses random actions with low confidence."""
    stores = StoreRegistry(embedding_space=EmbeddingSpace.MOCK_128, embedding_dims=128)
    embedder = MockEmbedder(seed=0)
    engine = RetrievalEngine(stores=stores, embedder=embedder)

    # No skill ingested
    policy = RetrievalAugmentedPolicy(retrieval_engine=engine, seed=0)
    action = policy.act(
        observation={"position": (0.0, 0.0, 0.0)},
        task="unknown task",
    )
    assert action["used_retrieval"] is False
    assert action["confidence"] < 0.5
    assert action["trajectory_similarity"] == 0.0


def test_skill_atom_creation() -> None:
    atom = create_skill_demo_atom("welding", "pipe_joint", seed=42)
    assert atom.modality == "skill_demo"
    assert "welding" in atom.tags
    assert atom.payload["trajectory"] is not None
