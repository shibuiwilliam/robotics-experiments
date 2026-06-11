"""IMPROVEMENT P10/M16 — real LLM calls are recorded to llm_calls.jsonl."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mws.core.llm_log import LLMCallRecorder


def test_recorder_is_lazy_no_file_without_records(tmp_path: Path) -> None:
    """Mock runs make no real calls → no llm_calls.jsonl appears."""
    recorder = LLMCallRecorder(tmp_path / "llm_calls.jsonl")
    assert not recorder.path.exists()
    assert recorder.count == 0


def test_recorder_appends_parseable_jsonl(tmp_path: Path) -> None:
    recorder = LLMCallRecorder(tmp_path / "llm_calls.jsonl")
    for i in range(2):
        recorder.record(
            agent="ops_agent",
            model="gemini-3.5-flash",
            purpose=f"step:s{i}",
            prompt="Execute step ...",
            response=f"Did step {i}.",
            input_tokens=100 + i,
            output_tokens=20,
            tokens_measured=True,
            latency_ms=1234.5,
        )
    lines = recorder.path.read_text().splitlines()
    assert len(lines) == 2
    entry = json.loads(lines[1])
    assert entry["purpose"] == "step:s1"
    assert entry["response"] == "Did step 1."
    assert entry["input_tokens"] == 101
    assert entry["tokens_measured"] is True
    assert entry["prompt_chars"] == len("Execute step ...")
    assert recorder.count == 2


class _FakeRunner:
    """Stands in for google.adk.runners.InMemoryRunner — no network."""

    def __init__(self, *, agent, app_name) -> None:
        pass

    def run_debug(self, message: str, quiet: bool = True):
        async def _coro():
            usage = SimpleNamespace(prompt_token_count=42, candidates_token_count=7)
            part = SimpleNamespace(text="fake agent answer")
            content = SimpleNamespace(parts=[part])
            return [SimpleNamespace(content=content, usage_metadata=usage)]

        return _coro()


def test_adk_agent_records_real_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every _run_agent invocation appends exactly one record with the
    extracted response text and measured tokens (no cloud — fake runner)."""
    import google.adk.runners as adk_runners

    from mws.agents.live import ADKOpsAgent

    monkeypatch.setattr(adk_runners, "InMemoryRunner", _FakeRunner)

    recorder = LLMCallRecorder(tmp_path / "llm_calls.jsonl")
    agent = ADKOpsAgent(api_key="test-key-not-real", call_recorder=recorder)
    out = agent.execute_step("confirm_leak", {"retrieval_results": []})

    assert out["result"] == "fake agent answer"
    assert recorder.count == 1
    entry = json.loads(recorder.path.read_text())
    assert entry["purpose"] == "step:confirm_leak"
    assert entry["response"] == "fake agent answer"
    assert entry["input_tokens"] == 42
    assert entry["output_tokens"] == 7
    assert entry["tokens_measured"] is True
    assert entry["latency_ms"] >= 0
