# Implementation Prompt for Claude Code

You are working on PSL-Bench, a Physical Semantic Layer verification bench at `/Users/shibuiyusuke/tmp/robotics-experiments/PhysicalSemanticLayer/`. Read CLAUDE.md (development rules) and PROJECT.md (research goals) thoroughly before starting. Follow every rule in CLAUDE.md strictly.

## Current state

The codebase is mature: 5 adapters (Panda, AMR, Drone, ClaudeAgent, CloudData), 7 scenarios (all PASS online and offline), 244 tests (160 unit + 50 oracle + 24 metamorphic + 40 scenario, 4 CLIP skipped), multi-seed evaluation (5 seeds × 7 scenarios = 35 runs, all PASS), and discovery APIs on the pseudo-cloud. All 5 hypotheses (H1-H5) are statistically supported.

IMPROVEMENT.md lists two remaining items:
- **R1**: VLA real CLIP inference is coded but untested (open-clip-torch not installed). Hash fallback works.
- **R2**: `get_vla_info()` in `eval/runner/manifest.py` reports mode based on import availability, not actual runtime VLA encoder state — so the manifest can say "clip" when hash fallback was actually used.

PROJECT.md defines two unstarted phases:
- **Phase 4**: Stress testing — schema fuzzing, clock skew injection beyond S7's basic version, reality gap simulation, new object grounding generalization
- **Phase 5**: Self-improving loop — "LLM proposes, ground truth disposes", using task success/failure as training signal

Your task is to fix R1/R2 and implement Phase 4 stress testing. Execute the following phases in order. After each phase, run `make check` and fix any failures before proceeding.

---

## Phase 1: Fix R2 — `get_vla_info()` accuracy

The function in `eval/runner/manifest.py` currently checks whether `open_clip` is importable. This is wrong because the orchestrator always calls `VLAEncoder(use_real_clip=False)`. Fix it to reflect reality.

1. Change `get_vla_info()` to accept an optional `VLAEncoder` instance parameter:

```python
def get_vla_info(encoder: object | None = None) -> dict[str, str]:
    """Get VLA encoder info. If encoder is provided, report its actual state."""
    if encoder is not None and hasattr(encoder, "_use_real_clip"):
        if encoder._use_real_clip:
            return {"mode": "clip", "model_version": "ViT-B-32/laion2b_s34b_b79k"}
        return {"mode": "hash_fallback", "model_version": "n/a"}
    # Fallback: check import availability
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
        return {"mode": "clip", "model_version": "ViT-B-32/laion2b_s34b_b79k"}
    except ImportError:
        return {"mode": "hash_fallback", "model_version": "n/a"}
```

2. In `write_manifest()`, keep the existing call `"vla": get_vla_info()` as-is (the caller can optionally pass the encoder). The orchestrator should pass the VLA encoder to `get_vla_info` when available.

3. In `eval/scenarios/orchestrator.py`, the VLA encoder `vla` is already created. In each scenario eval file (s1-s7), update the `write_manifest()` call: pass `vla_encoder=vla` to the metrics dict so that scenario evals can optionally pass it. Simpler: just add `metrics["vla_mode"] = "hash_fallback" if not vla._use_real_clip else "clip"` in the orchestrator before returning the result. Do this in `run_orchestrator()` right after the VLA encoder is created, and add a `vla_mode: str` field to `OrchestratorResult`.

4. Test: add a test in `tests/test_vla_encoder.py`:
```python
def test_get_vla_info_reflects_actual_mode(self) -> None:
    from eval.runner.manifest import get_vla_info
    enc = VLAEncoder(use_real_clip=False, seed=42)
    info = get_vla_info(enc)
    assert info["mode"] == "hash_fallback"
```

---

## Phase 2: Phase 4 Stress Testing — Schema Fuzzing

PROJECT.md Phase 4 calls for "schema fuzzing" — applying random heterogeneity transforms beyond the controlled 6-dose sweep to find breaking points.

1. Create `eval/stress/schema_fuzzing.py`:

```python
"""Schema fuzzing — randomized heterogeneity stress test.

Generates random SchemaTransform parameters from a wide range,
applies them to the R2R pipeline, and records where PSL breaks.
"""
```

Implement:
- `FuzzResult` dataclass: `transform` (SchemaTransform), `joint_rmse` (float), `info_loss` (float), `commutativity_div` (float), `gate_accepted` (bool), `contract_valid` (bool)
- `fuzz_schema(n_samples: int, seed: int, sim: MuJoCoSim, adapter: PandaAdapter) -> list[FuzzResult]`:
  - For each sample: generate random `unit_scale` in [0.01, 10000], `frame_rotation_z_rad` in [0, 2π], `sensor_noise_std` in [0, 0.1], `position_offset` as random 7-vector in [-0.5, 0.5]
  - Apply transform, translate, measure RMSE/info_loss/commutativity, check safety gate, check contract
  - Return all FuzzResults
- `find_breaking_point(results: list[FuzzResult]) -> dict[str, float]`:
  - Identify the threshold where RMSE > 0.01, info_loss > 0.5, gate rejection starts
  - Return dict with estimated thresholds per dimension

2. Create `eval/stress/__init__.py` (empty docstring).

3. Create `tests/test_schema_fuzzing.py`:
- Test that fuzz_schema with 20 samples runs without error
- Test that identity transform (all defaults) gives RMSE = 0
- Test that extreme noise (std=0.1) gives non-zero RMSE
- Test that find_breaking_point returns valid thresholds

4. Add Makefile target:
```makefile
.PHONY: stress-fuzz
stress-fuzz: ## Run schema fuzzing stress test (50 random transforms)
	$(PYTHON) -c "from eval.stress.schema_fuzzing import fuzz_schema; from sim.wrapper import MuJoCoSim; from psl.adapters.robots.panda.adapter import PandaAdapter; sim=MuJoCoSim(); sim.step(100); r=fuzz_schema(50, 42, sim, PandaAdapter()); print(f'{len(r)} fuzz runs complete, {sum(1 for x in r if x.gate_accepted)} accepted')"
```

---

## Phase 3: Phase 4 Stress Testing — Clock Skew Injection

S7 already does basic clock skew. Extend it with a systematic clock skew stress test.

1. Create `eval/stress/clock_skew.py`:

```python
"""Clock skew stress test — systematic causality violation probing.

Injects increasing clock skew between simulated sensor streams
and measures when the safety gate's causal ordering check starts
rejecting writes.
"""
```

Implement:
- `SkewResult` dataclass: `skew_seconds` (float), `causal_violations` (int), `gate_rejections` (int), `total_writes` (int), `latency_tolerance_exceeded` (bool)
- `sweep_clock_skew(skew_range: list[float], seed: int) -> list[SkewResult]`:
  - For each skew value: create a sim, step it, create adapter+gate+world_model
  - Write Phytes with timestamps offset by the skew amount
  - Count how many writes the gate rejects due to causal ordering
  - Return results
- Default skew_range: [0.0, 0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
- This measures the gate's sensitivity curve: at what skew does it start rejecting?

2. Create `tests/test_clock_skew_stress.py`:
- Test zero skew → zero violations
- Test large skew (5.0s) → non-zero violations or gate rejections
- Test monotonicity: violations should be non-decreasing with skew

3. Add Makefile target:
```makefile
.PHONY: stress-skew
stress-skew: ## Run clock skew stress test
	$(PYTHON) -c "from eval.stress.clock_skew import sweep_clock_skew; r=sweep_clock_skew([0,0.001,0.01,0.05,0.1,0.5,1.0,5.0], 42); [print(f'skew={x.skew_seconds:.3f}s violations={x.causal_violations} rejections={x.gate_rejections}') for x in r]"
```

---

## Phase 4: Phase 4 Stress Testing — New Object Grounding Generalization

Test whether the VLA encoder (even in hash-fallback mode) can handle objects never seen during development.

1. Create `eval/stress/grounding_generalization.py`:

```python
"""Grounding generalization test — holdout objects.

Tests VLA encoder on objects not present in any scenario,
measuring embedding distinctiveness and affordance prediction
quality on unseen objects.
"""
```

Implement:
- `HOLDOUT_OBJECTS`: list of 20 novel object names not used in any scenario (e.g., "wrench", "capacitor", "syringe", "o-ring", "conveyor_belt", "heat_sink", "gasket", "solenoid", "fuse", "relay", "diode", "resistor", "transistor", "inductor", "spring", "washer", "dowel_pin", "cotter_pin", "retaining_ring", "shaft_collar")
- `KNOWN_OBJECTS`: list of objects from scenarios ("blue_gear", "panda_arm", "amr_transport", etc.)
- `GeneralizationResult` dataclass: `object_name`, `embedding_norm` (should be 1.0), `nearest_known_cosine` (similarity to closest known object), `affordance_predicted` (bool), `material_class` (str)
- `test_generalization(holdout: list[str], known: list[str], seed: int) -> list[GeneralizationResult]`:
  - Encode all holdout and known objects with VLAEncoder
  - For each holdout: compute cosine similarity to all known objects, record nearest
  - Predict affordances, record material class
  - Return results
- `compute_generalization_metrics(results: list[GeneralizationResult]) -> dict[str, float]`:
  - `mean_nearest_cosine`: average similarity to nearest known (should be < 1.0, showing distinctiveness)
  - `embedding_coverage`: fraction of holdout objects with valid embeddings (should be 1.0)
  - `affordance_coverage`: fraction with affordance predictions (should be 1.0)
  - `material_diversity`: number of distinct material classes predicted

2. Create `tests/test_grounding_generalization.py`:
- Test all holdout objects get valid embeddings (norm ≈ 1.0)
- Test holdout objects are distinct from known objects (cosine < 0.99)
- Test all holdout objects get affordance predictions
- Test material diversity > 1 (not all predicted as same material)

3. Add Makefile target:
```makefile
.PHONY: stress-grounding
stress-grounding: ## Run grounding generalization test (20 holdout objects)
	$(PYTHON) -c "from eval.stress.grounding_generalization import test_generalization, compute_generalization_metrics, HOLDOUT_OBJECTS, KNOWN_OBJECTS; r=test_generalization(HOLDOUT_OBJECTS, KNOWN_OBJECTS, 42); m=compute_generalization_metrics(r); [print(f'{k}: {v:.4f}') for k,v in m.items()]"
```

---

## Phase 5: Phase 4 Stress Testing — Dose-Response with Baselines

Create a unified stress report that runs all three stress tests and compares PSL vs baselines.

1. Create `eval/stress/report.py`:

```python
"""Unified Phase 4 stress test report.

Runs schema fuzzing, clock skew, and grounding generalization,
then writes a JSON report with all results.
"""
```

Implement:
- `run_stress_suite(seed: int, n_fuzz: int = 50) -> dict`:
  - Run schema fuzzing (n_fuzz samples)
  - Run clock skew sweep (default range)
  - Run grounding generalization (all holdout objects)
  - Collect summary metrics from each
  - Return combined report dict
- `main()`: Run suite, save to `experiments/runs/stress_report.json`, print summary

2. Add Makefile target:
```makefile
.PHONY: stress-all
stress-all: ## Run full Phase 4 stress test suite
	$(PYTHON) -c "from eval.stress.report import main; main()"
```

3. Then actually execute `make stress-all` and capture the output.

---

## Phase 6: Integration and Documentation

1. Update `docs/metamorphic.md` to add stress testing section:
```markdown
## Stress Tests (Phase 4)

### Schema Fuzzing
- Random heterogeneity transforms from wide parameter ranges
- Identifies breaking points per dimension (unit scale, frame rotation, noise)

### Clock Skew Injection
- Systematic causality violation probing
- Measures safety gate sensitivity curve

### Grounding Generalization
- Holdout objects never seen during development
- Tests embedding distinctiveness and affordance prediction coverage
```

2. Update `README.md` mechanism status table: add a row for stress testing.

3. Run `make check` to verify everything passes.

4. Run `make stress-all` to execute the stress suite.

5. Update IMPROVEMENT.md: mark R1/R2 status, add Phase 4 stress testing as completed, note any new findings from stress tests.

---

## Final Verification

After all phases:
1. `make check` — must be green
2. `make test` — all tests pass
3. `make stress-all` — stress suite completes
4. `make scenario-s4` — drone integration still passes
5. Verify no regressions in existing 244 tests
6. Update REPORT.md with stress test results section

Do not skip any phase. Do not leave stubs or TODOs. Every function must work. Follow CLAUDE.md rules exactly.
