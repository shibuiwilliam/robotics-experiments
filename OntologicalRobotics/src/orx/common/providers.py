"""LLM/テキスト埋め込みプロバイダIF — 3モード (openai / cache / stub)。

- openai: ライブ。スナップショット名固定・temperature 0・**ディスクへ書き抜きキャッシュ**。
- cache : `data/cache/llm` からの再生のみ。ミスは CacheMissError（実APIを叩かない）。
- stub  : 決定的なカンド応答。デモ・テスト用（ネットワーク・APIキー不要）。

キャッシュキー: sha256(model × リクエスト正規化JSON)。モデル名はコンフィグからのみ
（CLAUDE.md §6: ハードコード禁止）。APIキーは環境変数 OPENAI_API_KEY のみ。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from orx.common.schemas import StrictModel

ProviderMode = Literal["openai", "cache", "stub"]

# 既定モデル（OpenAIモデルIDは https://developers.openai.com/api/docs/models）。
# 解決順序: 実験コンフィグの明示値 > 環境変数 > ここの既定値。
# 解決後の値は ProviderConfig に格納され、構成ハッシュとマニフェストに焼き込まれる
# （PROJECT.md §8.3 の再現性: 何が使われたかは常に記録される）。
DEFAULT_LLM_MODEL = "gpt-5.4-mini"
DEFAULT_TEXT_EMBEDDING_MODEL = "text-embedding-3-small"
LLM_MODEL_ENV = "ORX_LLM_MODEL"
TEXT_EMBEDDING_MODEL_ENV = "ORX_TEXT_EMBEDDING_MODEL"


def _default_llm_model() -> str:
    return os.environ.get(LLM_MODEL_ENV, DEFAULT_LLM_MODEL)


def _default_text_embedding_model() -> str:
    return os.environ.get(TEXT_EMBEDDING_MODEL_ENV, DEFAULT_TEXT_EMBEDDING_MODEL)


class ProviderConfig(StrictModel):
    """プロバイダ設定。実験コンフィグから注入される。

    `llm_model` / `text_embedding_model` を省略すると、環境変数
    （ORX_LLM_MODEL / ORX_TEXT_EMBEDDING_MODEL）→ 既定値 の順で解決される。
    コンフィグで明示すればそれが優先される（per-experimentのピン留め）。
    """

    mode: ProviderMode = "stub"
    llm_model: str = Field(default_factory=_default_llm_model)
    text_embedding_model: str = Field(default_factory=_default_text_embedding_model)
    cache_dir: Path = Path("data/cache/llm")
    embedding_cache_dir: Path = Path("data/cache/embeddings")
    text_embedding_dim: int = 64


class LLMRequest(StrictModel):
    messages: list[dict[str, Any]]  # OpenAI chat形式 (role/content/...)
    tools: list[dict[str, Any]] | None = None


class LLMResponse(StrictModel):
    content: str | None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = False


class CacheMissError(RuntimeError):
    """cacheモードでキャッシュミス。新規記録は --record 付き実行のみ。"""


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _request_key(model: str, request: LLMRequest) -> str:
    raw = _canonical_json({"model": model, "request": request.model_dump(mode="json")})
    return hashlib.sha256(raw.encode()).hexdigest()


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


class TextEmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


# ------------------------------------------------------------------ LLM impls


class _DiskCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self.path(key)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def put(self, key: str, value: dict[str, Any]) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_canonical_json(value), encoding="utf-8")


class StubLLMClient:
    """決定的カンド応答。リクエストのハッシュから内容を再現可能に生成。"""

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = _request_key(self.model, request)
        return LLMResponse(content=f"stub-response:{key[:16]}", cached=False)


class CacheLLMClient:
    """キャッシュ再生のみ。ミスでエラー（テスト・リプレイ用）。"""

    def __init__(self, model: str, cache_dir: Path) -> None:
        self.model = model
        self._cache = _DiskCache(cache_dir)

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = _request_key(self.model, request)
        hit = self._cache.get(key)
        if hit is None:
            raise CacheMissError(
                f"LLM cache miss (model={self.model}, key={key[:16]}…). "
                "ライブ記録は mode=openai の明示実行でのみ行えます。"
            )
        return LLMResponse(**hit, cached=True)


class OpenAILLMClient:
    """ライブAPI（temperature 0固定、書き抜きキャッシュ）。テストから呼ばない。"""

    def __init__(self, model: str, cache_dir: Path) -> None:
        self.model = model
        self._cache = _DiskCache(cache_dir)
        self._client: Any = None

    def _api(self) -> Any:
        if self._client is None:
            from openai import OpenAI  # 遅延import（オフライン経路で不要）

            self._client = OpenAI()
        return self._client

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = _request_key(self.model, request)
        hit = self._cache.get(key)
        if hit is not None:
            return LLMResponse(**hit, cached=True)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": request.messages,
            "temperature": 0,
        }
        if request.tools:
            kwargs["tools"] = request.tools
        raw = self._api().chat.completions.create(**kwargs)
        choice = raw.choices[0].message
        response = LLMResponse(
            content=choice.content,
            tool_calls=[tc.model_dump() for tc in (choice.tool_calls or [])],
            prompt_tokens=raw.usage.prompt_tokens if raw.usage else 0,
            completion_tokens=raw.usage.completion_tokens if raw.usage else 0,
        )
        self._cache.put(key, response.model_dump(mode="json", exclude={"cached"}))
        return response


# ------------------------------------------------------- text embedding impls


class StubTextEmbeddingClient:
    """決定的フェイク埋め込み: sha256ストリームから単位ベクトルを生成。"""

    def __init__(self, model: str, dim: int) -> None:
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            values: list[float] = []
            counter = 0
            while len(values) < self.dim:
                h = hashlib.sha256(f"{self.model}|{text}|{counter}".encode()).digest()
                values.extend(b / 255.0 - 0.5 for b in h)
                counter += 1
            vec = values[: self.dim]
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


class CacheTextEmbeddingClient:
    def __init__(self, model: str, cache_dir: Path) -> None:
        self.model = model
        self._cache = _DiskCache(cache_dir)

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}|{text}".encode()).hexdigest()

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            hit = self._cache.get(self._key(text))
            if hit is None:
                raise CacheMissError(f"embedding cache miss (model={self.model})")
            out.append(hit["vector"])
        return out


class OpenAITextEmbeddingClient:
    def __init__(self, model: str, cache_dir: Path) -> None:
        self.model = model
        self._cache = _DiskCache(cache_dir)
        self._client: Any = None

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}|{text}".encode()).hexdigest()

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            key = self._key(text)
            hit = self._cache.get(key)
            if hit is not None:
                out.append(hit["vector"])
                continue
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI()
            raw = self._client.embeddings.create(model=self.model, input=text)
            vector = list(raw.data[0].embedding)
            self._cache.put(key, {"vector": vector})
            out.append(vector)
        return out


# ------------------------------------------------------------------ factories


def make_llm_client(config: ProviderConfig) -> LLMClient:
    if config.mode == "stub":
        return StubLLMClient(config.llm_model)
    if config.mode == "cache":
        return CacheLLMClient(config.llm_model, config.cache_dir)
    return OpenAILLMClient(config.llm_model, config.cache_dir)


def make_text_embedding_client(config: ProviderConfig) -> TextEmbeddingClient:
    if config.mode == "stub":
        return StubTextEmbeddingClient(config.text_embedding_model, config.text_embedding_dim)
    if config.mode == "cache":
        return CacheTextEmbeddingClient(config.text_embedding_model, config.embedding_cache_dir)
    return OpenAITextEmbeddingClient(config.text_embedding_model, config.embedding_cache_dir)
