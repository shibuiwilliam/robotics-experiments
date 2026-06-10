"""Tests for the B2-Live real-LLM translation baseline (IMPROVEMENT.md).

Two tracks (CLAUDE.md §9):
  * ``unit`` — offline logic (prompt, parsing, fallback, failure detection)
    with the SDK boundary monkeypatched. These run in ``make test-fast``.
  * ``api`` — one real, billed Claude call. Skipped unless ANTHROPIC_API_KEY
    is set; excluded from ``make test-fast`` / default CI.
"""

from __future__ import annotations

import asyncio
import os

import numpy as np
import pytest

from eval.baselines import BaselineB2Live, run_baseline_comparison
from sim.schema_gen.generator import SchemaTransform

SEED = 42


@pytest.fixture(autouse=True)
def _restore_event_loop() -> object:
    """Leave a usable default event loop after each test.

    These tests drive coroutines via ``asyncio.run()``, which closes the loop
    and resets the policy's current loop to ``None``. Other modules in the
    suite use the deprecated ``asyncio.get_event_loop()`` and would then fail
    on a polluted loop state, so we reinstall a fresh loop afterwards.
    """
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


def _state(scale: float = 1000.0) -> dict[str, object]:
    """A small synthetic source state (no MuJoCo needed for offline logic)."""
    return {
        "joint_positions": np.array([10.0, -20.0, 30.0, -40.0, 50.0, -60.0, 70.0]),
        "joint_velocities": np.zeros(7),
        "ee_position": np.array([0.1, 0.2, 0.3]),
        "ee_quaternion": np.array([1.0, 0.0, 0.0, 0.0]),
        "time": 0.0,
    }


def _patch_response(b2: BaselineB2Live, text: str) -> None:
    """Make ``_call_llm`` return a fixed (text, cost) without touching the network."""

    async def fake_call(prompt: str) -> tuple[str, float]:
        return text, 0.0

    b2._call_llm = fake_call  # type: ignore[method-assign]


@pytest.mark.unit
class TestB2LivePrompt:
    def test_prompt_states_divide_for_unit_scale(self) -> None:
        b2 = BaselineB2Live(SchemaTransform(unit_scale=1000.0, unit_name="mrad"))
        prompt = b2._build_prompt(_state())
        assert "dividing each value by 1000" in prompt
        assert "mrad" in prompt
        assert "JSON array of 7 numbers" in prompt
        # The joint values must be present for the model to convert.
        assert "10.000000" in prompt

    def test_prompt_identity_says_unchanged(self) -> None:
        b2 = BaselineB2Live(SchemaTransform())  # unit_scale == 1.0
        prompt = b2._build_prompt(_state(scale=1.0))
        assert "unchanged" in prompt.lower()
        assert "dividing" not in prompt


@pytest.mark.unit
class TestB2LiveParsing:
    def test_parse_clean_array(self) -> None:
        arr, mode = BaselineB2Live._parse_response("[1.0, 2.0, 3.0]", 3)
        assert mode is None
        assert arr is not None and np.allclose(arr, [1.0, 2.0, 3.0])

    def test_parse_markdown_fenced(self) -> None:
        arr, mode = BaselineB2Live._parse_response("```json\n[1, 2, 3]\n```", 3)
        assert mode is None
        assert arr is not None and np.allclose(arr, [1, 2, 3])

    def test_parse_with_surrounding_prose(self) -> None:
        arr, mode = BaselineB2Live._parse_response("Here you go: [4, 5, 6] done.", 3)
        assert mode is None
        assert arr is not None and np.allclose(arr, [4, 5, 6])

    def test_parse_wrong_count_fails(self) -> None:
        arr, mode = BaselineB2Live._parse_response("[1, 2]", 3)
        assert arr is None and mode == "parse_failure"

    def test_parse_garbage_fails(self) -> None:
        arr, mode = BaselineB2Live._parse_response("I cannot do that.", 3)
        assert arr is None and mode == "parse_failure"

    def test_parse_non_finite_fails(self) -> None:
        arr, mode = BaselineB2Live._parse_response("[1, 2, 1e999]", 3)
        assert arr is None and mode == "parse_failure"


@pytest.mark.unit
class TestB2LiveTranslate:
    def test_translate_happy_path(self) -> None:
        t = SchemaTransform(unit_scale=1000.0, unit_name="mrad")
        b2 = BaselineB2Live(t)
        state = _state()
        # Correct conversion: divide by 1000.
        expected = (np.asarray(state["joint_positions"]) / 1000.0).tolist()
        _patch_response(b2, str(expected))

        result = asyncio.run(b2.translate(state))
        pred = np.asarray(result["joint_positions"])
        assert np.allclose(pred, expected)
        assert b2.parse_failures == 0
        assert b2.api_calls == 1
        assert b2.records[0].failure_mode is None
        # Non-measured fields carried through unchanged (not inverted).
        assert np.allclose(result["ee_position"], state["ee_position"])

    def test_translate_fallback_on_garbage(self) -> None:
        t = SchemaTransform(unit_scale=1000.0, unit_name="mrad")
        b2 = BaselineB2Live(t)
        state = _state()
        _patch_response(b2, "Sorry, I can't.")

        result = asyncio.run(b2.translate(state))
        pred = np.asarray(result["joint_positions"])
        # Fallback returns the raw, untranslated source joints.
        assert np.allclose(pred, state["joint_positions"])
        assert b2.parse_failures == 1
        assert b2.records[0].parsed is False
        assert b2.records[0].failure_mode == "parse_failure"

    def test_translate_detects_unit_confusion(self) -> None:
        t = SchemaTransform(unit_scale=1000.0, unit_name="mrad")
        b2 = BaselineB2Live(t)
        state = _state()
        # Model forgot to divide — returns the raw input values.
        _patch_response(b2, str(np.asarray(state["joint_positions"]).tolist()))

        asyncio.run(b2.translate(state))
        assert b2.records[0].parsed is True
        assert b2.records[0].failure_mode == "unit_confusion"

    def test_metrics_surface(self) -> None:
        b2 = BaselineB2Live(SchemaTransform(unit_scale=1000.0))
        _patch_response(b2, "[0.01, -0.02, 0.03, -0.04, 0.05, -0.06, 0.07]")
        asyncio.run(b2.translate(_state()))
        assert b2.mean_latency_ms >= 0.0
        assert len(b2.latencies) == 1
        assert b2.total_cost == 0.0


@pytest.mark.unit
class TestRunBaselineComparisonGuard:
    def test_requires_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        state = _state()
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            run_baseline_comparison(
                state,
                SchemaTransform(unit_scale=1000.0),
                np.random.default_rng(SEED),
                include_live_llm=True,
            )

    def test_default_off_unaffected(self) -> None:
        """Default path must not contain a B2-Live entry (zero regression)."""
        from sim.wrapper import MuJoCoSim

        sim = MuJoCoSim(seed=SEED)
        sim.step(200)
        state = {
            "joint_positions": sim.get_joint_positions(),
            "joint_velocities": sim.get_joint_velocities(),
            "ee_position": sim.get_ee_pose()[0],
            "ee_quaternion": sim.get_ee_pose()[1],
            "time": sim.time,
        }
        results = run_baseline_comparison(
            state, SchemaTransform(unit_scale=1000.0), np.random.default_rng(SEED)
        )
        assert "B2-Live" not in results
        assert {"B0", "B1", "B2", "PSL"} <= set(results)


@pytest.mark.api
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
class TestB2LiveRealAPI:
    def test_single_real_translation(self) -> None:
        from sim.wrapper import MuJoCoSim

        sim = MuJoCoSim(seed=SEED)
        sim.step(200)
        state = {
            "joint_positions": sim.get_joint_positions(),
            "joint_velocities": sim.get_joint_velocities(),
            "ee_position": sim.get_ee_pose()[0],
            "ee_quaternion": sim.get_ee_pose()[1],
            "time": sim.time,
        }
        t = SchemaTransform(unit_scale=1000.0, unit_name="mrad")
        hetero = {**state, "joint_positions": np.asarray(state["joint_positions"]) * 1000.0}

        b2 = BaselineB2Live(t)
        result = asyncio.run(b2.translate(hetero))
        pred = np.asarray(result["joint_positions"])

        assert pred.shape == np.asarray(state["joint_positions"]).shape
        assert np.all(np.isfinite(pred))
        assert b2.api_calls == 1
        assert b2.records[0].latency_ms > 0.0
