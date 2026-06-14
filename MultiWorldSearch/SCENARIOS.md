# SCENARIOS.md — Multi-World Search (MWS) Scenario Design

> **Role of this document**
> This is the design document that concretizes the MWS verification scenarios (the 7 in `PROJECT.md` §9) down to a **granularity Claude Code can implement**.
> The higher-level strategy, hypotheses, and success criteria follow `PROJECT.md`; the development conventions (mock by default, cloud boundary, dependency direction, seed, embedding-space discipline, DoD) follow `CLAUDE.md`.
> This document assumes consistency with those two and minimizes duplicated explanation.

---

## 1. Common Scenario Framework

Every scenario is implemented on a shared foundation. Each section defines only the scenario-specific deltas.

### 1.1 Placement and Registration

- Implementations go in `mws/scenarios/<scenario_key>/`.
- Each scenario implements the common base `BaseScenario` and registers under its name in the registry.
- Run via the unified CLI: `uv run python -m mws.cli scenario run --name <scenario_key> --seed <N>`.

### 1.2 Lifecycle (BaseScenario standard phases)

Every scenario runs in this order. Each phase is deterministic (same seed → same result).

1. `setup(seed, config)` — Build the MuJoCo world and synthetic business data, issue a `RUN_ID`.
2. `seed_memory()` — Inject precondition atoms (existing skill demos, past observations, business records).
3. `inject()` — Inject the event under test (anomaly, record drift, new object, hazard, occlusion, order, etc.).
4. `perceive()` — Generate observation atoms from sensors, normalize via `core`, store in `storage`.
5. `index()` — Offline indexing (mock by default; Gemini Embedding 2 / Batch only when live).
6. `act()` — Consumers (ADK agent, retrieval-augmented VLA, control) query MWS and act.
7. `evaluate()` — Compute metrics and write artifacts and the manifest to `runs/<RUN_ID>/`.
8. `teardown()` — Release resources.

### 1.3 Determinism and Mock

- Default `MWS_CLOUD_MODE=mock`. The cloud (LLM/Embedding) is replaced with a deterministic stub, and all phases complete with no key.
- The mock LLM/VLA is a lightweight stub returning "deterministically plausible behavior." Swapping to live happens only at the adapter boundary.
- All stochastic processing goes through `seed`. A scenario run always leaves a run manifest (`CLAUDE.md` §9).

### 1.4 Audit (provenance)

- Each scenario appends every query, every action, and every write-back to `runs/<RUN_ID>/audit.jsonl`.
- An audit line carries, at minimum: timestamp, RUN_ID, actor, operation type, target atom/entity ID, indices used, projection type, provenance.

### 1.5 Automatic Generation of Relevance Labels (the core of evaluation)

- From MuJoCo ground truth (object IDs, pose, attributes) and the true links in business data, auto-generate the correct atom set per query.
- This computes Recall@k / MRR / nDCG without human annotation (`PROJECT.md` §5.3 / §10).

### 1.6 Query-Spec Notation

Each scenario's MWS query is defined in this form (the implementation corresponds to the query planner in `retrieval`).

```
Query(
  consumer   = ops_agent | manipulator_vla | control | analytics | reactive,
  intent     = natural language or structured intent,
  indices    = {spatial?, temporal?, semantic?, symbolic?, structured?},
  filters    = {entity_id?, region?, time_window?, modality?, policy?},
  projection = text+provenance | pose+tensor | numeric | aggregate,
  qor        = {max_latency, freshness, authority, scope}
)
```

---

## 2. Common Data-Model Conventions

### 2.1 Semantic Schema of the Physical World (MuJoCo)

- Give each body a unique `entity_id` (e.g., `pump_07`, `valve_03`, `sku_A100`) and semantic attributes (kind, lot, state).
- Sensors are the source of observation atoms: RGB-D camera, proprioception, contact, range finder, scalar diagnostic channels (vibration / temperature proxies).
- Objects can be simple shapes (boxes, cylinders); carrying a semantic label is enough (research prototype, per `PROJECT.md` Non-Goals).
- Toggle dynamics injection (move, add, remove) and time dilation (fast worlds) via config.

### 2.2 Synthetic Business-Data Schema (`mws/business/`)

> **Implementation note (2026-06-11)**: The current 7 scenarios generate business data with dedicated generators in each `mws/scenarios/s*/data.py`; the schema/generators in `mws/business/` are a **library (not wired into scenarios)**. The table below is maintained as the canonical minimal schema both should follow.

External systems are stubbed, not really connected. Link the minimal schema to physical entities via `entity_id`.

| Source | Record (minimal fields) |
|---|---|
| CMMS (maintenance) | `work_order_id, entity_id, date, symptom, action, technician` |
| ERP / inventory | `part_id, entity_id, on_hand, location, reorder_point` |
| WMS (warehouse) | `entity_id, recorded_count, location, last_updated` |
| MES / quality | `lot_id, entity_id, station, defect_flag, timestamp` |
| Asset register | `asset_id, entity_id, status(in_service|maintenance|retired), location` |
| Orders | `order_id, sku, qty, customer, status` |
| Roster / shift | `person_id, role, on_call_window` |
| Documents | SOP / manuals (numeric specs like torque) / SDS (material safety). Reference `entity_id`/`substance_id` |

---

## 3. Per-Scenario Specifications

Each scenario is described with a common template. For hypothesis/mechanism numbers see `PROJECT.md` §8/§9.

### Scenario 1 | Multi-actor maintenance handoff (flagship)

- **Goal (business value)**: Reduce MTTR, reuse skills, explainable audit trail.
- **Hypotheses / mechanisms**: H1, H3, H8 / cross-modal & source fusion ◎, retrieval-augmented VLA ◎, multi-instance handoff ◎, provenance/audit ◎.
- **Actor responsibilities**:
  - PatrolRobot: Patrols, observes `pump_07`'s vibration/heat, generates an anomaly atom and links it to the entity node.
  - OpsAgent (ADK): Fused recall → generate work instructions → check parts inventory & staffing → finalize the schedule.
  - MobileManipulator (different embodiment, VLA): Recall geometry, torque spec, and existing skill demos to perform the work.
- **World**: One room with `pump_07` (diagnostic channels), `valve_03`, and a workbench. Two robots of different embodiment.
- **Business / documents**: `pump_07`'s CMMS history, ERP parts inventory, the torque-spec SOP, technician shift.
- **seed_memory**: Inject one "loosen the valve" VLA-demo atom previously taught by another robot.
- **inject**: A deterministic compound anomaly on `pump_07` (vibration↑ + temperature↑).
- **MWS queries**:
  - OpsAgent → `indices={semantic,symbolic,structured,temporal}, filters={entity_id=pump_07}, projection=text+provenance, qor={authority=high}`.
  - Manipulator → `indices={spatial,symbolic}+procedural(skill), filters={entity_id=pump_07,valve_03}, projection=pose+tensor, qor={max_latency=low}`.
- **Success criteria / metrics**: Recall@k of the correct manual + history + skill, work-task success rate, latency decomposition, audit completeness, **skill transfer to a different embodiment succeeds**.
- **Acceptance test (mock, deterministic)**: On anomaly injection the fused query returns manual + CMMS history + skill demo / the Manipulator succeeds via the recalled skill / audit.jsonl records the whole process.
- **CLI**: `scenario run --name maintenance_handoff`.

### Scenario 2 | Reconciling physical reality with the system of record

- **Goal**: Inventory accuracy; close the physical↔digital truth gap.
- **Hypotheses / mechanisms**: H4, H9 / physical-vs-record consistency & freshness ◎, multi-observer fusion ◎, external write-back ◎.
- **Actor responsibilities**:
  - InventoryRobots (multiple): Observe shelf counts and asset state (with viewpoint/noise differences).
  - ReconAgent (ADK): Adjudicate by provenance, trust, freshness, and observer agreement → write back or file a discrepancy ticket.
- **World**: A shelf with a count (ground truth ≈30); one asset that the register says is "in maintenance" but is on the floor. Multiple observers.
- **Business**: WMS record (e.g., 50), asset register (inconsistent status).
- **inject**: Drift the WMS, and give a deterministic disagreement (noise) among observers.
- **MWS query**: ReconAgent → `indices={structured,semantic,temporal}, filters={entity_id}, projection=text+provenance, qor={freshness=strict}`; fusion is a provenance-weighted multi-observer estimate.
- **Success criteria / metrics**: Adjudication accuracy, fused-count error < single-observer error (H9), stale-hit rate, write-back/ticketing accuracy.
- **Acceptance test**: Under deterministic drift, the fused estimate beats a single observation / a discrepancy ticket is generated for the inconsistent asset / the write-back content is correct.
- **CLI**: `scenario run --name physical_record_reconciliation`.

### Scenario 3 | Collective discovery of weak signals by the swarm

- **Goal**: Prevention rather than reaction; quality foresight; supplier accountability.
- **Hypotheses / mechanisms**: H5 / consolidation ◎, cross-source fusion ◎, standing query ○, temporal retrieval.
- **Actor responsibilities**:
  - Swarm: Observe micro-defects across time and instances (each individually harmless).
  - QualityAgent (ADK): Read patterns from consolidation's semantic memory, correlate with MES/supplier, register a standing query.
- **World**: Multiple objects, some with `lot=L + micro_defect` attributes. Time dilation generates many episodes.
- **Business**: MES lot records, supplier register.
- **inject**: Deterministically concentrate defects in lot L and scatter observations across time and instances.
- **MWS query**: QualityAgent → `indices={semantic,temporal,symbolic,structured}, projection=aggregate`; consolidation clusters → correlates.
- **Success criteria / metrics**: Pattern discovery (cluster purity / recall for lot-L), consolidation compression ratio ↔ recall retention, accuracy of standing-query registration.
- **Acceptance test**: With a deterministic seed, consolidation surfaces lot L / QualityAgent registers a standing query monitoring L.
- **CLI**: `scenario run --name collective_weak_signal`.

### Scenario 4 | Instant rampup of a new SKU / new site (transfer)

- **Goal**: Faster rampup; amortize a single demonstration across the swarm.
- **Hypotheses / mechanisms**: H1 / retrieval-augmented VLA & skill propagation ◎, multi-instance ◎, federation, product-master linkage.
- **Actor responsibilities**:
  - InstanceA: Turn a new SKU into skill/map atoms via one teleoperated demonstration or exploration.
  - SwarmB: Recall and reuse it. The product master drives the destination.
- **World**: A new SKU class unknown to every robot; an unfamiliar site map.
- **Business**: Product master (SKU → destination).
- **inject**: A new class. **Toggle the presence of the demo atom** (the independent variable of the A/B comparison).
- **MWS query**: SwarmB → `indices={semantic,symbolic}+procedural(skill), filters={sku_class}, projection=pose+tensor`.
- **Success criteria / metrics**: Transfer gain (success-rate difference with vs. without the demo), cold-start time, sample efficiency.
- **Acceptance test**: In the A/B over demo-atom presence, the swarm with shared memory significantly improves its success rate.
- **CLI**: `scenario run --name new_sku_rampup`.

### Scenario 5 | Real-time coordination for incident response

- **Goal**: Safety, response speed, compliance.
- **Hypotheses / mechanisms**: H6, H2 / standing query ◎, active curiosity ◎, multi-instance coordination ◎, cross-source (SDS+floor plan+roster) ◎, freshness, provenance.
- **Actor responsibilities**:
  - AnyRobot: Detect a hazard event (substance X leak / fall / failure).
  - IncidentAgent (ADK): Fused recall of SDS/SOP + floor plan/nearest exit + on-call roster → notify people + dispatch the nearest robot. Occluded areas are scouted via the curiosity loop before finalizing the plan.
  - Real-time 4D scene graph: Holds who is where.
- **World**: A "leaking" object tagged with a substance, multiple rooms with exits, an occluded area of unknown state.
- **Business / documents**: SDS, safety SOP, building floor plan, on-call roster.
- **inject**: A hazard event + an occluded area + nearest-robot selection.
- **MWS queries**:
  - Standing query: "Fire when any instance observes a leak."
  - IncidentAgent → `indices={semantic,spatial,structured}, filters={substance_id,region}, projection=text+provenance, qor={freshness=strict,max_latency=low}`.
  - Curiosity: Occlusion → too few hits → issue an exploration task → close the gap with new observations.
- **Success criteria / metrics**: Response latency, recall accuracy of SDS/exit/roster, the curiosity loop's reduction of plan-failure rate (H6), coordination.
- **Acceptance test**: On hazard injection the standing query fires / on a gap a scout robot is dispatched / the plan uses SDS + exit + roster.
- **CLI**: `scenario run --name incident_response`.

### Scenario 6 | Counterfactual-based on-site safety decision

- **Goal**: Reduce risk, avoid accidents/damage, justify actions.
- **Hypotheses / mechanisms**: H6 / counterfactual & sim retrieval ◎, retrieval-augmented decision-making ◎, cross-source (SOP limits + specs), safety.
- **Actor responsibilities**:
  - SafetyAgent / VLA: Just before a dangerous operation, query MWS for a counterfactual rollout and choose a safe action.
  - Sim backend: Fork the digital twin and roll out, generating a result atom.
- **World**: A dangerous configuration (high-stacked cargo / a pressure valve, etc.). MuJoCo's state forking doubles as the counterfactual engine.
- **Business / documents**: SOP risk limits, equipment specs.
- **inject**: A dangerous configuration + the fork/rollout mechanism.
- **MWS query**: VLA → `intent="is this operation safe", indices={semantic,structured}+counterfactual(sim), projection=numeric+provenance`. Recall the result atom (collapses / does not collapse).
- **Success criteria / metrics**: Safety outcome (collapse-avoidance rate), decision quality, rollout cost, the counterfactual atom is recalled and used.
- **Acceptance test**: With a deterministic fork, the rollout avoids the operation it predicts will collapse / the result atom is stored and recallable.
- **CLI**: `scenario run --name counterfactual_safety`.

### Scenario 7 | End-to-end order to fulfillment

- **Goal**: Order accuracy, fewer ghost-inventory failures, automation.
- **Hypotheses / mechanisms**: H4, H8 / cross-system orchestration ◎, physical-vs-digital consistency & freshness ◎, consumer-aware projection ○, multi-instance ○, external write-back ◎.
- **Actor responsibilities**:
  - OrderAgent (ADK): Orchestrate the order. Confirm physical inventory via robot observation, not just WMS.
  - PickingVLA / TransportRobot: Pick and transport.
  - External: orders, WMS, procurement, ERP (stubs).
- **World**: Shelves stocked with SKUs, the target order, **one damaged item injected**.
- **Business**: Orders, WMS (ghost inventory: recorded but physically missing/damaged), procurement, ERP.
- **inject**: An order event + ghost inventory/damaged item.
- **MWS query**: OrderAgent → `indices={structured,spatial,semantic,temporal}, filters={sku,entity_id}, projection=text+provenance/numeric, qor={freshness=strict}`; physical observation overrides WMS.
- **Success criteria / metrics**: Order accuracy, reduction of ghost-inventory-caused failures, E2E success rate, accuracy of procurement/notification/ERP update.
- **Acceptance test**: On damaged-item injection, physical confirmation overrides WMS / procurement reorder + customer notification + ERP close are executed.
- **CLI**: `scenario run --name order_to_fulfillment`.

---

## 4. Traceability Matrix

| Scenario | Main hypotheses | Main mechanisms | Scenario-specific metrics |
|---|---|---|---|
| 1 Maintenance handoff | H1,H3,H8 | fused retrieval / retrieval-augmented VLA / handoff / audit | cross-embodiment skill transfer succeeds, audit completeness |
| 2 Record reconciliation | H4,H9 | physical↔record / multi-observer fusion / write-back | adjudication accuracy, fused error < single, stale rate |
| 3 Weak signal | H5 | consolidation / correlation / standing query | cluster purity, compression ↔ recall retention |
| 4 Rampup transfer | H1 | skill propagation / multi-instance / federation | transfer gain, cold-start time |
| 5 Incident | H6,H2 | standing query / curiosity / coordination | response latency, lower plan-failure rate |
| 6 Counterfactual safety | H6 | counterfactual sim retrieval / safety | collapse-avoidance rate, rollout cost |
| 7 Order-to-fulfillment E2E | H4,H8 | cross-system / physical↔digital | fewer ghost-inventory failures, E2E success rate |

---

## 5. Implementation Order and Acceptance Gates

Align with the roadmap in `PROJECT.md`.

1. **Common framework** (§1–§2) — BaseScenario, registration, auto relevance-label generation, audit, mock path. Gate: an empty scenario completes and leaves a manifest.
2. **Scenario 1 (the vertical slice)** — Pierce all layers once. Gate: end-to-end succeeds under mock + audit complete + metrics emitted.
3. **Scenarios 2 & 3** — Novelty (reconciliation, weak signal). Gate: each acceptance test green.
4. **Scenario 4** — Transfer (doubles as the Phase 2 experiment). Gate: transfer gain measured by A/B.
5. **Scenario 6** — Sim-native, demo-friendly. Gate: counterfactual avoidance demonstrated.
6. **Scenario 5** — Reactivity + curiosity coordination. Gate: fire, scout, fused plan.
7. **Scenario 7** — Cross-system E2E. Gate: physical override + external integration completed.

A scenario is considered done only when it satisfies the DoD in `CLAUDE.md` §15 (ruff/type/pytest green, golden test, seed & manifest, adherence to the cloud boundary / dependency direction / embedding space).

---

## 6. Common Per-Scenario Acceptance Checklist

- [ ] `scenario run --name <key> --seed 0` completes under mock with no key
- [ ] Deterministic under the same seed (same result on re-run)
- [ ] Outputs manifest, metrics, and audit.jsonl to `runs/<RUN_ID>/`
- [ ] Computes retrieval metrics (Recall@k, etc.) with ground-truth-derived relevance labels
- [ ] Has assertions that meet the scenario-specific success criteria
- [ ] Cloud calls only in embedding/agents; no cloud embedding on the hot path
- [ ] Adheres to the dependency direction and embedding-space discipline
