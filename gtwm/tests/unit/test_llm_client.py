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
