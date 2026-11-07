"""Anthropic の live プロバイダ（HTTP 直、M8-10、ADR-0022）。

ネットワークは使わない（``httpx.MockTransport``）。重点:

1. **tool use で構造化出力を強制する** — スキーマが ``input_schema``、``tool_choice`` で固定。
2. **再試行はこのクラスの中だけ・上限つき** — 429/5xx は再試行、4xx（429 以外）は即失敗。
3. **鍵はヘッダにだけ載る**（不変条件 12） — ログ・例外・``repr`` に出ない。
4. **黙って別経路に落ちない** — 鍵が無ければ失敗。fixture が無くても API を叩かない。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import SecretStr

from sbp.core.config import LlmMode, Settings
from sbp.core.errors import FixtureMissingError, LlmError, StructuredOutputError
from sbp.llm import LlmTask, Message, Role, build_provider
from sbp.llm.anthropic import AnthropicProvider

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

KEY = "sk-ant-" + "test-SECRET-1234567890"  # 実在しないダミー（走査器に拾われないよう分けて書く）
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}
MESSAGES = [
    Message(role=Role.SYSTEM, content="あなたはオントロジー設計の助手である"),
    Message(role=Role.USER, content="signal を読んで概念を提案せよ"),
]


def _ok(name: str = "VibrationExcursion") -> dict[str, Any]:
    return {
        "model": "claude-test",
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "name": "emit_structured_output", "input": {"name": name}}
        ],
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


class _Recorder:
    """リクエストを覚え、用意した応答を順に返す MockTransport。"""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _provider(
    recorder: _Recorder, *, sleeps: list[float] | None = None, **overrides: Any
) -> AnthropicProvider:
    sleeper: Callable[[float], None] = (sleeps if sleeps is not None else []).append
    return AnthropicProvider(
        api_key=SecretStr(KEY),
        model="claude-test",
        client=recorder.client(),
        sleep=sleeper,
        **overrides,
    )


def _call(provider: AnthropicProvider) -> Any:
    return provider.complete_structured(
        MESSAGES, schema=SCHEMA, task=LlmTask.CONCEPT_PROPOSAL, prompt_version="1.0.0"
    )


class TestStructuredOutput:
    def test_the_tool_input_is_the_structured_output(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_ok()))
        response = _call(_provider(recorder))
        assert response.content == {"name": "VibrationExcursion"}
        assert response.provider == "anthropic"
        assert response.model == "claude-test"
        assert response.usage.prompt == 120
        assert response.usage.completion == 30
        assert response.provenance()["provider"] == "anthropic"

    def test_the_request_forces_one_tool_with_the_schema(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_ok()))
        _call(_provider(recorder))
        body = json.loads(recorder.requests[0].content)
        assert body["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}
        assert body["tools"][0]["input_schema"] == SCHEMA
        assert body["model"] == "claude-test"
        assert body["system"] == "あなたはオントロジー設計の助手である"
        assert body["messages"] == [{"role": "user", "content": "signal を読んで概念を提案せよ"}]

    def test_the_prompt_hash_is_the_hash_of_the_system_prompt(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_ok()))
        response = _call(_provider(recorder))
        expected = hashlib.sha256(MESSAGES[0].content.encode()).hexdigest()[:16]
        assert response.prompt_hash == expected

    def test_a_response_without_the_tool_call_is_a_structured_output_error(self) -> None:
        text_only = {"content": [{"type": "text", "text": "提案します"}], "stop_reason": "end_turn"}
        recorder = _Recorder(httpx.Response(200, json=text_only))
        with pytest.raises(StructuredOutputError):
            _call(_provider(recorder))


class TestRetry:
    def test_a_rate_limit_is_retried_with_backoff_then_succeeds(self) -> None:
        recorder = _Recorder(
            httpx.Response(429, json={"error": {"message": "slow down"}}),
            httpx.Response(529, json={"error": {"message": "overloaded"}}),
            httpx.Response(200, json=_ok()),
        )
        sleeps: list[float] = []
        response = _call(_provider(recorder, sleeps=sleeps, backoff_s=1.0))
        assert response.content["name"] == "VibrationExcursion"
        assert len(recorder.requests) == 3
        assert sleeps == [1.0, 2.0], "指数バックオフ"

    def test_retry_after_is_honoured_but_capped(self) -> None:
        recorder = _Recorder(
            httpx.Response(429, headers={"retry-after": "7"}, json={}),
            httpx.Response(429, headers={"retry-after": "9999"}, json={}),
            httpx.Response(200, json=_ok()),
        )
        sleeps: list[float] = []
        _call(_provider(recorder, sleeps=sleeps))
        assert sleeps == [7.0, 30.0]

    def test_a_transport_error_is_retried(self) -> None:
        recorder = _Recorder(httpx.ConnectError("boom"), httpx.Response(200, json=_ok()))
        assert _call(_provider(recorder)).content["name"]
        assert len(recorder.requests) == 2

    def test_the_retries_are_bounded_and_the_failure_is_loud(self) -> None:
        recorder = _Recorder(*[httpx.Response(503, json={}) for _ in range(10)])
        with pytest.raises(LlmError, match="failed after 3 attempts"):
            _call(_provider(recorder, max_retries=2))
        assert len(recorder.requests) == 3

    def test_a_client_error_is_not_retried(self) -> None:
        recorder = _Recorder(
            httpx.Response(400, json={"error": {"message": "schema is invalid"}}),
            httpx.Response(200, json=_ok()),
        )
        with pytest.raises(LlmError, match="HTTP 400 schema is invalid"):
            _call(_provider(recorder))
        assert len(recorder.requests) == 1, "入力が悪いので再試行しない"


class TestSecrets:
    def test_the_key_goes_in_the_header_only(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_ok()))
        _call(_provider(recorder))
        request = recorder.requests[0]
        assert request.headers["x-api-key"] == KEY
        assert request.headers["anthropic-version"]
        assert KEY not in request.content.decode()
        assert KEY not in str(request.url)

    def test_the_key_never_appears_in_errors_or_repr(self) -> None:
        recorder = _Recorder(httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
        provider = _provider(recorder)
        with pytest.raises(LlmError) as raised:
            _call(provider)
        assert KEY not in str(raised.value)
        assert KEY not in repr(provider)
        assert KEY not in repr(provider.__dict__.get("_api_key"))

    def test_the_key_is_not_logged(self, capsys: pytest.CaptureFixture[str]) -> None:
        recorder = _Recorder(httpx.Response(503, json={}), httpx.Response(200, json=_ok()))
        _call(_provider(recorder))
        captured = capsys.readouterr()
        assert KEY not in captured.out + captured.err

    def test_an_empty_key_or_model_is_refused(self) -> None:
        recorder = _Recorder()
        with pytest.raises(LlmError, match="KEY"):
            AnthropicProvider(api_key=SecretStr(""), model="m", client=recorder.client())
        with pytest.raises(LlmError, match="MODEL"):
            AnthropicProvider(api_key=SecretStr(KEY), model="", client=recorder.client())


class TestEmbeddings:
    def test_embeddings_are_unsupported_and_say_so(self) -> None:
        provider = _provider(_Recorder())
        with pytest.raises(LlmError, match="no embeddings API"):
            provider.embed(["x"], task=LlmTask.CONCEPT_PROPOSAL)


class TestBuildProvider:
    def _settings(self, **overrides: Any) -> Settings:
        base: dict[str, Any] = {"llm_mode": LlmMode.LIVE, "llm_default": "anthropic"}
        return Settings(_env_file=None, **(base | overrides))  # type: ignore[call-arg]

    def test_replay_needs_no_key_and_never_touches_the_network(self, tmp_path: Path) -> None:
        recorder = _Recorder()
        provider = build_provider(
            Settings(_env_file=None, llm_mode=LlmMode.REPLAY),  # type: ignore[call-arg]
            root=tmp_path,
            client=recorder.client(),
        )
        with pytest.raises(FixtureMissingError):
            provider.complete_structured(
                MESSAGES, schema=SCHEMA, task=LlmTask.CONCEPT_PROPOSAL, prompt_version="1.0.0"
            )
        assert recorder.requests == [], "fixture が無くても黙って live に落ちない"

    def test_live_without_a_key_fails(self) -> None:
        with pytest.raises(LlmError, match="ANTHROPIC_API_KEY"):
            build_provider(self._settings(anthropic_model="claude-test"))

    def test_live_without_a_model_fails(self) -> None:
        with pytest.raises(LlmError, match="MODEL"):
            build_provider(self._settings(anthropic_api_key=SecretStr(KEY)))

    def test_live_with_another_default_provider_fails(self) -> None:
        with pytest.raises(LlmError, match="only anthropic"):
            build_provider(
                self._settings(
                    llm_default="openai",
                    anthropic_api_key=SecretStr(KEY),
                    anthropic_model="claude-test",
                )
            )

    def test_live_calls_the_api_and_does_not_record(self, tmp_path: Path) -> None:
        recorder = _Recorder(httpx.Response(200, json=_ok()))
        provider = build_provider(
            self._settings(anthropic_api_key=SecretStr(KEY), anthropic_model="claude-test"),
            root=tmp_path,
            client=recorder.client(),
        )
        response = provider.complete_structured(
            MESSAGES, schema=SCHEMA, task=LlmTask.CONCEPT_PROPOSAL, prompt_version="1.0.0"
        )
        assert response.content["name"] == "VibrationExcursion"
        assert list(tmp_path.rglob("*.json")) == [], "live は fixture を書かない"

    def test_record_saves_a_fixture_without_the_key_and_then_replays_it(
        self, tmp_path: Path
    ) -> None:
        recorder = _Recorder(httpx.Response(200, json=_ok()))
        provider = build_provider(
            self._settings(
                llm_mode=LlmMode.RECORD,
                anthropic_api_key=SecretStr(KEY),
                anthropic_model="claude-test",
            ),
            root=tmp_path,
            client=recorder.client(),
        )
        kwargs: dict[str, Any] = {
            "schema": SCHEMA,
            "task": LlmTask.CONCEPT_PROPOSAL,
            "prompt_version": "1.0.0",
        }
        provider.complete_structured(MESSAGES, **kwargs)
        saved = list(tmp_path.rglob("*.json"))
        assert len(saved) == 1
        assert KEY not in saved[0].read_text(encoding="utf-8")
        again = provider.complete_structured(MESSAGES, **kwargs)
        assert again.from_fixture is True
        assert len(recorder.requests) == 1, "2 回目は fixture を再生し、API を叩かない"
