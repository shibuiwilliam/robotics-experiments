# B2-Live Implementation Plan — Real-LLM Schema-Translation Baseline

> **Status**: in progress · **Owner**: PSL-Bench · **Spec**: [`IMPROVEMENT.md`](../IMPROVEMENT.md) ·
> **Governing docs**: [`PROJECT.md`](../PROJECT.md) §9 (baselines), §8 (metrics), §11 (LLM non-determinism); [`CLAUDE.md`](../CLAUDE.md) §1/§5/§10/§11/§15.

## 1. Goal & Why

`eval/baselines.py::BaselineB2` *simulates* an LLM translator with a statistical error model
(1e-4 numerical noise + 1 % unit-confusion). It models the *structural* weaknesses of an LLM but
has never been checked against a real model. B2-Live replaces the simulation with **actual Claude
API calls** so the PSL-vs-LLM comparison (PROJECT.md §2.3, §9.1 — "why not just let the LLM do
it") rests on measured data, not an assumption.

We measure, per IMPROVEMENT.md §2: **RMSE** (accuracy), **non-determinism** (output spread at the
same input), **latency** (vs PSL's ~125 µs), **cost** (USD/translation), and **failure modes**
(parse failures, unit confusion, precision loss).

## 2. Fairness & invariants (non-negotiable)

- **Most-favorable prompt** (IMPROVEMENT.md §3a/§6): the conversion method is stated explicitly
  ("divide each value by 1000"). This forecloses the "your prompt was bad" rebuttal — any residual
  PSL advantage (latency, determinism, covariance propagation) is then structural.
- **Ground-truth isolation** (CLAUDE.md §1-3, §5): B2-Live lives in `eval/`. The LLM sees only the
  heterogeneous `source_state` joints + the natural-language conversion spec. It must **not** call
  `invert_schema_transform` to compute the answer — that is the model's job. Ground truth is used
  only to *score* the output, in `eval/`.
- **Determinism** (CLAUDE.md §10): the model name is pinned in config. The installed SDK
  (`claude-agent-sdk` 0.2.91) `ClaudeAgentOptions` exposes **no `temperature`** field (verified),
  matching the existing `agents/topology/supervisor.py` pattern which also drops it. Non-determinism
  is therefore itself a measurement target (PROJECT.md §11), not something we suppress.
- **Zero regression** (IMPROVEMENT.md §6/§7-5): `include_live_llm=False` is the default, so the
  existing offline suite is byte-for-byte unaffected. Every API-calling test carries
  `@pytest.mark.api` and is excluded from `make test-fast` / default CI.
- **Cost discipline** (CLAUDE.md §11): default model = Haiku (`claude-haiku-4-5`); log
  `total_cost_usd` + token `usage` per call; print a cost estimate before any sweep; hard
  `max_budget_usd` cap per call.

## 3. Design

### 3.1 `BaselineB2Live` (in `eval/baselines.py`)

| Member | Purpose |
|---|---|
| `__init__(transform, model="claude-haiku-4-5", max_budget_usd=0.05)` | Pin model + per-call budget cap. |
| `_build_prompt(source_state) -> str` | Natural-language conversion spec from `SchemaTransform` (unit_scale/unit_name) + joint vector. Joints undergo only unit scaling, so the inverse is "divide by `unit_scale`" (identity ⇒ "return unchanged"). |
| `async _call_llm(prompt) -> tuple[str, float]` | **Isolated** SDK boundary: `query()` with `ClaudeAgentOptions(model, allowed_tools=[], max_turns=1, max_budget_usd)`. Returns `(result_text, cost_usd)`. Monkeypatch target for unit tests. |
| `_parse_response(text, n) -> tuple[NDArray|None, str|None]` | Defensive: strip markdown fences, locate first JSON `[...]`, require exactly `n` finite floats. Returns `(array, None)` or `(None, "parse_failure")`. |
| `async translate(source_state) -> dict` | Orchestrate: build → call → parse → fallback. Records cost/latency/failure-mode. On parse failure, fall back to **raw untranslated** source joints (total-failure semantics) and increment `parse_failures`. |
| props: `api_calls`, `total_cost`, `parse_failures`, `latencies`, `mean_latency_ms`, `records` | Metrics surface for the driver/report. |

`B2LiveCallRecord` (dataclass): `raw_response`, `parsed: bool`, `latency_ms`, `cost_usd`,
`failure_mode: str|None` (`parse_failure` / `unit_confusion` / `None`). Enables Phase-4
failure-mode analysis. `unit_confusion` is detected post-parse: when `unit_scale != 1` and the
output is closer to the *unconverted* source than to `source/unit_scale`.

### 3.2 `run_baseline_comparison(..., include_live_llm=False)`

When `True`: run the existing offline baselines unchanged, then `asyncio.run(b2_live.translate(...))`
on the same `hetero_state`, returning
`results["B2-Live"] = {joint_rmse, info_loss, latency_ms, api_calls, cost_usd, parse_failures, model}`.
Raises a clear `RuntimeError` if `ANTHROPIC_API_KEY` is unset (the caller asked to pay).

### 3.3 Driver `eval/b2_live.py` (entry point for `make baseline-llm`)

`main(argv)` stages (each prints a pre-flight cost estimate):
1. **smoke** — 1 call on the S1 transform (`unit_scale=1000`); `--smoke` runs only this.
2. **dose-response** — one call per `HETEROGENEITY_DOSES` (6), B2-Live vs B2-sim vs PSL RMSE.
3. **non-determinism** — same input ×5; report per-element std + max spread.
4. **failure-mode** — aggregate `records` across all calls.
Saves `experiments/runs/b2_live_results.json` (fixed path, IMPROVEMENT.md §4-P3) **and** a
timestamped run dir with `manifest.json` (CLAUDE.md §10 reproducibility) via `eval/runner/manifest.py`.

### 3.4 Makefile

- `baseline-llm` — full run (`$$` billed).
- `baseline-llm-smoke` — single call (`$$`, ~$0.001).

## 4. Test strategy (CLAUDE.md §9 dual-track)

`tests/test_b2_live.py`:
- `@pytest.mark.unit` (runs in `test-fast`, **no network** — monkeypatch `_call_llm`):
  prompt content (divide-by-N, identity, joint values present); parse of clean / fenced /
  extra-text / wrong-count / garbage; `translate()` happy-path correctness; `translate()`
  fallback on garbage (raw source, `parse_failures == 1`); unit-confusion detection.
- `@pytest.mark.api` (skip if no key): one real `translate()` → 7 finite numbers, cost/latency
  recorded; optional small non-determinism probe.

## 5. Cost estimate (Haiku)

Smoke 1 + dose 6 + non-determinism 5 ≈ **12 calls ≈ ~$0.01** (IMPROVEMENT.md §5).

## 6. Success criteria (IMPROVEMENT.md §7)

1. B2-Live RMSE measured, comparable to B2-sim. 2. Latency recorded vs PSL 125 µs.
3. Non-determinism quantified. 4. `blog.ja.md` "future work" replaced with measured data.
5. Existing tests unaffected (`make test-fast` green, unchanged count).

## 7. Touch list

`eval/baselines.py` (class + integration) · `eval/b2_live.py` (new) · `Makefile` (2 targets) ·
`tests/test_b2_live.py` (new) · `REPORT.md` (new, results) · `blog.ja.md` · `IMPROVEMENT.md` (resolved table).
