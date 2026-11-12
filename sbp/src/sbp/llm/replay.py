"""replay / record / live の切り替え（LLM 利用ポリシー、M6-02）。

> ``SBP_LLM_MODE=replay | record | live``。既定は ``replay``。``live`` は開発者が明示したときだけ。
> fixture が無ければ黙って live に落ちず失敗させる。

**「黙って live に落ちない」が最重要である。** fixture が無いときに実 API を呼ぶ実装にすると、
CI がネットワークに出て、課金が発生し、テストが非決定的になる。しかも**気付かない**——通って
しまうからである。そのため fixture 欠落は :class:`FixtureMissingError` にする。

**``record`` は明示的な開発者の操作である。** fixture を録り直すときだけ使う。``record`` でも
fixture があれば再生する（既存の fixture を上書きしない）——上書きを既定にすると、意図しない
応答の変化が差分として現れないまま取り込まれる。

fixture は ``tests/fixtures/llm/<task>/<hash>.json``。ハッシュはメッセージ・スキーマ・プロンプト版の
すべてを含む（:func:`~sbp.llm.base.request_digest`）ので、入力を変えれば別の fixture を要求する。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sbp.core.errors import FixtureMissingError, LlmError
from sbp.core.logging import get_logger
from sbp.llm.base import (
    LlmTask,
    Provider,
    ProviderResponse,
    TokenUsage,
    UsageLedger,
    request_digest,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sbp.llm.base import Message

__all__ = ["LlmMode", "ReplayProvider", "fixture_path", "resolve_provider"]

log = get_logger(__name__)

_FIXTURE_ROOT = Path("tests/fixtures/llm")
"""fixture の既定の置き場（リポジトリ相対）。"""


class LlmMode(StrEnum):
    """LLM の実行モード。"""

    REPLAY = "replay"
    """fixture から再生する。ネットワークを使わない（既定）。"""
    RECORD = "record"
    """fixture が無ければ live を呼んで保存する。開発者が明示したときだけ。"""
    LIVE = "live"
    """常に live を呼ぶ。fixture は読まない。"""

    @property
    def uses_network(self) -> bool:
        """ネットワークを使う可能性があるか。"""
        return self is not LlmMode.REPLAY


def fixture_path(task: LlmTask, digest: str, *, root: Path | None = None) -> Path:
    """fixture のパス（``tests/fixtures/llm/<task>/<hash>.json``）。

    Args:
        task: 用途。
        digest: 要求の内容ハッシュ。
        root: fixture の根（既定はリポジトリ相対）。
    """
    return (root or _FIXTURE_ROOT) / task.value / f"{digest}.json"


@dataclass(slots=True)
class ReplayProvider:
    """fixture から再生するプロバイダ。

    Attributes:
        mode: 実行モード。
        live: ``record`` / ``live`` で使う実プロバイダ（``replay`` では不要）。
        root: fixture の根。
        ledger: トークン使用量の台帳。
    """

    mode: LlmMode = LlmMode.REPLAY
    live: Provider | None = None
    root: Path | None = None
    ledger: UsageLedger | None = None

    def __post_init__(self) -> None:
        """モードと実プロバイダの組み合わせを検証する。"""
        if self.mode is not LlmMode.REPLAY and self.live is None:
            raise LlmError(
                f"mode={self.mode.value} needs a live provider; only replay can run without one"
            )
        if self.ledger is None:
            self.ledger = UsageLedger()

    @property
    def name(self) -> str:
        """プロバイダ名。``replay`` では ``replay``、それ以外は実プロバイダ名。"""
        if self.mode is LlmMode.REPLAY or self.live is None:
            return "replay"
        return self.live.name

    def complete_structured(
        self,
        messages: Sequence[Message],
        *,
        schema: dict[str, Any],
        task: LlmTask,
        prompt_version: str,
    ) -> ProviderResponse:
        """構造化出力を得る（モードに応じて fixture か live）。

        Args:
            messages: メッセージ列。
            schema: 期待する JSON Schema。
            task: 用途。
            prompt_version: プロンプトの版。

        Raises:
            FixtureMissingError: ``replay`` で fixture が無い場合。
            LlmError: ``live`` が必要なのに無い場合。
        """
        digest = request_digest(messages, schema=schema, prompt_version=prompt_version)
        path = fixture_path(task, digest, root=self.root)

        if self.mode is not LlmMode.LIVE and path.is_file():
            response = self._load(path, task=task, prompt_version=prompt_version, digest=digest)
            assert self.ledger is not None
            self.ledger.record(response)
            return response

        if self.mode is LlmMode.REPLAY:
            raise FixtureMissingError(
                f"no LLM fixture at {path} for task {task.value} (digest {digest}). "
                "replay does not fall back to the network: a silent fallback makes CI "
                "non-deterministic, costs money, and passes without anyone noticing. "
                f"Record it with SBP_LLM_MODE=record."
            )

        if self.live is None:  # pragma: no cover — __post_init__ が保証する
            raise LlmError(f"mode={self.mode.value} requires a live provider")
        response = self.live.complete_structured(
            messages, schema=schema, task=task, prompt_version=prompt_version
        )
        if self.mode is LlmMode.RECORD:
            self._save(path, response, digest=digest)
        assert self.ledger is not None
        self.ledger.record(response)
        return response

    def embed(self, texts: Sequence[str], *, task: LlmTask) -> list[list[float]]:
        """埋め込みを得る。

        埋め込みは fixture にしない。次元が大きく差分が読めないためで、``replay`` では
        **決定的な疑似埋め込み**を返す（内容のハッシュから作る）。テストで必要なのは「同じ文字列が
        同じベクトルになる」ことと「違う文字列が違うベクトルになる」ことだけである。

        Args:
            texts: 埋め込む文字列。
            task: 用途。

        Raises:
            LlmError: ``live`` が必要なのに無い場合。
        """
        if self.mode is LlmMode.REPLAY:
            return [_pseudo_embedding(text) for text in texts]
        if self.live is None:  # pragma: no cover
            raise LlmError(f"mode={self.mode.value} requires a live provider")
        return self.live.embed(texts, task=task)

    def _load(
        self, path: Path, *, task: LlmTask, prompt_version: str, digest: str
    ) -> ProviderResponse:
        """fixture を読む。"""
        payload = json.loads(path.read_text(encoding="utf-8"))
        usage = payload.get("usage", {})
        log.info("llm.fixture_replayed", task=task.value, digest=digest, path=str(path))
        return ProviderResponse(
            content=payload["content"],
            provider=payload.get("provider", "replay"),
            model=payload.get("model", "fixture"),
            prompt_version=payload.get("prompt_version", prompt_version),
            prompt_hash=payload.get("prompt_hash", digest),
            usage=TokenUsage(
                prompt=int(usage.get("prompt", 0)), completion=int(usage.get("completion", 0))
            ),
            task=task,
            from_fixture=True,
        )

    def _save(self, path: Path, response: ProviderResponse, *, digest: str) -> None:
        """fixture を書く（既存は上書きしない）。"""
        if path.is_file():
            # 上書きを既定にすると、応答の変化が差分として現れないまま取り込まれる
            log.info("llm.fixture_kept", path=str(path), digest=digest)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "content": response.content,
                    "provider": response.provider,
                    "model": response.model,
                    "prompt_version": response.prompt_version,
                    "prompt_hash": response.prompt_hash,
                    "usage": {
                        "prompt": response.usage.prompt,
                        "completion": response.usage.completion,
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        log.info("llm.fixture_recorded", path=str(path), digest=digest)


def _pseudo_embedding(text: str, *, dim: int = 16) -> list[float]:
    """内容から決定的に作る疑似埋め込み（``replay`` 用）。

    意味的な性質（似た文が近い）は持たない。持たせようとすると「replay 用の小さなモデル」を
    作ることになり、それ自体がテスト対象になってしまう。
    """
    import hashlib  # noqa: PLC0415 — この関数だけで使う

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [digest[index % len(digest)] / 255.0 for index in range(dim)]


def resolve_provider(
    *,
    mode: LlmMode,
    live_factory: Any | None = None,
    root: Path | None = None,
) -> ReplayProvider:
    """モードからプロバイダを組み立てる。

    ``live_factory`` を遅延評価するのは、``replay`` のときに各社 SDK を import しないためである
    （SDK は任意依存であり、テスト環境に無くても ``replay`` は動く）。

    Args:
        mode: 実行モード。
        live_factory: 実プロバイダを作る呼び出し可能オブジェクト。
        root: fixture の根。

    Raises:
        LlmError: ``live`` が必要なのに factory が無い場合。
    """
    if mode is LlmMode.REPLAY:
        return ReplayProvider(mode=mode, root=root)
    if live_factory is None:
        raise LlmError(
            f"mode={mode.value} needs a live provider factory; replay is the only mode that "
            "runs without network access"
        )
    return ReplayProvider(mode=mode, live=live_factory(), root=root)
