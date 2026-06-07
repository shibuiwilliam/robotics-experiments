# PSL-Bench Verification Scenarios

7 business scenarios, each centered on a distinct breaking point where naive translation fails and PSL's value becomes measurable.

## Scenario Summary

| # | Scenario | Breaking Point | RQ/H | Phase |
|---|----------|---------------|------|-------|
| s1 | Mixed Fleet Pick | Uncertainty propagation + R2R mismatch | RQ1, RQ2/H2, RQ5/H4 | 0-3 |
| s2 | Line Changeover | Commutativity + safety gate rejection | RQ3/H3, RQ7 | 2-3 |
| s3 | Lab Custody | Provenance chain as business deliverable | RQ1 | 1-3 |
| s4 | Field Inspection | Multi-resolution fusion + bidirectional anchoring | RQ1 | 3 |
| s5 | Pharma Logistics | Neuro-symbolic binding + provenance poisoning | RQ7 | 4 |
| s6 | E-waste Disassembly | Open-world affordance grounding | RQ6/H5 | 4-5 |
| s7 | Degraded Ops | Clock skew causality + graceful degradation | Causal | 4 |

## Two-Layer Success

Every scenario measures both:
1. **Business success**: Did the task goal get met? (e.g., correct item in correct tray)
2. **PSL success**: Did the PSL-specific metric meet its threshold? (e.g., calibration NLL, commutativity divergence)

Both must pass — business success alone is insufficient because a naive translator can succeed by luck.

## Running Scenarios

```bash
# Run a specific scenario evaluation
.venv/bin/python -c "from eval.scenarios.s1_mixed_fleet_pick.eval import evaluate_s1; print(evaluate_s1())"

# Run all scenario tests
make test-oracle
```

## Coverage Matrix

Each scenario exercises all 3 flows (R2R, A2R, A2A) plus document-to-physical anchoring, and forces its breaking point to actually occur. See SCENARIOS.md for the full specification.
