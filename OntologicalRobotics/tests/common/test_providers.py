from pathlib import Path

import pytest

from orx.common.providers import (
    CacheLLMClient,
    CacheMissError,
    LLMRequest,
    LLMResponse,
    ProviderConfig,
    StubLLMClient,
    StubTextEmbeddingClient,
    _DiskCache,
    _request_key,
    make_llm_client,
    make_text_embedding_client,
)

REQ = LLMRequest(messages=[{"role": "user", "content": "hello"}])


def test_stub_llm_deterministic() -> None:
    client = StubLLMClient("stub-model")
    assert client.complete(REQ) == client.complete(REQ)
    other = LLMRequest(messages=[{"role": "user", "content": "bye"}])
    assert client.complete(REQ).content != client.complete(other).content


def test_cache_mode_miss_raises(tmp_path: Path) -> None:
    client = CacheLLMClient("m", tmp_path)
    with pytest.raises(CacheMissError):
        client.complete(REQ)


def test_cache_mode_hit(tmp_path: Path) -> None:
    key = _request_key("m", REQ)
    _DiskCache(tmp_path).put(
        key, LLMResponse(content="hi").model_dump(mode="json", exclude={"cached"})
    )
    response = CacheLLMClient("m", tmp_path).complete(REQ)
    assert response.content == "hi"
    assert response.cached


def test_stub_text_embedding_deterministic_unit_norm() -> None:
    client = StubTextEmbeddingClient("m", dim=64)
    [v1], [v2] = client.embed(["abc"]), client.embed(["abc"])
    assert v1 == v2
    assert len(v1) == 64
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-9
    assert client.embed(["abc"]) != client.embed(["xyz"])


def test_factories_respect_mode(tmp_path: Path) -> None:
    cfg = ProviderConfig(mode="stub", cache_dir=tmp_path, embedding_cache_dir=tmp_path)
    assert isinstance(make_llm_client(cfg), StubLLMClient)
    assert isinstance(make_text_embedding_client(cfg), StubTextEmbeddingClient)
    cfg2 = ProviderConfig(mode="cache", cache_dir=tmp_path, embedding_cache_dir=tmp_path)
    assert isinstance(make_llm_client(cfg2), CacheLLMClient)
