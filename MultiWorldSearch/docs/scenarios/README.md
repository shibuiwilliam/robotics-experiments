# MWS Verification Scenarios

Multi-World Search includes 7 verification scenarios that exercise the full MWS
pipeline — from MuJoCo simulation and synthetic business data through multi-index
fused retrieval to agent/VLA action and audit. Each scenario targets specific
research hypotheses (PROJECT.md §8) and MWS mechanisms (PROJECT.md §9).

All scenarios run offline on a MacBook with no cloud API keys required
(`MWS_CLOUD_MODE=mock`). They are deterministic under a fixed seed and produce
run artifacts (`manifest.json`, `metrics.json`, `audit.jsonl`) in `runs/<RUN_ID>/`.

## Scenario Index

| # | Scenario | CLI Key | Hypotheses | Document |
|---|----------|---------|------------|----------|
| 1 | [Maintenance Handoff](01_maintenance_handoff.md) | `maintenance_handoff` | H1, H3, H8 | Flagship |
| 2 | [Physical-Record Reconciliation](02_physical_record_reconciliation.md) | `physical_record_reconciliation` | H4, H9 | |
| 3 | [Collective Weak Signal](03_collective_weak_signal.md) | `collective_weak_signal` | H5 | |
| 4 | [New SKU Rampup](04_new_sku_rampup.md) | `new_sku_rampup` | H1 | |
| 5 | [Incident Response](05_incident_response.md) | `incident_response` | H6, H2 | |
| 6 | [Counterfactual Safety](06_counterfactual_safety.md) | `counterfactual_safety` | H6 | |
| 7 | [Order to Fulfillment](07_order_to_fulfillment.md) | `order_to_fulfillment` | H4, H8 | |

## Running Scenarios

```bash
# Run a single scenario
uv run python -m mws.cli scenario run --name maintenance_handoff --seed 0

# List all available scenarios
uv run python -m mws.cli scenario list

# Run all scenarios
for s in $(uv run python -m mws.cli scenario list); do
  uv run python -m mws.cli scenario run --name "$s" --seed 0
done

# View results
uv run python -m mws.cli eval run --run-id <RUN_ID>
```

## Shared Framework

All scenarios share a common `BaseScenario` lifecycle with 8 phases:

1. **setup** — Build the MuJoCo world and synthetic business data; issue a `RUN_ID`.
2. **seed_memory** — Inject precondition atoms (historical records, past skill demos).
3. **inject** — Inject the condition under test (anomaly, drift, hazard, order, etc.).
4. **perceive** — Run the simulation and extract observation atoms from sensors.
5. **index** — Offline indexing (no-op in mock mode; Gemini Embedding 2 Batch in live).
6. **act** — Consumers (ADK agents, VLA policies) query MWS and take actions.
7. **evaluate** — Compute metrics from ground-truth labels and save artifacts.
8. **teardown** — Release resources and flush the audit log.

## Traceability Matrix

| Scenario | Primary Hypotheses | Primary Mechanisms | Scenario-Specific Metrics |
|----------|-------------------|-------------------|--------------------------|
| 1 Maintenance | H1, H3, H8 | Cross-modal fusion, RA-VLA, handoff, audit | Skill transfer, audit completeness |
| 2 Reconciliation | H4, H9 | Physical vs record, multi-observer, write-back | Reconciliation accuracy, stale hit rate |
| 3 Weak Signal | H5 | Consolidation, correlation, standing query | Cluster purity, compression vs recall |
| 4 SKU Rampup | H1 | Skill propagation, multi-instance, federation | Transfer gain, cold start time |
| 5 Incident | H6, H2 | Standing query, curiosity, coordination | Response latency, plan failure reduction |
| 6 Counterfactual | H6 | Sim fork/rollout, safety decisions | Collapse avoidance, rollout cost |
| 7 Order E2E | H4, H8 | Cross-system, physical vs digital, write-back | Ghost inventory reduction, E2E success |
