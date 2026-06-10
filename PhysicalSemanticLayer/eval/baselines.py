"""Baselines for comparison with PSL (PROJECT.md §9).

B0: No semantic layer, hand-written N×N adapters (combinatorial explosion).
B1: Raw data shared blackboard (no semantics).
B2: LLM ad-hoc translation — simulated with realistic error modes.
B2-Live: LLM ad-hoc translation — actual Claude API calls (IMPROVEMENT.md).

All baselines use the same shared infrastructure (sim, metrics) for fairness.
"""

from __future__ import annotations

import json
import os
import re
import time as _time
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from sim.schema_gen.generator import (
    SchemaTransform,
    apply_schema_transform,
    invert_schema_transform,
)


class BaselineB0:
    """B0: Hand-written N×N adapter (no semantic layer).

    For each pair of robots, a bespoke translation is written.
    This works for 2 robots but scales as O(N^2).

    For identical robots, this is trivially the identity.
    For heterogeneous robots, it must know the specific transform.
    """

    def __init__(self, transform: SchemaTransform) -> None:
        self._transform = transform

    def translate(self, source_state: dict[str, object]) -> dict[str, object]:
        """Direct A→B translation without any semantic layer."""
        return invert_schema_transform(source_state, self._transform)

    @property
    def n_adapters_needed(self) -> int:
        """For N robots, N×N adapters are needed (minus diagonal)."""
        return -1  # Placeholder — actual count depends on N


class BaselineB1:
    """B1: Raw shared blackboard (no semantics).

    Data is shared as-is on a common blackboard. No unit conversion,
    no frame transformation, no uncertainty propagation.
    The receiver must interpret raw values.
    """

    def __init__(self) -> None:
        self._blackboard: dict[str, dict[str, object]] = {}

    def write(self, entity_id: str, state: dict[str, object]) -> None:
        """Write raw state to the blackboard."""
        self._blackboard[entity_id] = dict(state)

    def read(self, entity_id: str) -> dict[str, object] | None:
        """Read raw state from the blackboard (no translation)."""
        return self._blackboard.get(entity_id)


class BaselineB2:
    """B2: LLM ad-hoc translation (simulated with realistic error modes).

    Simulates an LLM that mostly gets the schema transform right,
    but exhibits realistic failure modes:
    - Small numerical imprecision (rounding errors from text serialization)
    - Occasional unit confusion (1% chance of forgetting to convert)
    - Does NOT propagate covariance or provenance (structural information loss)
    """

    def __init__(self, transform: SchemaTransform, seed: int = 42) -> None:
        self._transform = transform
        self._call_count = 0
        self._rng = np.random.default_rng(seed)

    def translate(self, source_state: dict[str, object]) -> dict[str, object]:
        """Simulate LLM translation with realistic error modes."""
        self._call_count += 1
        result = invert_schema_transform(source_state, self._transform)

        jpos = np.asarray(result["joint_positions"], dtype=np.float64).copy()

        # Simulate LLM numerical imprecision (text round-trip)
        jpos += self._rng.normal(0, 1e-4, size=jpos.shape)

        # 1% chance: LLM forgets to convert units (returns raw input)
        if self._rng.random() < 0.01:
            jpos = np.asarray(source_state["joint_positions"], dtype=np.float64).copy()

        result["joint_positions"] = jpos
        return result

    @property
    def api_calls(self) -> int:
        """Number of simulated API calls."""
        return self._call_count


@dataclass
class B2LiveCallRecord:
    """One real B2-Live API call, retained for failure-mode analysis.

    Fields:
        raw_response: The raw text Claude returned (for post-hoc inspection).
        parsed: True if a valid joint array of the expected length was parsed.
        latency_ms: Wall-clock latency of the API round-trip, in milliseconds.
        cost_usd: Reported ``total_cost_usd`` for the call.
        failure_mode: ``None`` if clean, else one of ``"parse_failure"``,
            ``"unit_confusion"`` (returned the unconverted input).
    """

    raw_response: str
    parsed: bool
    latency_ms: float
    cost_usd: float
    failure_mode: str | None = None


class BaselineB2Live:
    """B2-Live: real Claude API calls for schema translation (IMPROVEMENT.md).

    Where :class:`BaselineB2` *simulates* an LLM with a statistical error model,
    this baseline calls an actual Claude model (Haiku by default, for cost
    efficiency) to perform the joint-position unit conversion. It measures the
    real LLM's translation accuracy, latency, cost, non-determinism, and
    failure modes for an honest PSL-vs-LLM comparison.

    Fairness (IMPROVEMENT.md §3a/§6): the prompt states the conversion method
    explicitly ("divide each value by 1000") — the most favorable condition for
    the LLM. Any residual PSL advantage (latency, determinism, covariance
    propagation) is then structural rather than a prompt artifact.

    Ground-truth isolation (CLAUDE.md §1-3): the model sees only the
    heterogeneous source joints plus the natural-language conversion spec. It
    never reads MuJoCo ground truth nor calls ``invert_schema_transform`` to
    compute the answer; ground truth is used only to *score* the output, outside
    this class.

    Determinism (CLAUDE.md §10): the model name is pinned. The installed SDK's
    ``ClaudeAgentOptions`` exposes no temperature field, so non-determinism is
    measured rather than suppressed (PROJECT.md §11).

    Note:
        Only ``joint_positions`` is translated (the metric target shared with
        B2-sim). Non-measured fields are carried through from the source
        unchanged — they are deliberately *not* inverted, to avoid any
        ground-truth-derived computation leaking into the baseline.
    """

    DEFAULT_MODEL = "claude-haiku-4-5"

    def __init__(
        self,
        transform: SchemaTransform,
        model: str = DEFAULT_MODEL,
        max_budget_usd: float = 0.05,
    ) -> None:
        self._transform = transform
        self._model = model
        self._max_budget_usd = max_budget_usd
        self._call_count = 0
        self._total_cost = 0.0
        self._latencies: list[float] = []
        self._parse_failures = 0
        self._records: list[B2LiveCallRecord] = []

    # ── Prompt construction ──────────────────────────────────────────────

    def _build_prompt(self, source_state: dict[str, object]) -> str:
        """Build a translation prompt: explicit conversion spec + joint data.

        Joint positions undergo only unit scaling in the schema transform, so
        the inverse is a single scalar division by ``unit_scale`` (identity ⇒
        return unchanged). The conversion method is stated explicitly — the
        most favorable condition for the LLM (IMPROVEMENT.md §3a).
        """
        jpos = np.asarray(source_state["joint_positions"], dtype=np.float64)
        n = int(jpos.size)
        scale = self._transform.unit_scale
        unit_name = self._transform.unit_name

        if abs(scale - 1.0) < 1e-12:
            conversion = "The values are already in radians. Return them unchanged."
        else:
            conversion = f"Convert by dividing each value by {scale:g}."

        values = ", ".join(f"{v:.6f}" for v in jpos.tolist())
        return (
            "You are a robot state translator.\n\n"
            f"Source robot: joint positions in {unit_name} ({n} joints).\n"
            f"Target robot: joint positions in radians (rad) ({n} joints).\n\n"
            f"{conversion}\n\n"
            f"Input joint_positions ({unit_name}):\n"
            f"[{values}]\n\n"
            f"Return ONLY a JSON array of {n} numbers in radians. "
            "No explanation, no markdown."
        )

    # ── SDK boundary (isolated for testability) ──────────────────────────

    async def _call_llm(self, prompt: str) -> tuple[str, float]:
        """Call Claude via the Agent SDK ``query()`` and return (text, cost_usd).

        This is the only method that touches the network. Unit tests monkeypatch
        it to exercise the parse/fallback pipeline offline.
        """
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        options = ClaudeAgentOptions(
            model=self._model,
            allowed_tools=[],
            max_turns=1,
            max_budget_usd=self._max_budget_usd,
        )
        text = ""
        cost = 0.0
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                text = message.result or ""
                cost = message.total_cost_usd or 0.0
                break
        return text, cost

    # ── Response parsing ─────────────────────────────────────────────────

    @staticmethod
    def _parse_response(text: str, n: int) -> tuple[NDArray[np.float64] | None, str | None]:
        """Parse a JSON array of ``n`` finite floats from the model's reply.

        Defensive against markdown fences and surrounding prose: locates the
        first ``[...]`` block and validates length and finiteness.

        Returns:
            ``(array, None)`` on success, else ``(None, "parse_failure")``.
        """
        candidate = text.strip()
        # Strip ```json ... ``` fences if present.
        if candidate.startswith("```"):
            candidate = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", candidate).strip()

        match = re.search(r"\[.*?\]", candidate, re.DOTALL)
        if match is None:
            return None, "parse_failure"
        try:
            parsed = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None, "parse_failure"
        if not isinstance(parsed, list) or len(parsed) != n:
            return None, "parse_failure"
        try:
            arr = np.asarray([float(x) for x in parsed], dtype=np.float64)
        except (TypeError, ValueError):
            return None, "parse_failure"
        if not np.all(np.isfinite(arr)):
            return None, "parse_failure"
        return arr, None

    def _detect_unit_confusion(
        self, result: NDArray[np.float64], source: NDArray[np.float64]
    ) -> str | None:
        """Flag the unit-confusion failure mode (returned the unconverted input).

        Only meaningful when a non-identity unit scale was requested. The model
        "confused units" if its output is closer to the raw source than to the
        correctly converted ``source / unit_scale``.
        """
        scale = self._transform.unit_scale
        if abs(scale - 1.0) < 1e-12:
            return None
        expected = source / scale
        err_correct = float(np.linalg.norm(result - expected))
        err_raw = float(np.linalg.norm(result - source))
        return "unit_confusion" if err_raw < err_correct else None

    # ── Public translation API ───────────────────────────────────────────

    async def translate(self, source_state: dict[str, object]) -> dict[str, object]:
        """Call Claude to translate ``joint_positions`` into the base schema.

        On parse failure, falls back to the raw (untranslated) source joints —
        representing a total translation failure — and counts it.

        Returns:
            A copy of ``source_state`` with ``joint_positions`` replaced by the
            model's (or fallback) values.
        """
        prompt = self._build_prompt(source_state)
        source_jpos = np.asarray(source_state["joint_positions"], dtype=np.float64)

        t0 = _time.perf_counter()
        text, cost = await self._call_llm(prompt)
        latency_ms = (_time.perf_counter() - t0) * 1000.0

        self._call_count += 1
        self._total_cost += cost
        self._latencies.append(latency_ms)

        parsed, failure_mode = self._parse_response(text, int(source_jpos.size))
        if parsed is None:
            self._parse_failures += 1
            result_jpos = source_jpos.copy()
            parsed_ok = False
        else:
            result_jpos = parsed
            parsed_ok = True
            failure_mode = self._detect_unit_confusion(result_jpos, source_jpos)

        self._records.append(
            B2LiveCallRecord(
                raw_response=text,
                parsed=parsed_ok,
                latency_ms=latency_ms,
                cost_usd=cost,
                failure_mode=failure_mode,
            )
        )

        result = dict(source_state)
        result["joint_positions"] = result_jpos
        return result

    # ── Metrics surface ──────────────────────────────────────────────────

    @property
    def api_calls(self) -> int:
        """Number of real API calls made."""
        return self._call_count

    @property
    def total_cost(self) -> float:
        """Cumulative reported cost in USD."""
        return self._total_cost

    @property
    def parse_failures(self) -> int:
        """Number of calls whose response could not be parsed."""
        return self._parse_failures

    @property
    def latencies(self) -> list[float]:
        """Per-call latencies in milliseconds."""
        return list(self._latencies)

    @property
    def mean_latency_ms(self) -> float:
        """Mean call latency in milliseconds (0.0 if no calls yet)."""
        return float(np.mean(self._latencies)) if self._latencies else 0.0

    @property
    def records(self) -> list[B2LiveCallRecord]:
        """All per-call records (for failure-mode analysis)."""
        return list(self._records)


def run_baseline_comparison(
    base_state: dict[str, object],
    transform: SchemaTransform,
    rng: np.random.Generator,
    seed: int = 42,
    include_live_llm: bool = False,
    live_model: str = BaselineB2Live.DEFAULT_MODEL,
) -> dict[str, dict[str, float]]:
    """Run all baselines on the same input and return metrics.

    Args:
        base_state: Canonical base state.
        transform: Schema transform applied to create the heterogeneous view.
        rng: Random generator for noise.
        seed: Seed for B2's stochastic error simulation.
        include_live_llm: If True, additionally call the real Claude API
            (B2-Live) on the same heterogeneous state and add a ``"B2-Live"``
            entry. Defaults to False so the offline suite is unaffected
            (IMPROVEMENT.md §6). Requires ``ANTHROPIC_API_KEY``.
        live_model: Claude model id for B2-Live (Haiku by default).

    Returns:
        Dict of baseline_name → metrics dict.

    Raises:
        RuntimeError: If ``include_live_llm`` is True but ``ANTHROPIC_API_KEY``
            is not set.
    """
    import asyncio

    from eval.metrics.contract import round_trip_information_loss
    from eval.metrics.se3 import joint_rmse

    hetero_state = apply_schema_transform(base_state, transform, rng=rng)
    original_jpos = np.asarray(base_state["joint_positions"])

    results: dict[str, dict[str, float]] = {}

    # B0: hand-written adapter (knows the transform)
    b0 = BaselineB0(transform)
    b0_result = b0.translate(hetero_state)
    b0_jpos = np.asarray(b0_result["joint_positions"])
    results["B0"] = {
        "joint_rmse": joint_rmse(b0_jpos, original_jpos),
        "info_loss": round_trip_information_loss(original_jpos, b0_jpos),
    }

    # B1: raw blackboard (no translation)
    b1 = BaselineB1()
    b1.write("source", hetero_state)
    b1_state = b1.read("source")
    assert b1_state is not None
    b1_jpos = np.asarray(b1_state["joint_positions"])
    results["B1"] = {
        "joint_rmse": joint_rmse(b1_jpos, original_jpos),
        "info_loss": round_trip_information_loss(original_jpos, b1_jpos),
    }

    # B2: simulated LLM translation (with realistic error modes)
    b2 = BaselineB2(transform, seed=seed)
    t0 = _time.perf_counter()
    b2_result = b2.translate(hetero_state)
    b2_latency = _time.perf_counter() - t0
    b2_jpos = np.asarray(b2_result["joint_positions"])
    results["B2"] = {
        "joint_rmse": joint_rmse(b2_jpos, original_jpos),
        "info_loss": round_trip_information_loss(original_jpos, b2_jpos),
        "api_calls": float(b2.api_calls),
        "latency_ms": b2_latency * 1000,
    }

    # PSL (via heterogeneous adapter)
    from psl.adapters.robots.panda.adapter import PandaAdapter
    from psl.adapters.robots.panda.heterogeneous import HeterogeneousPandaAdapter
    from psl.ir.translation import translate_r2r

    hetero_adapter = HeterogeneousPandaAdapter(entity_id="h", transform=transform)
    base_adapter = PandaAdapter(entity_id="b")
    t0 = _time.perf_counter()
    psl_result = translate_r2r(hetero_adapter, base_adapter, hetero_state)
    psl_latency = _time.perf_counter() - t0
    psl_jpos = np.asarray(psl_result["joint_positions"])
    results["PSL"] = {
        "joint_rmse": joint_rmse(psl_jpos, original_jpos),
        "info_loss": round_trip_information_loss(original_jpos, psl_jpos),
        "latency_ms": psl_latency * 1000,
    }

    # B2-Live: real Claude API translation (opt-in, billed). Default-off keeps
    # the offline suite byte-for-byte unchanged (IMPROVEMENT.md §6).
    if include_live_llm:
        if not os.environ.get("ANTHROPIC_API_KEY", ""):
            raise RuntimeError(
                "include_live_llm=True requires ANTHROPIC_API_KEY to be set "
                "(B2-Live makes real, billed Claude API calls)."
            )
        b2_live = BaselineB2Live(transform, model=live_model)
        t0 = _time.perf_counter()
        b2_live_result = asyncio.run(b2_live.translate(hetero_state))
        b2_live_latency = _time.perf_counter() - t0
        b2_live_jpos = np.asarray(b2_live_result["joint_positions"])
        results["B2-Live"] = {
            "joint_rmse": joint_rmse(b2_live_jpos, original_jpos),
            "info_loss": round_trip_information_loss(original_jpos, b2_live_jpos),
            "latency_ms": b2_live_latency * 1000,
            "api_calls": float(b2_live.api_calls),
            "cost_usd": b2_live.total_cost,
            "parse_failures": float(b2_live.parse_failures),
        }

    return results
