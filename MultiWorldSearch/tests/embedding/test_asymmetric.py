"""IMPROVEMENT P9 — gemini-embedding-2 doc-compliance unit tests (E1/E2/E4).

A fake genai client captures requests so the teacher adapter is exercised
without network access. The exact prefix strings and the one-request batch
contract come from the official docs (see mws/embedding/teacher.py header).
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from mws.core.config import MWSSettings
from mws.core.types import CloudMode, EmbeddingSpace
from mws.embedding.mock import MockEmbedder
from mws.embedding.teacher import (
    DOCUMENT_PREFIX_FMT,
    QUERY_PREFIX_FMT,
    GeminiTeacherEmbedder,
)


def _fake_vector(text: str, dims: int = 768) -> list[float]:
    """Deterministic per-text fake vector."""
    h = hashlib.sha256(text.encode()).digest()
    return [(h[i % len(h)] - 128) / 128.0 for i in range(dims)]


class _FakeModels:
    """Captures embed_content requests; one embedding per Content (or str)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def embed_content(self, *, model: str, contents, config) -> SimpleNamespace:
        self.calls.append({"model": model, "contents": contents, "config": config})
        texts = [contents] if isinstance(contents, str) else [c.parts[0].text for c in contents]
        return SimpleNamespace(embeddings=[SimpleNamespace(values=_fake_vector(t)) for t in texts])


def _fake_teacher() -> tuple[GeminiTeacherEmbedder, _FakeModels]:
    teacher = GeminiTeacherEmbedder(api_key="test-key-not-real")
    fake = _FakeModels()
    teacher._client = SimpleNamespace(models=fake)
    return teacher, fake


# --- E1: asymmetric task-instruction prefixes ---


def test_teacher_prefix_strings_match_official_docs() -> None:
    assert QUERY_PREFIX_FMT == "task: search result | query: {content}"
    assert DOCUMENT_PREFIX_FMT == "title: none | text: {content}"
    teacher, _ = _fake_teacher()
    assert teacher.format_query("pump bearing") == "task: search result | query: pump bearing"
    assert teacher.format_document("pump bearing") == "title: none | text: pump bearing"


def test_teacher_embeds_prefixed_strings_with_distinct_hashes() -> None:
    """The same raw text embeds differently (and caches separately) per role."""
    teacher, fake = _fake_teacher()
    doc = teacher.embed_document("valve_03 manual")
    query = teacher.embed_query("valve_03 manual")

    sent = [c["contents"] for c in fake.calls]
    assert sent == [
        "title: none | text: valve_03 manual",
        "task: search result | query: valve_03 manual",
    ]
    # Hash covers the FINAL prefixed string → distinct cache entries (correct).
    assert doc.content_hash != query.content_hash
    assert doc.content_hash == teacher._content_hash("title: none | text: valve_03 manual")
    assert doc.vector != query.vector


def test_teacher_default_space_is_v2() -> None:
    """E1 space bump: the prefixed scheme lives in gemini2-768-v2."""
    teacher, _ = _fake_teacher()
    assert teacher.space == EmbeddingSpace.GEMINI_768_V2
    assert str(teacher.space) == "gemini2-768-v2"


def test_factory_live_teacher_uses_v2_space() -> None:
    from mws.embedding.factory import create_embedder

    settings = MWSSettings(cloud_mode=CloudMode.LIVE, google_api_key="test-key-not-real")
    teacher = create_embedder(settings, role="teacher")
    assert teacher.space == EmbeddingSpace.GEMINI_768_V2


def test_v1_and_v2_vectors_never_share_an_index() -> None:
    """Embedding-space discipline: a v2 store rejects nothing silently —
    spaces are distinct enum members so (hash, space) cache keys and store
    tags can never collide between schemes."""
    assert EmbeddingSpace.GEMINI_768 != EmbeddingSpace.GEMINI_768_V2
    from mws.embedding.base import EmbeddingResult
    from mws.embedding.cache import EmbeddingCache

    cache = EmbeddingCache()
    cache.put(
        EmbeddingResult(vector=[0.0], space=EmbeddingSpace.GEMINI_768, content_hash="h", dims=1)
    )
    assert cache.get("h", EmbeddingSpace.GEMINI_768_V2) is None


def test_mock_embedder_is_symmetric_and_unchanged() -> None:
    """E1 must not change mock behavior: formatting is identity, so
    embed_document/embed_query equal the historical embed_text exactly."""
    emb = MockEmbedder(seed=0)
    text = "forklift_12 battery low"
    assert emb.format_document(text) == text
    assert emb.format_query(text) == text
    assert emb.embed_document(text).vector == emb.embed_text(text).vector
    assert emb.embed_query(text).content_hash == emb.embed_text(text).content_hash


# --- E2: single-request multi-content batch ---


def test_embed_batch_is_one_request_with_per_text_vectors() -> None:
    teacher, fake = _fake_teacher()
    texts = [f"document number {i}" for i in range(10)]
    results = teacher.embed_batch(texts)

    assert len(fake.calls) == 1, "embed_batch must issue exactly ONE API request"
    assert len(results) == 10
    assert len({r.content_hash for r in results}) == 10
    for text, result in zip(texts, results, strict=True):
        assert result.content_hash == teacher._content_hash(text)
        assert result.vector == _fake_vector(text)
        assert result.space == EmbeddingSpace.GEMINI_768_V2


def test_embed_batch_empty_is_no_request() -> None:
    teacher, fake = _fake_teacher()
    assert teacher.embed_batch([]) == []
    assert fake.calls == []


def test_embed_batch_length_mismatch_raises() -> None:
    teacher, _ = _fake_teacher()
    teacher._client = SimpleNamespace(
        models=SimpleNamespace(
            embed_content=lambda **kw: SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0])])
        )
    )
    with pytest.raises(RuntimeError, match="returned 1 vectors for 2"):
        teacher.embed_batch(["a", "b"])


# --- E4: typed config ---


def test_embed_calls_use_typed_config_with_dims() -> None:
    from google.genai import types

    teacher, fake = _fake_teacher()
    teacher.embed_text("hello")
    teacher.embed_batch(["a", "b"])
    for call in fake.calls:
        assert isinstance(call["config"], types.EmbedContentConfig)
        assert call["config"].output_dimensionality == 768
