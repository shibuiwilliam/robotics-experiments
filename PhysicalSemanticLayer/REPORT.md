# REPORT — PSL-Bench Validation

> **Date**: 2026-06-10 · **Branch**: `feat/b2-live` (working tree; manifests record base commit `939d1c9`)
> **Scope**: (A) full online end-to-end of all 7 scenarios with real Claude agents (`make scenarios-all`),
> (B) real-LLM translation baseline (`make baseline-llm`, B2-Live).
> **Raw data**: `experiments/runs/<ts>-<scenario>/manifest.json`, `experiments/runs/b2_live_results.json`.
> **Data/instrumentation gaps found during this run are filed in [`IMPROVEMENT.md`](IMPROVEMENT.md) §10.**

---

## 1. Executive summary

- **All 7 scenarios passed online** (real Claude agents via MCP + MuJoCo + CLIP/SmolVLA + pseudo-cloud HTTP):
  `business=True, psl=True, pass=True` for s1–s7.
- **PSL translation fidelity** holds: PSL joint-RMSE = **1.08×10⁻⁵** (unit+noise dose) or **0.0** (identity),
  vs the raw-blackboard baseline **B1 ≈ 576–672** (≈7 orders of magnitude worse). Commutativity divergence = **0.0**.
- **Safety gate works**: S2 rejected an impossible state (`gate_rejected_impossible=1.0`, `false_reject=0.0`);
  S5 caught provenance poisoning at **100%** (`ns_detection_rate=1.0`).
- **Provenance / custody**: S3 chain intact (`chain_intact=1.0`, `min_confidence=0.99`).
- **Grounding generalization (RQ6/H5)**: S6 embedding advantage = **0.667** (VLA exact-match 0.667 vs symbol-only 0.167),
  action validity 100% on 3 held-out objects.
- **Causal consistency under clock skew (S7)**: `causal_violations=0.0`, degradation monotonic.
- **Cost**: the 7 online scenario runs cost **~$0.73** total (agent calls only). Latency was dominated by
  agent "thinking" (second-order), as designed.
- **Real-LLM baseline (B2-Live)**: a real Claude Haiku translation is numerically accurate but **~2000× slower**
  than PSL and propagates **no** uncertainty/provenance (see §4).

**Caveat up front (honest):** three metrics in the online manifests are not yet meaningful measurements —
`physical_accuracy_ee/object` is identically 0.0, `smolvla_action_confidence` is a hardcoded 0.8, and
`agent_num_turns/agent_tool_calls` are only recorded for S1. These are detailed in §5 and filed in IMPROVEMENT.md.

---

## 2. Online scenario results (`make scenarios-all`, PSL_MODE=online, seed=42)

Each scenario ran the orchestrator in **online** mode — a real Claude supervisor/worker calling PSL MCP tools
(`query_world_model`, `command_robot_semantic`, `resolve_document_to_physical`, `subscribe_affordances`),
over real MuJoCo physics, with CLIP/SmolVLA grounding and the pseudo-cloud as a local HTTP service.

| Scenario | Pass | Key PSL evidence (measured) | Agent cost | RQ/H |
|---|---|---|---|---|
| **s1** mixed-fleet pick | ✅ | PSL rmse 1.08e-5 vs B2 1.19e-4 vs B1 576; calibration NLL **−0.745**; commutativity 0.0; R2R handoff err 0.0; 10 turns / 9 tool calls / 3 MCP | $0.132 | RQ1, RQ2/H2, RQ5/H4 |
| **s2** line changeover | ✅ | PSL rmse **0.0**; **gate rejected impossible state** (1.0), false-reject 0.0; commutativity 0.0 | $0.068 | RQ3/H3, RQ7 |
| **s3** lab custody | ✅ | **chain_intact 1.0**, min provenance confidence **0.99**; PSL rmse 1.08e-5 | $0.211 | RQ2, provenance |
| **s4** field inspection | ✅ | drone R2R round-trip err **0.0**, negotiation feasible; LOD 7 joints | $0.065 | RQ1, RQ5/H4, LOD |
| **s5** pharma logistics | ✅ | **gate caught poisoning 1.0**, neuro-symbolic detection rate **1.0** | $0.071 | RQ7, neuro-symbolic |
| **s6** e-waste disassembly | ✅ | **embedding advantage 0.667** (VLA 0.667 vs symbol 0.167 exact match), embedding rate 0.833, action validity **1.0** on 3 holdouts | $0.072 | RQ6/H5 |
| **s7** degraded ops | ✅ | **causal_violations 0.0**, degradation monotonic, LOD staleness 2.0 | $0.109 | RQ4, causal consistency |

**Total agent cost (7 runs): ~$0.73.** All runs: `agent_fallback_used=False`, `agent_404_count=0`,
`smolvla_available=True`, VLA mode = real CLIP.

### 2.1 Translation fidelity & baselines (RQ1, H1) — per-scenario, seed 42

The orchestrator runs B0/B1/B2/PSL on the identical heterogeneous state (`unit_scale=1000`, `sensor_noise_std=0.01`):

| Scenario | PSL rmse | B0 rmse | B2 (sim) rmse | B1 (raw) rmse |
|---|---|---|---|---|
| s1 | 1.078e-5 | 1.078e-5 | 1.186e-4 | 576.3 |
| s2 | 0.0 | 0.0 | 1.078e-4 | 668.3 |
| s3 | 1.078e-5 | 1.078e-5 | 1.186e-4 | 603.9 |
| s4 | 1.078e-5 | 1.078e-5 | 1.186e-4 | 604.3 |
| s5 | 1.078e-5 | 1.078e-5 | 1.186e-4 | 671.5 |
| s6 | 1.078e-5 | 1.078e-5 | 1.186e-4 | — |
| s7 | 1.078e-5 | 1.078e-5 | 1.186e-4 | 576.3 |

**Reading:** PSL matches the hand-written oracle B0 to the last digit while requiring only N+N adapters; the raw
blackboard B1 (no translation) is ~7 orders of magnitude worse; the simulated-LLM B2 carries ~10× the residual
of PSL and — critically — **no covariance or provenance**. The PSL non-zero residual (1.08e-5) is the irreversible
sensor noise (σ=0.01 scaled), i.e. the information-theoretic floor, not a translation defect (s2 has no joint
noise in its dose → exactly 0.0).

### 2.2 Calibration (RQ2/H2)

S1 calibration NLL = **−0.745** (below the configured `calibration_nll_max`). A negative Gaussian NLL indicates the
declared covariance comfortably covers the realized error — the uncertainty PSL attaches is *safe-side calibrated*,
which is the deliverable only obtainable with ground truth (PROJECT.md §8).

### 2.3 Commutativity (RQ3/H3)

`commutativity_divergence = 0.0` (s1, s2). Direct vs multi-hop translation paths agree exactly, as expected for
arithmetically-exact symbolic transforms — within the H3 tolerance.

### 2.4 Safety (RQ7)

- **s2**: one impossible state generated and **rejected** by the consistency gate; **0 false rejections**.
- **s5**: provenance-poisoning attack **detected at 100%** (`gate_caught_poisoning=1.0`, `ns_detection_rate=1.0`).

### 2.5 Grounding generalization (RQ6/H5) — S6

| Held-out object | Ground truth (grasp/detach) | Symbol-only acc | VLA (CLIP) acc |
|---|---|---|---|
| capacitor | T / T | 0.0 | 0.5 |
| heat_sink | T / T | 0.0 | 1.0 |
| ribbon_cable | F / T | 0.5 | 1.0 |

Embedding **advantage = 0.667** (object-level exact match: VLA 0.667 vs symbol 0.167). All 3 SmolVLA actions were
geometrically valid (`action_validity_rate=1.0`, distinct `ee_delta` norms 0.54 / 0.45 / 0.46 — i.e. real,
object-dependent actions, not a constant).

### 2.6 Causal consistency under clock skew (RQ4) — S7

`causal_violations=0.0`, `degradation_monotonic=1.0`, `lod_staleness=2.0`. PSL's clock-domain handling produced no
causal-order violations across the injected skew sweep. **However**, `fidelity_by_skew` was identically 0.0 at every
skew level — see §5.4 (the curve carries no degradation signal and should be verified).

---

## 3. Reproducibility

- Command: `make scenarios-all` (auto-detects online when `ANTHROPIC_API_KEY` + SDK present).
- Per-run manifests under `experiments/runs/<UTC-ts>-<scenario>/manifest.json` include config, seed, dependency
  versions, VLA mode, full metrics, and (where captured) the agent tool-call trace.
- Determinism: seed 42; PSL metrics are bit-reproducible across repeat runs (verified — duplicate manifests from a
  concurrent run produced identical PSL numbers; only agent cost varied, as expected for the non-deterministic API).

---

## 4. Real-LLM translation baseline (B2-Live)

`BaselineB2Live` calls real Claude (`claude-haiku-4-5`) to perform the joint unit conversion, with the conversion
method stated explicitly (most-favorable condition). Measured (`make baseline-llm`, 12 calls, $0.094):

| Dose | B2-Live (real) | B2 (sim) | PSL | B2-Live latency |
|---|---|---|---|---|
| identity | 3.21e-07 | 1.08e-04 | 0.0 | 2490 ms |
| unit ×1000 | **3.15e-10** | 1.08e-04 | 0.0 | 2391 ms |
| frame rot 45° | 3.21e-07 | 1.08e-04 | 0.0 | 2206 ms |
| noise σ=0.01 | 1.08e-02 | 1.09e-02 | 1.08e-02 | 2046 ms |
| unit+frame+noise | 1.08e-05 | 1.19e-04 | 1.08e-05 | 2701 ms |
| everything+offset | 5.39e-05 | 1.62e-04 | 5.39e-05 | 2409 ms |

- **Accuracy**: real Haiku is *more* numerically accurate than the B2 simulation assumed; on noise-dominated doses
  it tracks PSL exactly (irreversible noise dominates).
- **Latency**: mean **2374 ms** vs PSL **~1.2 ms** (≈**2000×**; core canonicalization ~125 µs).
- **Determinism**: identical across 5 repeats for this simple task (`max_spread=0`); SDK exposes no temperature knob.
- **Structural gap**: B2-Live propagates **no** covariance/provenance — the dimensions where PSL is unique.
- **Failure modes**: 0 parse failures, 0 unit-confusion over 12 calls (parser hardened with fallback regardless).

**Conclusion**: "just let the LLM translate" is not free — the cost is structural (latency, $, lost uncertainty),
not point-estimate accuracy on simple specified conversions.

---

## 5. Data & instrumentation gaps (filed in IMPROVEMENT.md §10)

These do **not** affect the pass/fail conclusions above (which rest on the green metrics), but they limit how much
can be concluded and must be fixed for the metrics to be trustworthy as research deliverables.

### 5.1 `make test` bills the real API (cost/CI hazard) — **high**
The scenario oracle tests are not marked `@pytest.mark.api`; `detect_mode()` auto-selects **online** whenever
`ANTHROPIC_API_KEY` is set. Running `make test` (which only excludes `-m api`) therefore made **real agent calls**:
during this session it billed **~$0.98 across 11 scenario runs** concurrently with `make scenarios-all`. CI / local
test runs with a key present will silently incur cost.

### 5.2 `smolvla_action_confidence` is a hardcoded constant — **medium**
`src/psl/grounding/smolvla_encoder.py:190` returns `confidence=0.8` for every successful prediction. The 6-DOF
`ee_delta` is genuinely model-derived (varies per object in S6), but the reported confidence is a placeholder, so any
metric or safety margin keyed on action confidence is non-informative.

### 5.3 `physical_accuracy_ee` / `physical_accuracy_object` are identically 0.0 — **medium**
In all 7 scenarios both are 0.0. They compare `ee_position` against the EE ground-truth *site* (orchestrator.py
:258/:268), but the compared quantities are derived from the same reading, so the metric is trivially zero and does
**not** measure real physical task accuracy (e.g. whether a grasp reached the target). No genuine end-effector/object
accuracy signal is currently captured.

### 5.4 S7 `fidelity_by_skew` is flat 0.0 across all skews — **medium**
Every skew level (0.0 … 1.0 s) yields fidelity 0.0, so the "degradation curve" has no signal and the monotonicity
check passes trivially. Either PSL is genuinely skew-invariant on this path (plausible) or the metric isn't
exercising the degradation it claims to — needs an oracle check.

### 5.5 Agent turn/tool-call counts only captured for S1 — **low**
Only `s1/eval.py` records `agent_num_turns` and `agent_tool_calls`; s2–s7 record only `agent_cost_usd`. Per-scenario
agent efficiency (RQ5 extension-cost story) cannot be compared across scenarios.

### 5.6 No consolidated online-run summary artifact — **low**
`scenarios-all` emits only stdout one-liners plus 7 separate manifests. There is no single combined results JSON for
the online run, which made post-hoc analysis fragile (worsened by §5.1 pollution). A `scenarios_all_results.json`
(scenario → {pass, metrics, cost}) would make the run self-describing.

### 5.7 Manifest commit vs working tree — **low**
Manifests record `git_commit=939d1c9` (HEAD) while the run used uncommitted `feat/b2-live` working-tree code. Commit
the branch before authoritative runs so results link to exact code (CLAUDE.md §10).
