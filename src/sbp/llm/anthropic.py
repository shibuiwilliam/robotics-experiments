"""Anthropic の live プロバイダ（HTTP 直、M8-10、ADR-0022）。

SDK を使わず ``httpx`` で Messages API を呼ぶ。使う面は構造化出力 1 本で小さく、依存を増やさず
``httpx.MockTransport`` でネットワーク無しにテストできる。

**構造化出力は tool use で強制する。** 期待する JSON Schema を ``tools[].input_schema`` にし、
``tool_choice`` で 1 つのツールを指定する。応答の ``tool_use`` ブロックの ``input`` が構造化出力で、
自由文の JSON をパースするより崩れにくい。それでも崩れうるので、検証は呼び出し側の
``ProviderResponse.validated`` が行う（ここは「ツール呼び出しが無い」ときだけ失敗させる）。

**再試行はこのクラスの中だけ**（CLAUDE.md: リトライは eventlog の consumer 層と ``llm.Provider`` の
2 箇所）。429・5xx・接続エラー・タイムアウトを上限つきの指数バックオフで再試行する。
4xx（429 以外）は入力が悪いので即失敗する。上限に達したら握りつぶさず
:class:`~sbp.core.errors.LlmError` にする。

**鍵はヘッダにだけ載せる**（不変条件 12）。``SecretStr`` で受け、リクエストごとに取り出し、
クライアントの既定ヘッダには入れない（``repr`` や例外に出ない）。ログ・例外の文言にも鍵を入れない。
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any, Final

import httpx

from sbp.core.errors import LlmError, StructuredOutputError
from sbp.core.logging import get_logger
from sbp.llm.base import ProviderResponse, Role, TokenUsage

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import SecretStr

    from sbp.llm.base import LlmTask, Message

__all__ = ["DEFAULT_BASE_URL", "AnthropicProvider"]

log = get_logger(__name__)

DEFAULT_BASE_URL: Final = "https://api.anthropic.com"
"""API の根（秘密ではない）。テストと検証では差し替える。"""

_API_VERSION: Final = "2023-06-01"
_TOOL_NAME: Final = "emit_structured_output"
_RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504, 529})
_MAX_RETRY_AFTER_S: Final = 30.0
"""``Retry-After`` を尊重する上限。長すぎる待ちでワーカーのループを止めない。"""


class AnthropicProvider:
    """Messages API を呼ぶ ``Provider``。

    Attributes:
        model: モデル ID（``.env`` の ``ANTHROPIC_MODEL``。コードに書かない）。
    """

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        client: httpx.Client | None = None,
        base_url: str = DEFAULT_BASE_URL,
        max_tokens: int = 4096,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        backoff_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Args:
        api_key: API キー。``SecretStr`` のまま持つ。
        model: モデル ID。空は拒否する。
        client: HTTP クライアント（テストで ``MockTransport`` を渡す）。
        base_url: API の根。
        max_tokens: 応答の最大トークン。
        timeout_s: 1 回の呼び出しのタイムアウト。
        max_retries: 再試行の上限（初回を含まない）。
        backoff_s: バックオフの初期値（指数で伸ばす）。
        sleep: 待ちの関数（テストで差し替える）。

        Raises:
            LlmError: モデル ID が空、または鍵が空の場合。
        """
        if not model:
            raise LlmError("ANTHROPIC_MODEL is not set; the model id is never hard-coded")
        if not api_key.get_secret_value():
            raise LlmError("ANTHROPIC_API_KEY is empty")
        self._api_key = api_key
        self.model = model
        self._client = client or httpx.Client(timeout=timeout_s)
        self._url = f"{base_url.rstrip('/')}/v1/messages"
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    @property
    def name(self) -> str:
        """プロバイダ名。"""
        return "anthropic"

    def complete_structured(
        self,
        messages: Sequence[Message],
        *,
        schema: dict[str, Any],
        task: LlmTask,
        prompt_version: str,
    ) -> ProviderResponse:
        """構造化出力を得る（tool use で強制）。

        Args:
            messages: メッセージ列。``system`` は先頭にまとめて渡す。
            schema: 期待する JSON Schema。
            task: 用途。
            prompt_version: プロンプトの版。

        Raises:
            LlmError: 再試行しても失敗した、または 4xx の場合。
            StructuredOutputError: ツール呼び出しが応答に無い場合。
        """
        system = "\n\n".join(m.content for m in messages if m.role is Role.SYSTEM)
        conversation = [
            {"role": m.role.value, "content": m.content}
            for m in messages
            if m.role is not Role.SYSTEM
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "messages": conversation,
            "tools": [
                {
                    "name": _TOOL_NAME,
                    "description": "構造化された結果を返す",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": _TOOL_NAME},
        }
        if system:
            body["system"] = system

        payload = self._post(body, task=task)
        content = _tool_input(payload, task=task, model=self.model)
        usage = payload.get("usage") or {}
        return ProviderResponse(
            content=content,
            provider=self.name,
            model=str(payload.get("model") or self.model),
            prompt_version=prompt_version,
            prompt_hash=hashlib.sha256(system.encode("utf-8")).hexdigest()[:16],
            usage=TokenUsage(
                prompt=int(usage.get("input_tokens", 0)),
                completion=int(usage.get("output_tokens", 0)),
            ),
            task=task,
        )

    def embed(self, texts: Sequence[str], *, task: LlmTask) -> list[list[float]]:
        """未対応。Anthropic は埋め込み API を持たない（``SBP_EMBEDDINGS`` は別のプロバイダ）。

        Args:
            texts: 埋め込む文字列。
            task: 用途。

        Raises:
            LlmError: 常に。
        """
        del texts, task
        raise LlmError(
            "the anthropic provider has no embeddings API; set SBP_EMBEDDINGS to another provider"
        )

    def _post(self, body: dict[str, Any], *, task: LlmTask) -> dict[str, Any]:
        """1 回の呼び出し（再試行つき）。鍵はここでだけ取り出す。"""
        headers = {
            "x-api-key": self._api_key.get_secret_value(),
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        attempt = 0
        while True:
            reason: str
            retry_after: float | None = None
            try:
                response = self._client.post(self._url, headers=headers, json=body)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                reason = f"transport error: {type(exc).__name__}"
            else:
                if response.status_code < 400:
                    return _json_object(response)
                if response.status_code not in _RETRYABLE_STATUS:
                    raise LlmError(
                        f"anthropic rejected the request for task {task.value}: "
                        f"HTTP {response.status_code} {_error_message(response)}"
                    )
                reason = f"HTTP {response.status_code}"
                retry_after = _retry_after(response)
            if attempt >= self._max_retries:
                raise LlmError(
                    f"anthropic call for task {task.value} failed after {attempt + 1} attempts "
                    f"({reason})"
                )
            delay = retry_after if retry_after is not None else self._backoff_s * (2**attempt)
            attempt += 1
            log.warning(
                "llm.anthropic_retry",
                task=task.value,
                attempt=attempt,
                reason=reason,
                delay_s=delay,
            )
            self._sleep(min(delay, _MAX_RETRY_AFTER_S))


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """応答の本文を JSON のオブジェクトとして読む。"""
    try:
        payload = response.json()
    except ValueError as exc:
        raise LlmError("anthropic returned a body that is not JSON") from exc
    if not isinstance(payload, dict):
        raise LlmError("anthropic returned a JSON body that is not an object")
    return payload


def _tool_input(payload: dict[str, Any], *, task: LlmTask, model: str) -> dict[str, Any]:
    """応答の ``tool_use`` ブロックから構造化出力を取り出す。"""
    for block in payload.get("content") or ():
        if isinstance(block, dict) and block.get("type") == "tool_use":
            value = block.get("input")
            if isinstance(value, dict):
                return value
    raise StructuredOutputError(
        f"{model} returned no structured output for task {task.value} "
        f"(stop_reason={payload.get('stop_reason')})"
    )


def _error_message(response: httpx.Response) -> str:
    """API のエラー本文を短く取り出す（鍵は本文に含まれない）。"""
    try:
        error = response.json().get("error", {})
        return str(error.get("message", ""))[:300]
    except (ValueError, AttributeError):
        return ""


def _retry_after(response: httpx.Response) -> float | None:
    """``Retry-After``（秒）。読めなければ ``None``。"""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
