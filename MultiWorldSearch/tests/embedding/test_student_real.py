"""Tests for the REAL local student embedder (H7).

Marked @pytest.mark.student: requires the 'student' extra and a model
download, so deselected by default (like live cloud tests).
"""

import pytest

pytest.importorskip("sentence_transformers")

from mws.embedding.student import LocalStudentEmbedder

# The ungated fallback model — EmbeddingGemma is HF-gated and needs a token.
_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


@pytest.mark.student
def test_real_student_embeds_deterministically() -> None:
    emb = LocalStudentEmbedder(model_name=_MODEL)
    assert emb.is_stub is False
    assert emb.dims == 384
    r1 = emb.embed_text("pump bearing vibration anomaly")
    r2 = emb.embed_text("pump bearing vibration anomaly")
    assert r1.vector == r2.vector
    assert r1.dims == 384
    assert str(r1.space) == "minilm-384-v1"


@pytest.mark.student
def test_real_student_semantic_neighbors() -> None:
    """Real embeddings put paraphrases closer than unrelated text — something
    the hash-based stub can never do."""
    import numpy as np

    emb = LocalStudentEmbedder(model_name=_MODEL)
    a = np.array(emb.embed_text("the pump bearing is vibrating heavily").vector)
    b = np.array(emb.embed_text("strong vibration detected on the pump bearing").vector)
    c = np.array(emb.embed_text("the cafeteria serves soup on Fridays").vector)
    sim_ab = float(np.dot(a, b))
    sim_ac = float(np.dot(a, c))
    assert sim_ab > sim_ac + 0.2, f"paraphrase {sim_ab:.2f} vs unrelated {sim_ac:.2f}"


def test_unknown_student_model_rejected() -> None:
    """Embedding-space discipline: unregistered models are refused."""
    with pytest.raises(ValueError, match="EmbeddingSpace"):
        LocalStudentEmbedder(model_name="some/unknown-model")
