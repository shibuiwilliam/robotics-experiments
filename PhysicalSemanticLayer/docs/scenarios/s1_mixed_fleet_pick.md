# Scenario 1: Mixed Fleet Picking with Defect Exception Handling

## Business Story

A WMS/ERP business agent receives Work Order #42: "Retrieve the defective blue gear from
Bin C, place it in the QA tray. If the item is ambiguous or damaged, isolate it and file
an exception." A 7-DOF Panda arm (position control, metres, z-up) grasps the gear and
hands it off to an AMR mobile base (velocity control, millimetres, y-up, different naming
convention). The AMR transports the gear to the QA station. If the VLA perception system
reports uncertain defect confidence, the routing changes to an isolation tray and an
exception record is created in the ERP.

## Why This Scenario Matters

This is the **vertical slice** — the single scenario that exercises every layer of PSL
from Phase 0 through Phase 3. It contains the canonical cross-cutting task from
PROJECT.md §6.5 and touches the most core mechanisms of any scenario. If s1 works, the
foundation is sound.

The breaking point is twofold:
1. **Uncertainty propagation (A2R → A2A):** The VLA encoder outputs "defect 0.6" with
   σ=0.13. This uncertainty must propagate through the Phyte → fidelity contract →
   agent decision chain without being crushed to a point estimate. A naive translator
   would drop the covariance, giving the agent false certainty.
2. **Cross-adapter R2R mismatch:** The Panda speaks metres/z-up/position; the AMR
   speaks millimetres/y-up/velocity. A direct adapter-to-adapter translation would
   require N×N pairs. PSL forces all translation through the Canonical IR (N+N).

## What Actually Runs

| Component | What happens |
|-----------|-------------|
| **MuJoCo** | Loads `sim/scenes/s1_mixed_fleet_pick/scene.xml` (Panda + AMR + 3 bins + QA tray + isolation tray + blue gear). Task controller drives the arm through 5 waypoints: home → approach Bin C → grasp pose → retract → handoff position. |
| **Cross-adapter R2R** | Panda sensor readings → `PandaAdapter.to_ir()` → safety-gated write to world model → world model stores gear position as Phyte → `AMRAdapter.from_ir()` translates to mm/y-up/velocity. Round-trip error measured. |
| **Pseudo-cloud HTTP** | Agent calls `resolve_document_to_physical(work_order, "WO-42")` → in-process HTTP GET to `/api/v1/workorders/WO-42` and `/api/v1/bins/C` → returns Bin C position [0.3, 0.3, 0.45] in world frame. |
| **VLA encoder** | Hash-based encoder creates 512-dim embedding Phyte for `blue_gear` with provenance tracking the encoder source. Affordance prediction: graspable=True. |
| **Claude Agent SDK** | Supervisor receives task prompt, delegates to worker. Worker calls 13 MCP tools: 6× `resolve_document_to_physical`, 1× `query_world_model`, 1× `subscribe_affordances`, 4× `command_robot_semantic`, 1× `Agent` delegation. Cost: $0.265, 2 turns, 69.3s. |
| **Semantic negotiation** | Panda (7-DOF, position, m, z-up) and AMR (0-DOF, velocity, mm, y-up) capabilities are registered. Negotiator confirms feasibility with 4 translation notes (frame mismatch, unit mismatch, control mode, no shared modalities). |
| **Baselines** | B0/B1/B2 run under identical 1000× unit scaling + noise. B1 (raw blackboard) RMSE = 576.3 (catastrophic failure). PSL RMSE = 1.08e-05. |

## Three-Flow Coverage

| Flow | How it's exercised |
|------|-------------------|
| **R2R** | Panda → IR → WorldModel → IR → AMR. Different units (m vs mm), frames (z-up vs y-up), and control modes (position vs velocity). |
| **A2R** | Agent issues `command_robot_semantic("panda_arm", "move_to", ...)` via MCP tool. Robot state is read via `query_world_model("panda_arm")`. |
| **A2A** | Supervisor delegates to worker agent via `Agent` tool. Worker reports back defect assessment and routing decision. |

## Breakpoints and Metrics

| Breakpoint | Value | Threshold | Result |
|-----------|-------|-----------|--------|
| Calibration NLL | -0.745 | ≤ 5.0 | **PASS** |
| R2R handoff error | 0.000 m | ≤ 0.1 m | **PASS** |
| Negotiation feasible | 1.0 | ≥ 0.5 | **PASS** |

| Metric | Value |
|--------|-------|
| Joint position RMSE (round-trip) | 0.0 |
| Defect confidence | 0.640 (σ=0.132) |
| Commutativity divergence | 0.0 |
| B1 baseline RMSE | 576.3 |
| Agent tool calls | 13 |
| Agent cost | $0.265 |

## RQ/Hypothesis Contributions

- **RQ1 (Fidelity):** Round-trip RMSE = 0.0 — lossless translation through the IR.
- **RQ2 / H2 (Calibration):** NLL = -0.745 — declared uncertainty matches actual error.
- **RQ5 / H4 (Scale):** Semantic negotiation connected two heterogeneous robots without
  modifying either adapter. Adding a new robot = 1 adapter, not N adapters.

## Files

| Purpose | Path |
|---------|------|
| MuJoCo scene | `sim/scenes/s1_mixed_fleet_pick/scene.xml` |
| Eval module | `eval/scenarios/s1_mixed_fleet_pick/eval.py` |
| Config | `experiments/scenarios/s1_mixed_fleet_pick.yaml` |
| Pseudo-cloud | `pseudo_cloud/data.py` (WO-42, bins, inventory) |
| Tests | `tests/scenarios/test_all_scenarios.py::TestS1MixedFleetPick` |
