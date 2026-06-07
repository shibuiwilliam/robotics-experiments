# SCENARIOS.md — PSL-Bench Verification Scenario Specification

> This document is the **implementation specification for business use cases and scenarios** used to verify PSL.
> It is a subordinate document to `PROJECT.md` (Why/What, RQ/H, 10 mechanisms, evaluation metrics, phases) and `CLAUDE.md` (How to build, invariants, conventions); in case of conflict, those documents take precedence.
> Each scenario is designed around a "**breaking point**" of translation. The value of PSL only manifests at the moment naive translation breaks, so each scenario is composed non-redundantly to primarily stress different mechanisms and RQ/H.
> All scenarios include three flows (R2R / A2R / A2A) and document physical anchoring, and are constrained to what is reproducible in MuJoCo.

- **Version**: 0.1.0 (draft)
- **Target implementer**: Claude Code
- **Related**: `PROJECT.md` §5.2 (10 mechanisms), §6 (testbed), §8 (evaluation metrics), §10 (phases) / `CLAUDE.md` §4 (layout), §16–17 (recipes)

---

## 0. Common Specification (Contract Shared by All Scenarios)

### 0.1 What Each Scenario Must Include

- **3 flows**: R2R (robot-to-robot), A2R (agent-to/from-robot), A2A (agent-to-agent).
- **Document anchoring**: Resolve business documents/data (pseudo-cloud) to physical frames (forward direction). Reverse direction (physical to document reference) is specified explicitly in applicable scenarios.
- **Breaking point**: The point where naive translation breaks. The core of each scenario.
- **Ground truth isolation compliance**: Success determination and metric computation read MuJoCo ground truth only from `eval/`. Scenario implementation (`src/psl`, `agents`, task logic in `sim`) does not reference ground truth (`CLAUDE.md` §1-3).

### 0.2 Standard Structure for Scenario Implementation (Directories)

Each scenario `sN_<slug>` has the following (aligned with `CLAUDE.md` §4):

```
sim/scenes/sN_<slug>/            # MuJoCo scene (mjcf/xml), initial layout, asset retrieval scripts
experiments/scenarios/sN_<slug>.yaml   # config-as-code (seed, heterogeneity, agent settings, thresholds)
src/psl/...                      # Required mechanism implementations (shared; scenario-specific logic is minimal)
agents/topology/sN_<slug>/       # supervisor/worker configuration (when needed)
pseudo_cloud/sN_<slug>/          # Business data for that scenario (SQLite/JSON)
eval/scenarios/sN_<slug>/        # Success conditions, metric collection, breaking point assertions (ground truth read here only)
tests/scenarios/sN_<slug>/       # oracle + metamorphic tests
docs/scenarios/sN_<slug>.md      # Detailed supplementary notes if needed
```

### 0.3 Scenario Definition Schema (config-as-code)

`experiments/scenarios/sN_<slug>.yaml` must declare at minimum the following:

```yaml
id: sN_<slug>
seed: 12345
robots:            # Participating robots and the Schema Generator dose applied to each
  - model: <menagerie_model>
    schema_dose: { units: mm, frame: y_up, naming: remap_a, control: velocity, noise: 0.01 }
documents:         # Business data from pseudo-cloud (reference only)
  sources: [workorders, sop, inventory]
agents:            # Claude Agent SDK settings (temperature, model, permissions are fixed)
  supervisor: { model: <model>, temperature: 0.0 }
  workers: [...]
breakpoints:       # Breaking points intentionally triggered in this scenario (may be multiple)
  - type: lossy_abstraction_uncertainty
thresholds:        # Success conditions and tolerance thresholds (pass/fail determined here)
  task_success: 0.9
  calibration_nll_max: <value>
  commutativity_divergence_max: <value>
metrics:           # Metrics from PROJECT.md §8 to collect
  - translation_fidelity
  - uncertainty_calibration
sweep:             # Dose-response sweep axis (optional)
  axis: schema_dose.units
  values: [m, cm, mm]
```

### 0.4 Two-Layer Success Criteria

- **Task success**: Achievement of the scenario-specific business goal (e.g., the correct item is placed in the QA tray).
- **PSL success**: PSL metrics corresponding to the breaking point meet their thresholds (e.g., calibration NLL <= threshold, commutativity divergence <= threshold).

Only when both are satisfied is the scenario considered "passed." Task success alone is insufficient (naive translation can succeed by chance).

### 0.5 Dose-Response

All scenarios must be sweepable along Schema Generator dose axes (units, frame, naming, control mode, noise), generating **degradation curves of success rate against increasing heterogeneity** (`PROJECT.md` §6.4, §8). Baselines B0/B1/B2 and PSL are compared in the same sweep.

---

## 1. Scenario List and Coverage Matrix

| # | slug | Title | Primary breaking point | Primary RQ/H verified | Primary mechanisms used | Recommended phase | Feasibility |
|---|---|---|---|---|---|---|---|
| 1 | `s1_mixed_fleet_pick` | Mixed-fleet picking with defect exception handling | Lossy abstraction x calibration, R2R frame/unit mismatch | RQ1, RQ2/H2, RQ5/H4 | Phyte, IR, contracts, world_model, negotiation | 0–3 (vertical slice) | Easy (**starting point**) |
| 2 | `s2_line_changeover` | Manufacturing line changeover (recipe switch) | Anchoring x safety gate x commutativity | RQ3/H3, RQ7 | Anchoring, safety, IR (commutativity) | 2–3 | Medium |
| 3 | `s3_lab_custody` | Lab automation and specimen chain of custody | Provenance x uncertainty as business requirement | RQ1, ablation | Phyte (provenance), contracts | 1–3 | Easy to medium |
| 4 | `s4_field_inspection` | Field asset inspection (aerial + ground + contact) | Multi-frame fusion x bidirectional anchoring x LOD | RQ1, LOD | LOD, anchoring (bidirectional), fusion | 3 | Medium |
| 5 | `s5_pharma_logistics` | Hospital pharmaceutical and controlled substance logistics | Neuro-symbolic binding x false/malicious provenance resistance x safety | RQ7, resistance | safety, neuro-symbolic, provenance | 4 | Medium |
| 6 | `s6_ewaste_disassembly` | Open-world recycling/e-waste disassembly | Affordance grounding x embedding generalization (open world) | RQ6/H5 | grounding (embedding), affordance, negotiation | 4–5 | Medium to high |
| 7 | `s7_degraded_ops` | Operations under time constraints and degraded communications | Clock domain causality x graceful degradation | Causal consistency (Phase 4) | L1 time, LOD, contracts (degradation) | 4 | Easy |

**Non-redundancy check**: The "primary breaking point" in each row is unique. The 7 scenarios nearly cover all 10 mechanisms from `PROJECT.md`, and in particular share responsibility for covering the "core outcomes measurable only because ground truth exists": calibration, contract accuracy, commutativity, and causal consistency.

**Implementation order guideline**: Scenario 1 serves as the vertical slice for Phases 0–3 (subsuming cross-cutting task #42). In Phase 4, add Scenario 7 (clock skew) and Scenario 5 (resistance); Scenario 6 comes later for generalization verification. Scenarios 2, 3, and 4 are inserted in between.

---

## 2. Scenario 1: Mixed-Fleet Picking with Defect Exception Handling `s1_mixed_fleet_pick`

### 2.1 Business Story
A business agent with WMS/ERP receives a work order: "Retrieve the blue gear from Bin C and place it in the QA tray. If defective, isolate and raise an exception." A 7-DOF arm (joint position, m, z-up) grasps the item and hands it off to an AMR (velocity control, different frame, different naming convention), which transports it to the QA station. If the target is damaged or ambiguous, the flow branches to exception handling.

### 2.2 Flow Mapping
- **Document to physical**: Resolve "Bin C" and "blue gear" to physical frames/entities in the world model.
- **A2R**: Agent intent to arm grasp action; observation to semantic abstraction ("possible defect").
- **R2R**: Arm-to-AMR handoff (differing frame, units, naming, and control mode).
- **A2A**: Perception worker to decision worker to ERP write-back worker.

### 2.3 Breaking Point
The VLA produces an **uncertain semantic quantity** such as "defect 0.6." This uncertainty must **propagate without attenuation or distortion** through fidelity contracts from A2R to A2A, so the agent can make threshold decisions (reroute/isolate). Naive translation collapses uncertainty into a point estimate, producing incorrect decisions. In R2R, the handoff with mm/different frame/different naming can cause pose translation to break.

### 2.4 MuJoCo Configuration
- Arm (e.g., 7-DOF, position control, m, z-up) + AMR (mobile base, velocity control, different frame) + bin cluster + QA tray + isolation tray.
- Gears have color and a small damage flag (mesh/texture difference); ambiguous specimens are mixed in.
- Schema Generator applies mm, y_up, naming remap, and velocity control to the AMR.

### 2.5 Pseudo-Cloud
`workorders` (#42 and its exception rules), `inventory` (bin-to-shelf-location mapping), `sop` (isolation procedure for defects).

### 2.6 Success Criteria
- **Task success**: Normal items go to the QA tray; ambiguous/defective items go to the isolation tray. An exception record is created in the ERP.
- **PSL success**: Translation fidelity (round-trip SE(3) error) <= threshold, **uncertainty calibration (NLL/ECE) <= threshold**, R2R handoff pose error <= threshold.

### 2.7 Breaking Point Assertions (eval)
- Calibration ablation: Removing covariance from Phyte must cause a significant drop in isolation decision precision (proof of mechanism contribution).
- Adding a second arm (mm, different naming) at runtime: semantic negotiation enables cooperation, and integration cost does not become N x N (RQ5/H4).

### 2.8 Tests
- oracle: Round-trip error of grasp pose, handoff pose, calibration.
- metamorphic: Frame equivariance, unit invariance (semantic invariance under AMR's mm conversion), object substitution invariance.

### 2.9 Dose-Response Axes
AMR units (m to cm to mm), frame rotation angle, noise intensity. Degradation curves of PSL vs B0/B2.

---

## 3. Scenario 2: Manufacturing Line Changeover (Recipe Switch) `s2_line_changeover`

### 3.1 Business Story
An MES agent injects a new product recipe (business document) and reconfigures the settings (fixture positions, target poses, torques) of multiple robots with different control modes.

### 3.2 Flow Mapping
- **Document to physical**: Resolve the recipe's symbolic specifications (part names, tolerances, torques) to each robot's physical parameters.
- **A2R**: Distribution of configuration commands. **R2R**: Inter-robot alignment (shared fixtures, handoff positions). **A2A**: MES agent to quality agent.

### 3.3 Breaking Point
(a) The recipe requests a **physically impossible reconfiguration** (exceeding joint limits/torque limits, interference) and the physical consistency gate rejects it (RQ7). (b) The **multi-hop translations** "recipe to IR to robot A" and "recipe to IR to robot B" must not contradict each other -- commutativity diagram divergence <= threshold (RQ3/H3).

### 3.4 MuJoCo Configuration
2-3 robots with different control modes (position/velocity/torque), shared fixtures, layout where interference can occur. Targets change with different recipes.

### 3.5 Pseudo-Cloud
`recipes` (per-product configuration, tolerances, torques), `equipment` (limit values for each robot).

### 3.6 Success Criteria
- Task success: With a valid recipe, all robots are correctly reconfigured; with an impossible recipe, it is safely rejected and the MES is notified.
- PSL success: **Commutativity divergence <= threshold**, consistency gate blocks 100% of impossible configurations with false rejection rate <= threshold.

### 3.7 Tests
- oracle: Pose/torque error after reconfiguration.
- metamorphic: Composability (agreement between route-A and route-B translations), frame equivariance.
- safety: Impossible recipe set (limit exceedance, interference) must always be rejected.

### 3.8 Dose-Response Axes
Increasing convention differences between robots (frame/control mode) vs. commutativity divergence.

---

## 4. Scenario 3: Lab Automation and Specimen Chain of Custody `s3_lab_custody`

### 4.1 Business Story
An SOP protocol document is grounded into physical actions of dispensing and transport; a liquid handler arm, transport robot, and reader cooperate. The specimen chain of custody must never be broken.

### 4.2 Breaking Point
**Provenance (origin and confidence) is simultaneously an internal PSL mechanism and a business deliverable required by regulation.** The symbolic assertion of a specimen ID must always be backed by unbroken physical provenance. If confidence falls below a threshold, automatic isolation is triggered.

### 4.3 Flow Mapping
Forward anchoring (SOP to physical actions), A2R (dispensing commands), R2R (arm to transport), A2A (execution agent to compliance agent).

### 4.4 MuJoCo Configuration
Liquid-handler-style arm (precision placement) + transport robot + plates/racks + reader positions. Specimens are bound to IDs and physical positions via Phyte.

### 4.5 Pseudo-Cloud
`sop` (protocol procedures), `samples` (specimen registry), `custody_log` (generated chain records).

### 4.6 Success Criteria
- Task success: Chain records are generated without interruption at every step; isolation is triggered when confidence drops.
- PSL success: Each Phyte maintains valid provenance and confidence. **Provenance ablation** demonstrates that the chain breaks down when the mechanism is removed (mechanism contribution).

### 4.7 Tests
- oracle: Placement accuracy, round-trip consistency of specimen-to-physical-position correspondence.
- metamorphic: Object substitution invariance, temporal equivariance (order preservation).
- ablation: Removing the provenance field from Phyte causes custody establishment rate to collapse.

---

## 5. Scenario 4: Field Asset Inspection `s4_field_inspection`

### 5.1 Business Story
An overhead sensor (approximating a "drone" via an overhead camera/elevated viewpoint) performs a wide-area survey, a ground robot approaches, and a contact arm inspects. The VLA describes "corrosion on flange" which is resolved to an asset ID and a work order is generated (forward direction). Simultaneously, tightening torque from a manual is grounded to the physical part (reverse direction).

### 5.2 Breaking Point
**Fusion of multi-resolution streams** with significantly different coordinate systems, units, and sensor characteristics, combined with **bidirectional anchoring**. Overhead view is low resolution (coarse LOD); contact is high resolution.

### 5.3 Flow Mapping
A2R (inspection commands), R2R (handoff from overhead to ground to arm, multi-frame fusion), A2A (inspection agent to maintenance agent), bidirectional anchoring.

### 5.4 MuJoCo Configuration
Mobile base + contact arm + inspection target assets (part meshes such as flanges) + overhead camera viewpoint. Corrosion is represented via texture/flags.

### 5.5 Pseudo-Cloud
`assets` (asset ID to physical mapping), `manuals` (per-part specifications and torques), `workorders` (generation target).

### 5.6 Success Criteria
- Task success: Anomalous locations are correctly resolved to asset IDs and work orders are generated; manual values are correctly grounded to physical parts.
- PSL success: Translation fidelity of post-fusion pose/identification <= threshold; LOD-specific subscriptions meet requested resolution.

### 5.7 Tests
- oracle: Fusion identification error, bidirectional anchoring correspondence accuracy.
- metamorphic: Frame equivariance (overhead to/from ground), LOD consistency (same entity regardless of resolution change).

---

## 6. Scenario 5: Hospital Pharmaceutical and Controlled Substance Logistics `s5_pharma_logistics`

### 6.1 Business Story
A business agent enforces regulations (regulatory classification, expiration date, dosage) while the physical consistency gate enforces physical safety. An arm, transport robot, and storage handle pharmaceuticals.

### 6.2 Breaking Point
When the **symbolic assertion "this vial = drug X" and physical/visual observations disagree**. Safety-critical decisions must not be delegated solely to learned grounding; **binding with symbolic verification (neuro-symbolic)** is required. Resistance to false/malicious provenance injection is also tested (Phase 4).

### 6.3 Flow Mapping
Forward anchoring (prescription/regulation to physical actions), A2R, R2R (handoff), A2A (pharmaceutical agent to regulatory audit agent).

### 6.4 MuJoCo Configuration
Arm + transport + storage + vials/containers (ID and appearance are separable). Cases where observations and assertions intentionally disagree are injected.

### 6.5 Pseudo-Cloud
`formulary` (drugs, regulatory classification, dosage), `expiry` (expiration dates), `audit_log`.

### 6.6 Success Criteria
- Task success: Regulatory violations, expired items, and assertion mismatches are always detected and result in stop/isolation; audit log is generated.
- PSL success: Neuro-symbolic binding produces **significantly lower false acceptance than learned grounding alone**. The system fails safe even under contaminated provenance.

### 6.7 Tests
- safety: 100% stop rate for mismatch/violation cases, false stop rate <= threshold.
- robustness: False acceptance rate under provenance contamination injection.
- ablation: Removing neuro-symbolic binding worsens false acceptance.

---

## 7. Scenario 6: Open-World Recycling/E-Waste Disassembly `s6_ewaste_disassembly`

### 7.1 Business Story
Heterogeneous tools/arms use a VLA to infer affordances (graspable/removable points) on **unknown items** and disassemble them. A business agent connects to material recovery value and compliance manifests.

### 7.2 Breaking Point
When **symbolic ontology breaks down** for unknown objects, and whether embedding/affordance grounding can generalize (open world). Runtime negotiation of capability descriptors also arises.

### 7.3 Flow Mapping
A2R (disassembly commands), R2R (tool switching, multi-arm coordination), A2A (disassembly agent to material/compliance agent), anchoring (item to manifest).

### 7.4 MuJoCo Configuration
Diverse object set (known + **holdout novel objects**), multiple arms/tools. Affordance annotations are held as ground truth on the `eval/` side (implementation side does not reference them).

### 7.5 Pseudo-Cloud
`materials` (material value), `compliance` (disposal classification), `manifest` (generated).

### 7.6 Success Criteria
- Task success: Unknown objects are disassembled and materials are correctly sorted; manifest is generated.
- PSL success: **Success rate on holdout novel objects is significantly higher with embedding grounding than with symbolic-only (RQ6/H5)**.

### 7.7 Tests
- oracle: Geometric accuracy of affordance points (ground truth comparison).
- generalization: Success rate difference between training/holdout splits.
- metamorphic: Object substitution invariance, frame equivariance.

---

## 8. Scenario 7: Operations Under Time Constraints and Degraded Communications `s7_degraded_ops`

### 8.1 Business Story
Multiple robots coordinate under time pressure and degraded communications (delay/jitter/clock skew injection). Agents reference floor plans and manifests to respond.

### 8.2 Breaking Point
**Causal ordering of observations is preserved under inter-stream clock skew**, and as degraded communications force coarser LOD and lossier translation, fidelity contracts degrade **gracefully**. Even on a single Mac, this can be verified by simply injecting delay into one stream (`PROJECT.md` Phase 4).

### 8.3 Flow Mapping
A2R/R2R/A2A are all included; delay, jitter, and skew can be injected into each link.

### 8.4 MuJoCo Configuration
Reuses an existing scenario's configuration (e.g., s1) and adds delay/jitter/clock skew injection hooks to the transport layer.

### 8.5 Pseudo-Cloud
`floorplan`, `manifest`. Degradation injection parameters are controlled via config.

### 8.6 Success Criteria
- Task success: Causal consistency is maintained under skew/delay, and the task is completed (while tolerating degradation).
- PSL success: **Zero causal order violations**; LOD/contracts become monotonically coarser in proportion to degradation (graceful degradation rather than discontinuous failure).

### 8.7 Tests
- causal: Causal ordering is preserved under injected skew (violation detection).
- degradation: Monotonic and continuous decline curve of fidelity against degradation amount.
- metamorphic: Temporal equivariance (consistency under time shifts).

---

## 9. Implementation Checklist (Definition of Done Common to All Scenarios)

In addition to `CLAUDE.md` §15, the following scenario-specific items must be satisfied:

- [ ] `experiments/scenarios/sN_<slug>.yaml` is defined per the §0.3 schema (seed, heterogeneity, agent settings, thresholds, metrics, sweep axes).
- [ ] All 3 flows (R2R/A2R/A2A) + document anchoring fire.
- [ ] The breaking point **actually occurs**, and PSL handles it (tests that do not trigger the breaking point are invalid).
- [ ] **Both task success and PSL success** are measured and pass/fail is determined for both (§0.4).
- [ ] Baselines B0/B2 are run with the same config and compared via dose-response (§0.5).
- [ ] Oracle + metamorphic tests are added (ground truth is read only from `eval/`).
- [ ] Contribution to the applicable RQ/H is recorded in the run manifest.
- [ ] Agent invocation costs are logged, and estimates are produced before sweeps.

---

## 10. Related Documents
- `PROJECT.md` — Overall project definition (RQ/H, 10 mechanisms, evaluation metrics, phases, success criteria).
- `CLAUDE.md` — Development operations (invariants, layout, commands, conventions, recipes, DoD).
- `docs/scenarios/` — Detailed supplementary notes for each scenario (when needed).
