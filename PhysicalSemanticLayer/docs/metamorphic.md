# Metamorphic Relations

> Label-free invariance tests for PSL translations.

## Active Relations (Phase 0)

### MR1: Frame Equivariance
- **Property**: `T(g . x) == g . T(x)` for any SE(3) transform `g`
- **Meaning**: Applying a rigid transform before translation equals transforming the result
- **Tolerance**: 1e-6 (SE(3) distance)
- **Implementation**: `eval/metamorphic/frame_equivariance.py`

### MR2: Unit Invariance
- **Property**: Rescaling units (m -> mm -> m) round-trips exactly
- **Meaning**: Unit representation doesn't affect semantic content
- **Tolerance**: 1e-10
- **Implementation**: `eval/metamorphic/unit_invariance.py`

### MR3: Time Equivariance
- **Property**: Shifting timestamps by dt shifts all output timestamps by dt without changing values
- **Meaning**: Translation is time-shift equivariant
- **Tolerance**: 1e-10
- **Implementation**: `eval/metamorphic/time_equivariance.py`

### MR4: Object Permutation Invariance
- **Property**: Double round-trip (native -> IR -> native -> IR -> native) is idempotent
- **Meaning**: Translation is stable under iteration
- **Tolerance**: 1e-10
- **Implementation**: `eval/metamorphic/time_equivariance.py` (`check_object_permutation_invariance`)

### MR5: Compositionality / Commutativity
- **Property**: `A -> IR -> B` diverges from `A -> IR -> C -> IR -> B` within threshold
- **Meaning**: Multi-hop translation paths approximately commute
- **Tolerance**: 1e-6
- **Implementation**: `eval/metamorphic/compositionality.py`

## Stress Tests (Phase 4)

### Schema Fuzzing
- Random heterogeneity transforms from wide parameter ranges (unit scale 0.01–10000, frame rotation 0–2π, noise 0–0.1)
- Identifies breaking points per dimension (noise std, gate rejection threshold)
- Implementation: `eval/stress/schema_fuzzing.py`
- Runner: `make stress-fuzz`

### Clock Skew Injection
- Systematic causality violation probing (0–5 seconds of clock offset)
- Measures safety gate sensitivity curve for cross-domain causal ordering
- Implementation: `eval/stress/clock_skew.py`
- Runner: `make stress-skew`

### Grounding Generalization
- 20 holdout objects never seen during development
- Tests embedding distinctiveness (cosine similarity to known objects) and affordance prediction coverage
- Implementation: `eval/stress/grounding_generalization.py`
- Runner: `make stress-grounding`

### Full Stress Suite
```bash
make stress-all  # Runs all three stress tests, saves JSON report
```

## Test Runner

```bash
make test-mr       # Metamorphic tests via hypothesis
make stress-all    # Phase 4 stress tests
```

All hypothesis tests use `derandomize=True` for reproducibility.
