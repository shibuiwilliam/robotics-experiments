"""LLMClient のユニットテスト（llm.md「テストは常に mock」。実ネットワーク呼出をしない）。"""

from __future__ import annotations

from pathlib import Path

import pydantic
import pytest

from gtwm.llm.client import (
    LLMBudgetExceededError,
    LLMCache,
    LLMClient,
    LLMNotConfiguredError,
)
from gtwm.llm.prompt_loader import load_prompt

pytestmark = pytest.mark.unit


class _Output(pydantic.BaseModel):
    value: str


def _client(tmp_path: Path) -> LLMClient:
    return LLMClient(
        config_path="configs/llm_mock.yaml",
        cache=LLMCache(tmp_path / "cache.sqlite"),
        usage_log_path=tmp_path / "usage.jsonl",
    )


def test_ping_mock_ok(tmp_path: Path) -> None:
    client = _client(tmp_path)
    result = client.ping("mock")
    assert result.ok is True
    client.close()


def test_ping_unconfigured_provider_reports_ng_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = _client(tmp_path)
    result = client.ping("anthropic")
    assert result.ok is False
    assert "未設定" in result.detail
    client.close()


def test_call_mock_returns_valid_schema_json_and_logs_usage(tmp_path: Path) -> None:
    client = _client(tmp_path)
    prompt = load_prompt("concept_naming", cluster_id="c0", n_members=1, feature_summary="x")
    resp = client.call("concept_naming", prompt, schema=None, max_tokens=100)
    assert resp.provider == "mock"
    assert resp.cached is False
    usage_lines = (tmp_path / "usage.jsonl").read_text().strip().splitlines()
    assert len(usage_lines) == 1
    client.close()


def test_call_is_cached_on_second_identical_call(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp1 = client.call("concept_naming", "same prompt", max_tokens=50)
    resp2 = client.call("concept_naming", "same prompt", max_tokens=50)
    assert resp1.cached is False
    assert resp2.cached is True
    assert resp1.text == resp2.text
    client.close()


def test_unknown_task_raises_not_configured(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with pytest.raises(LLMNotConfiguredError):
        client.call("no_such_task", "prompt")
    client.close()


def test_budget_exceeded_rejects_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0.0000001")
    usage_path = tmp_path / "usage.jsonl"
    usage_path.write_text(
        '{"timestamp": "2026-01-01T00:00:00", "cost_usd": 1.0}\n'.replace("2026-01", "2026-01")
    )
    # 当月と一致させるため、実行時の年月で1行書き込む。
    import time

    year_month = time.strftime("%Y-%m")
    usage_path.write_text(f'{{"timestamp": "{year_month}-01T00:00:00", "cost_usd": 1.0}}\n')
    client = LLMClient(
        config_path="configs/llm_mock.yaml",
        cache=LLMCache(tmp_path / "cache.sqlite"),
        usage_log_path=usage_path,
    )
    with pytest.raises(LLMBudgetExceededError):
        client.call("concept_naming", "prompt")
    client.close()


def test_openai_dispatch_retries_without_temperature_on_unsupported_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一部の実プロバイダのモデル（例：2026-09-13 に確認した gpt-5.6-luna）は
    temperature の変更を受け付けず、`Unsupported value: 'temperature' ...` で
    BadRequestError を返す。`_dispatch` はこの特定のエラーだけを検知して
    temperature 無指定（モデル既定値）で1回だけ再試行しなければならない。"""
    import httpx
    import openai

    config_path = tmp_path / "llm_openai.yaml"
    config_path.write_text(
        "tasks:\n"
        "  concept_naming:\n"
        "    provider: openai\n"
        "    model: fake-temperature-locked-model\n"
        "pricing: {}\n"
        "retry:\n"
        "  max_retries: 0\n"
        "  temperature_for_json_tasks: 0.0\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    call_count = {"n": 0}

    class _FakeMessage:
        content = '{"value": "ok"}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        prompt_tokens = 3
        completion_tokens = 1

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    def _fake_create(**kwargs: object) -> object:
        call_count["n"] += 1
        if call_count["n"] == 1:
            assert kwargs.get("temperature") == 0.0
            req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            resp = httpx.Response(400, request=req, json={"error": {"message": "x"}})
            raise openai.BadRequestError(
                "Unsupported value: 'temperature' does not support 0.0 with this model.",
                response=resp,
                body=None,
            )
        assert "temperature" not in kwargs
        return _FakeResponse()

    class _FakeCompletions:
        create = staticmethod(_fake_create)

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            del api_key
            self.chat = _FakeChat()

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAIClient)

    client = LLMClient(
        config_path=str(config_path),
        cache=LLMCache(tmp_path / "cache.sqlite"),
        usage_log_path=tmp_path / "usage.jsonl",
    )
    resp = client.call("concept_naming", "prompt", schema=_Output, max_tokens=10)
    assert resp.text == '{"value": "ok"}'
    assert call_count["n"] == 2
    client.close()
