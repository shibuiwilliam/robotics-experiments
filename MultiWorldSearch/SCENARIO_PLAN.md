# SCENARIO_PLAN.md — MWS Scenario Implementation

Living checklist. Follows SCENARIOS.md §5 implementation order.

**Status**: All 7 scenarios implemented. 120 tests, all green.

---

## 0. Shared Framework (SCENARIOS.md §1–§2)

- [x] `mws/scenarios/base.py` — BaseScenario ABC with 8-phase lifecycle
- [x] `mws/scenarios/registry.py` — scenario registry + dispatch + `scenario list`
- [x] `mws/scenarios/audit.py` — JSONL audit logger (append-only)
- [x] `mws/scenarios/ground_truth.py` — relevance label auto-generation from sim+business data
- [x] CLI wired: `scenario run --name <key>`, `scenario list`
- [x] Gate: all 7 scenarios complete, write manifest + audit.jsonl

## 1. Scenario 1 — maintenance_handoff ✅

- [x] `mws/scenarios/s1_maintenance_handoff/` (scenario.py, world.py, data.py)
- [x] World: pump_07 + valve_03 + workbench + patrol_robot + mobile_manipulator
- [x] Business: CMMS history, ERP parts, SOP with torque specs, technician shift
- [x] seed_memory: past VLA demo "loosen valve_03"
- [x] inject: compound anomaly (vibration↑ + temperature↑) on pump_07
- [x] Queries: OpsAgent + Manipulator
- [x] Acceptance: 7 tests in test_maintenance_handoff.py

## 2. Scenario 2 — physical_record_reconciliation ✅

- [x] `mws/scenarios/s2_physical_record_reconciliation/` (scenario.py)
- [x] World: shelves + misplaced asset on floor + 2 inventory robots
- [x] Business: WMS (drifted count=50), asset registry (status mismatch)
- [x] inject: WMS drift, observer noise variance
- [x] H9: fusion estimate (error=0.14) beats single observer (error=1.0)
- [x] Acceptance: 5 tests — fusion, discrepancy ticket, write-back

## 3. Scenario 3 — collective_weak_signal ✅

- [x] `mws/scenarios/s3_collective_weak_signal/` (scenario.py, world.py, data.py)
- [x] World: 10 objects, 4 from lot_L with micro_defect
- [x] Business: MES lot records, supplier registry
- [x] inject: defects concentrated in lot_L, scattered across time/instances
- [x] H5: consolidation surfaces lot_L with cluster purity=1.0
- [x] Acceptance: 4 tests — pattern discovered, standing query registered

## 4. Scenario 4 — new_sku_rampup ✅

- [x] `mws/scenarios/s4_new_sku_rampup/` (scenario.py)
- [x] World: pick + drop stations, demonstrator + swarm
- [x] Business: product master (sku_X100→destination)
- [x] inject: A/B toggle (demo present vs absent)
- [x] H1: transfer gain > 0 (with-demo=1.0, without=0.0)
- [x] Acceptance: 3 tests — transfer gain, deterministic

## 5. Scenario 6 — counterfactual_safety ✅

- [x] `mws/scenarios/s6_counterfactual_safety/` (scenario.py, world.py, data.py)
- [x] World: 3 stacked boxes + pressurized valve + safety robot
- [x] Business: SOP risk limits, equipment specs
- [x] inject: hazardous config exceeding limits
- [x] H6: rollout predicts collapse → unsafe action avoided
- [x] Acceptance: 4 tests — avoidance, atoms stored & retrievable

## 6. Scenario 5 — incident_response ✅

- [x] `mws/scenarios/s5_incident_response/` (scenario.py, world.py, data.py)
- [x] World: room_A (leak), room_B (occluded), exits, 3 robots
- [x] Business: SDS, safety SOP, floor plan, duty roster
- [x] inject: substance_X leak + occlusion
- [x] Standing query fires, curiosity loop fills gap, plan uses SDS+exit+roster
- [x] Acceptance: 5 tests — standing query, recon, all sources used

## 7. Scenario 7 — order_to_fulfillment ✅

- [x] `mws/scenarios/s7_order_to_fulfillment/` (scenario.py, world.py, data.py)
- [x] World: shelves (A/B/C), packing station, picking robot
- [x] Business: WMS (ghost inventory sku_C=5), order, product master
- [x] inject: order event + ghost inventory + damaged sku_A
- [x] H4: physical observation overrides WMS → re-order + notify + ERP close
- [x] Acceptance: 5 tests — ghost detected, write-back, procurement, notification

---

## Quality Summary

| Check | Status |
|---|---|
| All 7 `scenario run` complete mock/no-key | ✅ |
| Deterministic (same seed → same result) | ✅ |
| `runs/<RUN_ID>/` has manifest + metrics + audit.jsonl | ✅ |
| Ground-truth retrieval metrics computed | ✅ |
| Scenario-specific success criteria asserted | ✅ |
| Cloud boundary intact | ✅ |
| Dependency direction + embedding-space discipline | ✅ |
| ruff + pyright + pytest all green | ✅ 120 tests |
