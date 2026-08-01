"""P5 clients/VCR tests — modes, MissingCassette, record→replay roundtrip, fakes, cache."""

from __future__ import annotations

import numpy as np
import pytest

from clients import (
    VCR,
    ChatAdapter,
    EmbeddingAdapter,
    ERAdapter,
    FakeGeminiClient,
    MissingCassette,
    VcrMode,
)


def test_replay_missing_cassette_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    vcr = VCR(VcrMode.replay, cassette_dir=tmp_path)
    with pytest.raises(MissingCassette):
        vcr.interact("model-x", {"q": 1}, live_fn=lambda: {"never": "called"})


def test_record_then_replay_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def live() -> dict[str, int]:
        calls["n"] += 1
        return {"answer": 42}

    rec = VCR(VcrMode.record, cassette_dir=tmp_path)
    assert rec.interact("model-x", {"q": 1}, live) == {"answer": 42}
    assert rec.interact("model-x", {"q": 1}, live) == {"answer": 42}  # cached, no 2nd live call
    assert calls["n"] == 1

    play = VCR(VcrMode.replay, cassette_dir=tmp_path)
    assert play.interact("model-x", {"q": 1}, lambda: pytest.fail("should not call live")) == {
        "answer": 42
    }
    assert play.live_calls == 0


def test_key_is_stable_and_request_sensitive(tmp_path) -> None:  # type: ignore[no-untyped-def]
    rec = VCR(VcrMode.record, cassette_dir=tmp_path)
    rec.interact("m", {"a": 1, "b": 2}, lambda: "AB")
    # same request (key order irrelevant) hits the cassette; different request misses.
    play = VCR(VcrMode.replay, cassette_dir=tmp_path)
    assert play.interact("m", {"b": 2, "a": 1}, lambda: pytest.fail("miss")) == "AB"
    with pytest.raises(MissingCassette):
        play.interact("m", {"a": 1, "b": 3}, lambda: "X")


def test_fake_er_client_schema_valid_points() -> None:
    fake = FakeGeminiClient()
    pts = fake.detect_points(np.zeros((4, 4, 3), dtype=np.uint8), "point at pallets", "er", 512)
    assert pts and all({"y", "x"} <= set(p) for p in pts)


def test_er_adapter_offline_passthrough_with_fake(tmp_path) -> None:  # type: ignore[no-untyped-def]
    adapter = ERAdapter(
        vcr=VCR(VcrMode.passthrough, cassette_dir=tmp_path), backend=FakeGeminiClient()
    )
    points = adapter.detect_points(np.zeros((8, 8, 3), dtype=np.uint8), "point at pallets")
    assert len(points) >= 1
    assert 0 <= points[0].y <= 1000


def test_er_adapter_record_then_replay(tmp_path) -> None:  # type: ignore[no-untyped-def]
    img = np.ones((8, 8, 3), dtype=np.uint8)
    rec = ERAdapter(vcr=VCR(VcrMode.record, cassette_dir=tmp_path), backend=FakeGeminiClient())
    recorded = rec.detect_points(img, "q")
    play = ERAdapter(vcr=VCR(VcrMode.replay, cassette_dir=tmp_path), backend=FakeGeminiClient())
    replayed = play.detect_points(img, "q")  # served from cassette, no backend needed
    assert [(p.y, p.x) for p in recorded] == [(p.y, p.x) for p in replayed]


def test_chat_adapter_returns_schema_valid_minimal_instance(tmp_path) -> None:  # type: ignore[no-untyped-def]
    schema = {
        "type": "object",
        "required": ["ok", "count"],
        "properties": {"ok": {"type": "boolean"}, "count": {"type": "integer"}},
    }
    chat = ChatAdapter(
        vcr=VCR(VcrMode.passthrough, cassette_dir=tmp_path), backend=FakeGeminiClient()
    )
    out = chat.generate("do the thing", schema)
    assert out == {"ok": False, "count": 0}  # minimal valid instance


def test_chat_adapter_canned_intent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    fake = FakeGeminiClient()
    fake.register_generate("PLAN_E0", {"steps": ["move", "scan"]})
    chat = ChatAdapter(vcr=VCR(VcrMode.passthrough, cassette_dir=tmp_path), backend=fake)
    assert chat.generate("please PLAN_E0 now") == {"steps": ["move", "scan"]}


def test_embedding_cache_prevents_recompute(tmp_path) -> None:  # type: ignore[no-untyped-def]
    class CountingFake(FakeGeminiClient):
        calls = 0

        def embed(self, content: str, model_id: str, dim: int) -> list[float]:
            CountingFake.calls += 1
            return super().embed(content, model_id, dim)

    emb = EmbeddingAdapter(
        vcr=VCR(VcrMode.passthrough, cassette_dir=tmp_path),
        backend=CountingFake(),
        cache_path=tmp_path / "emb.sqlite",
    )
    v1 = emb.embed("hello world")
    v2 = emb.embed("hello world")  # cached
    assert v1 == v2
    assert CountingFake.calls == 1
    assert emb.cache_hits == 1
    assert len(v1) == 768 and abs(np.linalg.norm(v1) - 1.0) < 1e-6
    emb.close()
