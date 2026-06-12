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


# --- REPLAY: deterministic replay of recorded responses ---


def _write_records(path: Path, records: list[dict]) -> Path:
    import json as _json

    path.write_text("\n".join(_json.dumps(r) for r in records) + "\n")
    return path


def test_replay_source_matches_in_order(tmp_path: Path) -> None:
    from mws.core.llm_log import LLMReplaySource

    path = _write_records(
        tmp_path / "llm_calls.jsonl",
        [
            {"agent": "ops_agent", "purpose": "plan", "response": '["a"]'},
            {"agent": "ops_agent", "purpose": "step:a", "response": "did a"},
        ],
    )
    replay = LLMReplaySource(path)
    assert replay.remaining == 2
    assert replay.next(agent="ops_agent", purpose="plan")["response"] == '["a"]'
    assert replay.next(agent="ops_agent", purpose="step:a")["response"] == "did a"
    assert replay.remaining == 0


def test_replay_source_mismatch_and_exhaustion_raise(tmp_path: Path) -> None:
    from mws.core.llm_log import (
        LLMReplaySource,
        ReplayExhaustedError,
        ReplayMismatchError,
    )

    path = _write_records(
        tmp_path / "llm_calls.jsonl",
        [{"agent": "ops_agent", "purpose": "plan", "response": "x"}],
    )
    replay = LLMReplaySource(path)
    with pytest.raises(ReplayMismatchError, match="no recorded call matches"):
        replay.next(agent="ops_agent", purpose="step:wrong")
    replay2 = LLMReplaySource(path)
    replay2.next(agent="ops_agent", purpose="plan")
    with pytest.raises(ReplayExhaustedError, match="exhausted"):
        replay2.next(agent="ops_agent", purpose="plan")


def test_record_then_replay_reproduces_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: record a run with the fake runner, then replay the file —
    identical parsed outputs, zero runner invocations, no re-recording."""
    import google.adk.runners as adk_runners

    from mws.agents.live import ADKOpsAgent
    from mws.core.llm_log import LLMReplaySource

    monkeypatch.setattr(adk_runners, "InMemoryRunner", _FakeRunner)
    recorder = LLMCallRecorder(tmp_path / "llm_calls.jsonl")
    original = ADKOpsAgent(api_key="test-key-not-real", call_recorder=recorder)
    out1 = original.execute_step("confirm_leak", {"retrieval_results": []})
    assert recorder.count == 1

    class _Boom:
        def __init__(self, **kwargs) -> None:
            raise AssertionError("replay must not construct a runner (no cloud)")

    monkeypatch.setattr(adk_runners, "InMemoryRunner", _Boom)
    recorder2 = LLMCallRecorder(tmp_path / "should_not_exist.jsonl")
    replayed = ADKOpsAgent(
        api_key="test-key-not-real",
        call_recorder=recorder2,
        replay_source=LLMReplaySource(recorder.path),
    )
    assert replayed.is_replay is True
    out2 = replayed.execute_step("confirm_leak", {"retrieval_results": []})

    assert out2["result"] == out1["result"] == "fake agent answer"
    assert replayed.last_usage == {"input_tokens": 42, "output_tokens": 7}
    assert not recorder2.path.exists(), "replay must not re-record"


def test_factory_disables_recorder_under_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_step_agent with MWS_LLM_REPLAY set wires the replay source and
    drops the recorder (a replayed run is a reproduction, not a measurement)."""
    from mws.agents.scenario_agent import create_step_agent
    from mws.core.config import MWSSettings
    from mws.core.types import CloudMode

    path = _write_records(
        tmp_path / "rec.jsonl",
        [{"agent": "incident_agent", "purpose": "step:confirm_leak", "response": "ok"}],
    )
    settings = MWSSettings(
        cloud_mode=CloudMode.LIVE,
        google_api_key="test-key-not-real",
        llm_replay=str(path),
    )
    recorder = LLMCallRecorder(tmp_path / "llm_calls.jsonl")
    agent = create_step_agent(
        settings,
        name="incident_agent",
        instruction="x",
        mock_step_fn=lambda s, r: {"status": "complete", "result": "mock"},
        call_recorder=recorder,
    )
    out = agent.execute_step("confirm_leak", {"retrieval_results": []})
    assert out["result"] == "ok"
    assert recorder.count == 0 and not recorder.path.exists()


@pytest.mark.live
def test_live_record_then_replay_identical(tmp_path: Path) -> None:
    """Live: one real ADK call is recorded, then replayed byte-identically
    with zero cloud calls (REPLAY acceptance)."""
    import os

    from mws.agents.live import ADKOpsAgent
    from mws.core.llm_log import LLMReplaySource

    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        pytest.skip("GOOGLE_API_KEY not set")

    recorder = LLMCallRecorder(tmp_path / "llm_calls.jsonl")
    real = ADKOpsAgent(
        api_key=key,
        name="replay_probe",
        instruction="Answer in one short sentence.",
        call_recorder=recorder,
    )
    out_real = real.execute_step("probe", {"retrieval_results": [{"text": "say hello"}]})
    assert recorder.count == 1

    replayed = ADKOpsAgent(
        api_key="unused-key-no-network",
        name="replay_probe",
        instruction="Answer in one short sentence.",
        replay_source=LLMReplaySource(recorder.path),
    )
    out_replay = replayed.execute_step("probe", {"retrieval_results": [{"text": "say hello"}]})
    assert out_replay["result"] == out_real["result"]


def test_replay_is_order_tolerant(tmp_path: Path) -> None:
    """Parallel runs record in completion order — replay matches by
    (agent, purpose) regardless of file position (Backlog B compatibility)."""
    from mws.core.llm_log import LLMReplaySource

    path = _write_records(
        tmp_path / "llm_calls.jsonl",
        [
            {"agent": "a", "purpose": "step:2", "response": "two"},
            {"agent": "a", "purpose": "step:1", "response": "one"},
        ],
    )
    replay = LLMReplaySource(path)
    assert replay.next(agent="a", purpose="step:1")["response"] == "one"
    assert replay.next(agent="a", purpose="step:2")["response"] == "two"


def test_parallel_steps_attribute_usage_and_record_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backlog B: concurrent execute_step calls return per-call usage in the
    result (no shared-state races) and the thread-safe recorder captures
    every call exactly once."""
    from concurrent.futures import ThreadPoolExecutor

    import google.adk.runners as adk_runners

    from mws.agents.live import ADKOpsAgent

    class _SlowFakeRunner(_FakeRunner):
        def run_debug(self, message: str, quiet: bool = True):
            import time as _t

            async def _coro():
                _t.sleep(0.01)  # encourage interleaving
                usage = SimpleNamespace(prompt_token_count=len(message), candidates_token_count=7)
                part = SimpleNamespace(text=f"answer:{message[-8:]}")
                return [
                    SimpleNamespace(content=SimpleNamespace(parts=[part]), usage_metadata=usage)
                ]

            return _coro()

    monkeypatch.setattr(adk_runners, "InMemoryRunner", _SlowFakeRunner)
    recorder = LLMCallRecorder(tmp_path / "llm_calls.jsonl")
    agent = ADKOpsAgent(api_key="test-key-not-real", call_recorder=recorder)

    steps = [f"step_{i}" for i in range(6)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda s: agent.execute_step(s, {"retrieval_results": []}), steps))

    assert [r["action"] for r in results] == steps  # original order preserved
    assert recorder.count == 6
    assert len(recorder.path.read_text().splitlines()) == 6
    for r in results:
        # usage attributed per call: input tokens = len of that step's prompt
        assert r["usage"]["output_tokens"] == 7
        assert r["usage"]["input_tokens"] > 0
