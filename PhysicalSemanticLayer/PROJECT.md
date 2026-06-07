# PROJECT.md — Physical Semantic Layer Validation Research Project

> This document is the **single source of truth** that defines the entire project.
> It defines "Why we build it (Why)," "What we build (What)," and "How we measure success (How measured)."
> **Development procedures, coding conventions, execution commands, and other "How to build" concerns are separated into `CLAUDE.md`.**
> If this document and `CLAUDE.md` conflict, this document takes precedence for conceptual definitions, while `CLAUDE.md` takes precedence for development operations.

- **Project codename**: PSL-Bench
- **Version**: 0.1.0 (draft)
- **Status**: Pre-Phase 0
- **Owner**: (TBD)
- **Last updated**: (TBD)

---

## 1. Executive Summary

This project aims to take the **Physical Semantic Layer (PSL)** — an intermediary layer that mutually translates data of fundamentally different natures in systems integrating robotics, AI agents, and VLAs — and **reduce it to a fully locally verifiable research subject using only MuJoCo (physics simulator) on a MacBook and the Claude Agent SDK (cloud-side agent)**.

The central idea is to maximally exploit the fact that **MuJoCo serves simultaneously as the "physical world" and as a supplier of "complete ground truth."** This transforms PSL from "an engineering artifact that merely works" into "a falsifiable scientific object." Because ground truth is available, we can quantitatively measure how well a translation preserves meaning, whether declared uncertainties are correct, and whether translations via multiple paths agree (composability). This is a decisive advantage unique to this configuration, unattainable with real robots or standalone cloud environments.

---

## 2. Background and Problem Definition

### 2.1 Structure of Integrated Systems

The target is a system that coordinates multiple heterogeneous robotics platforms x multiple AI agents x VLAs. Data sharing occurs via three pathways:

- **R2R (Robot <-> Robot)**: Primarily a physical problem. Mismatches in coordinate frames, units, naming, control modes, and sensor configurations.
- **A2R (Agent <-> Robot)**: The primary battlefield for VLAs. Symbolic intent -> grounded action, physical observation -> semantic abstraction.
- **A2A (Agent <-> Agent)**: Primarily a symbolic problem. However, the key is that all assertions are grounded in spacetime.

Data on the robotics side consists of "physical observations of the robot itself and its surroundings" — high-frequency, continuous, SI-unit, coordinate-frame-dependent, and noisy. Data on the agent side consists of "data obtained from robots plus cloud-side business data and documents" — low-frequency, discrete, symbolic, and business-ontology-dependent.

### 2.2 The Essential Challenge

The core of the problem is not mere schema conversion but **impedance matching between two fundamentally different worlds (physical side = measurements, symbolic side = concepts)**. From this, two design insights emerge that form the foundation of this project:

- **Insight A — Spacetime is a universal join key**: Every physical event possesses `(reference frame, SE(3) pose, timestamp)`. These can serve as a primary key to JOIN heterogeneous data. The essence of PSL is a "semantic JOIN engine with spacetime as the primary key."
- **Insight B — Dual grounding is essential**: Typical data/semantic layers only have grounding in the data-source direction. PSL must be simultaneously grounded in both the downward direction (physical: coordinates, units, time, uncertainty) and the upward direction (symbolic: business concepts, tasks, documents).

### 2.3 Position on Existing Approaches

Against the naive objection "why not just throw everything at an LLM for conversion," this project takes the position of **demonstrating that PSL is superior in reliability, cost, reproducibility, and verifiability** (explicitly pitted against this as baseline B2. See S9).

---

## 3. Research Propositions, Questions, and Hypotheses

### 3.1 Central Proposition

> **Transform the Physical Semantic Layer from an engineering artifact into a falsifiable scientific object.**
> That is, demonstrate quantitatively that translation between heterogeneous data is not "intuitively smooth" but "**verifiably smooth**."

### 3.2 Research Questions (RQs)

- **RQ1 (Fidelity)**: To what precision can the loss of semantic translation between heterogeneous schemas be measured and predicted against ground truth?
- **RQ2 (Calibration)**: Is the uncertainty that PSL assigns to translations calibrated against the true error distribution?
- **RQ3 (Composability)**: Do results from different translation paths (multi-hop) agree (how large is the commutativity violation)?
- **RQ4 (Robustness)**: At what point does each method break down as heterogeneity increases (dose-response curve)?
- **RQ5 (Scale)**: Is the integration cost of new robots/agents N x N or N+N?
- **RQ6 (Grounding)**: Is grounding via learned embeddings stronger than symbol-only grounding for novel objects and novel concepts?
- **RQ7 (Safety)**: Can the physical consistency gate block translations that produce impossible states, and is the false rejection rate within acceptable bounds?

### 3.3 Testable Hypotheses

- **H1**: As the dose of heterogeneity increases, the success rate of baselines without a semantic layer (B0/B2) degrades, but PSL significantly suppresses the degradation.
- **H2**: The information loss declared in PSL's fidelity contracts is statistically consistent with the measured mutual information loss.
- **H3**: Commutativity violations remain within the defined tolerance thresholds.
- **H4**: The cost of integrating a new robot remains constant-order with respect to N (does not become N x N).
- **H5**: Adding embedding-based grounding significantly improves success rate on held-out novel objects compared to symbol-only grounding.

Each hypothesis is accepted or rejected using the evaluation metrics and statistical procedures in S8.

---

## 4. Scope

### 4.1 In Scope

- Research prototype implementation of PSL's core mechanisms (described below).
- A testbed consisting of heterogeneous robot groups on MuJoCo and a pseudo-cloud (local business data).
- Multi-agent (supervisor + worker) configuration using the Claude Agent SDK, with MCP tool-mediated access to PSL.
- A dual evaluation harness combining oracle verification (ground-truth-based) and metamorphic/property-based verification.
- Baseline and ablation comparisons, dose-response curves, and statistical tests.

### 4.2 Out of Scope

- Verification on real robots (this project is simulation-only; addressed as a validity threat in S11).
- Large-scale VLA training or foundation model pretraining (substituted with frozen small encoders + small adapters).
- Production-grade distributed, highly available world model infrastructure (the research prototype uses a lightweight implementation, while maintaining an abstraction that does not impede future migration to OpenUSD).
- Productization, UI, or operational monitoring.

### 4.3 Constraints and Assumptions

- **Compute environment**: A single MacBook (Apple Silicon assumed). No large-scale GPU training. MuJoCo runs natively on CPU as a baseline.
- **Precise definition of "fully local"**: What runs locally is MuJoCo, PSL, orchestration, pseudo-cloud data, and the evaluation harness. **Only Claude Agent SDK inference makes external calls to the Anthropic API.** Therefore, agent "thinking" has second-order latency. This is both a constraint and a natural experimental condition for the timescale-gap research described later (S6.3).
- **Reproducibility**: Random seed fixing, agent temperature fixing, multi-seed execution, and environment version fixing are mandatory.

---

## 5. System Architecture (Conceptual Definition)

### 5.1 Five-Layer Stack

| Layer | Role | Primary Focus in This Project |
|---|---|---|
| **L0 Transport** | Byte transport and synchronization | Inter-process messaging, simulated time synchronization |
| **L1 Physical Grounding** | Unification of spacetime and units | Distributed TF (coordinate transform tree), clock domains, dimensional analysis |
| **L2 Perception / Entity** | Objects / scenes / affordances | Shared world model, entity resolution |
| **L3 Semantics / Ontology** | Tasks / business concepts | Knowledge graph, capability descriptors, document anchoring |
| **L4 Pragmatics / Intent** | Goals / plans / agent intent | Planner, inter-agent protocol |

**L1 (shared spacetime) is the bedrock of the entire system.** Without shared spacetime, semantic translation cannot hold. This is the decisive difference from pure data/semantic layers.

### 5.2 Core Mechanisms (Components as Research Subjects)

1. **Phyte (Physically-Grounded Semantic Object)**: The canonical data unit of PSL. A self-describing object that always carries `{semantic ID (ontology link), reference frame + SE(3) pose, timestamp + clock domain + temporal uncertainty, units and dimensions, covariance, provenance + confidence}`. Rejects dimension and frame mismatches at contract time.
2. **Canonical Intermediate Representation (Canonical IR)**: Every robot and every agent implements only "native language <-> PSL canonical language." This reduces the N x N combinatorial explosion to N+N (analogous to a compiler IR).
3. **Shared World Model (Blackboard-Style Hub)**: All agents read from and write to a persistent shared digital twin, reducing the three data flows to "dialogue with the world model." The future infrastructure target is OpenUSD; the research prototype uses a lightweight scene graph (preserving USD migration capability).
4. **Typed Lossy Transformation + Fidelity Contract**: Each transformation edge declares "what is preserved / what is lost / how uncertainty increases." This makes end-to-end semantic fidelity of multi-hop translations computable.
5. **VLA Latent Space as Lingua Franca**: Robot observations, agent concepts, and documents are projected into a common latent space and decoded into target schemas. This absorbs the grounding of novel concepts that the symbolic layer cannot handle.
6. **Affordance-Centric Semantics**: Grounded in terms of "graspable / pushable / openable." A hinge between geometry and force (physical) and tasks (symbolic).
7. **Multi-Resolution Access (LOD)**: Subscribe to the same reality at different zoom levels, like map tiles. Absorbs the time-constant difference between control loops and semantic summaries.
8. **Runtime Semantic Negotiation**: Upon joining, participants exchange capability descriptors and align semantic mappings on the fly (semantic handshake).
9. **Physical Consistency Gate**: A safety mechanism that rejects translations producing physically impossible states, such as violations of conservation laws, kinematic limits, teleportation, or causal ordering.
10. **Physical Anchoring of Documents and Business Data (Reverse Grounding)**: Binds manuals to physical asset frames and inventory records to shelf positions, resolving symbolic data to physical references.

### 5.3 Design Principles (Invariants)

- Avoid N x N; achieve N+N via Canonical IR.
- Always carry uncertainty and provenance as first-class citizens.
- Safety-critical quantities are symbolically exact; fuzzy matching is handled by learned components; the two are bound together (neuro-symbolic dual layer).
- Translation composability (functoriality / commutative diagrams) is the criterion of correctness.
- **All A2A messages must be resolvable to a spacetime key** as an invariant (Insight A).

---

## 6. Testbed Definition

### 6.1 Physical Side (MuJoCo) — Intentionally Heterogeneous Robot Group

Select robots with different characteristics from MuJoCo Menagerie and others, **deliberately engineering schema mismatches**. Examples:
- 7-DOF arm (joint position control, meters, z-up)
- Mobile base (velocity control, different frame)
- Second arm (different naming convention, different control mode)

### 6.2 Pseudo-Cloud Side — Local Business Data

Inventory DB, Standard Operating Procedures (SOPs), and work orders simulated with SQLite/JSON. Serves as verification material for document physical anchoring (reverse grounding).

### 6.3 Agent Side (Claude Agent SDK)

Supervisor + worker multi-agent configuration. Each worker accesses PSL via **MCP tool groups (the concrete realization of the A2R standard interface)**. Representative tools: `query_world_model` / `command_robot_semantic` / `subscribe_affordances` / `resolve_document_to_physical`. A2A is implemented using the SDK's sub-agent mechanism.

**Turning the timescale gap to our advantage**: MuJoCo runs at kHz; Claude's decision-making operates at second-order. This speed difference is not an obstacle but a natural experimental condition that necessitates multi-resolution (LOD) design. High-frequency = raw physics within MuJoCo, medium-frequency = PSL canonicalization and world model updates, low-frequency = agent semantic subscription and commands.

### 6.4 Independent Variables — Schema Generator (Controlled Heterogeneity)

Transformations are composably applied to a reference robot, turning heterogeneity into a continuous dial: unit scale (m <-> mm), frame convention (z-up <-> y-up, rotation), name remapping, observation subsetting/augmentation, control mode change (position <-> velocity <-> torque), rate change, sensor noise injection. This enables drawing **dose-response curves**.

### 6.5 Cross-Cutting Task (Representative Scenario Spanning All Three Flows)

> "Work Order #42: Blue gear in Bin C is defective. Retrieve it and place in QA tray."

Document comprehension (pseudo-cloud) -> resolve "Bin C" to a physical frame (document anchoring) -> Robot A grasps (A2R) -> handoff to Robot B with a different schema (R2R) -> Robot B places. A held-out set of novel objects and novel placements is used to prevent PSL overfitting.

---

## 7. Methodology

### 7.1 Dual Verification (The Methodological Originality of This Project)

Treating PSL as a "semantic compiler," we bring in the rigor of software verification.

- **(A) Oracle-Based Verification**: Directly measure error against MuJoCo ground truth (fidelity, calibration, contract accuracy). This is the exclusive domain of environments that possess ground truth.
- **(B) Metamorphic / Property-Based Verification (Label-Free)**: Continuously test invariances and equivariances that translations must satisfy, without requiring ground truth.
  - Frame equivariance: T(g . x) = g . T(x)
  - Unit invariance: semantic invariance under unit rescaling
  - Temporal equivariance: consistency under time shifts
  - Object substitution invariance
  - Composability (functoriality / commutative diagrams)

Combining (A) and (B), we build a **differential testing infrastructure that continuously falsifies translators**, analogous to CI. The systematic application of metamorphic verification to semantic translation is the research novelty.

### 7.2 Self-Improvement Loop

Treating PSL not as a static translator but as a learning system. Task success/failure serves as a supervisory signal for translation correctness, continuously improving VLA latent representations and adapters ("**Claude proposes mappings, ground truth disposes**"). Contract violations and consistency gate rejections serve as hard negative examples.

---

## 8. Evaluation Metrics and Judgment (Dependent Variables)

| Metric | Measurement Method (Leveraging Ground Truth) | Corresponding RQ/H |
|---|---|---|
| **Translation Fidelity** | Round-trip reconstruction error. Pose uses Lie group distance on SE(3); unit and frame correctness rate | RQ1 |
| **Uncertainty Calibration** | Declared covariance vs. actual error from ground truth (regression ECE / NLL) | RQ2 / H2 |
| **Semantic Commutativity** | Divergence between direct path and multi-hop path | RQ3 / H3 |
| **Contract Accuracy** | Declared information loss vs. measured mutual information loss | RQ1 / H2 |
| **Task Success Rate** | Multi-robot + agent cooperative task completion rate and trial efficiency | RQ4 / H1 |
| **Robustness (Degradation Curve)** | Success rate degradation against heterogeneity dial | RQ4 / H1 |
| **Extension Cost** | Integration time and negotiation message count for new participants (N x N vs. N+N) | RQ5 / H4 |
| **Grounding Generalization** | Success rate on held-out novel objects (embedding vs. symbol-only) | RQ6 / H5 |
| **Safety** | Number of impossible states blocked by the consistency gate / false rejection rate | RQ7 |

**Statistical procedures**: Multi-seed execution, confidence interval reporting, significance tests for baseline comparisons, and agent non-determinism handled via temperature fixing + distribution reporting. **Calibration and contract accuracy are core deliverables that can only be measured in an environment with ground truth.**

---

## 9. Baselines and Ablations

### 9.1 Baselines

- **B0**: No semantic layer; hand-written N x N adapters (demonstrates combinatorial explosion).
- **B1**: Raw data shared blackboard (no semantics).
- **B2**: LLM ad-hoc translation (having Claude perform schema conversion each time without using PSL structure). For refuting "just let the LLM handle it."

### 9.2 Ablations

Remove Phyte fields (covariance, provenance), fidelity contracts, physical consistency gates, and embedding-based grounding one at a time to isolate each contribution.

**Ensuring fairness**: The largest confound is "differential implementation effort between PSL and baselines." Common infrastructure (MuJoCo wrapper, logging, task definitions) is shared across all conditions.

---

## 10. Roadmap (Phase Definitions)

| Phase | Objective | Exit Criteria |
|---|---|---|
| **Phase 0** | Single robot + single agent. Establish Phyte pipeline + round-trip fidelity metrics + metamorphic verification harness | Sanity check: round-trip error is measurable as defined, and invariance tests pass |
| **Phase 1** | Two homogeneous robots (R2R), establish shared world model | Two robots achieve consistency via shared model |
| **Phase 2** | Two heterogeneous robots (unit, frame, naming mismatches) = the core of translation verification | Dose-response curves can be drawn; fidelity, calibration, and commutativity are measured |
| **Phase 3** | Introduce Claude agents (A2R, A2A), document grounding tasks | S6.5 cross-cutting task succeeds end to end |
| **Phase 4** | Stress: schema fuzzing, clock skew injection, reality gap simulation with two instances, novel object grounding | Identify breaking points for robustness, grounding generalization, and causal consistency |
| **Phase 5** | Self-improvement loop (LLM proposes, ground truth disposes) | Learning yields statistically significant improvement in fidelity/success rate |

**Note**: Bootstrapping Phase 0 and the metamorphic verification harness from S7.1 simultaneously offers the highest return on investment (since round-trip metrics and invariance tests serve as the shared measurement infrastructure for all phases). Phase 4's clock skew can verify "causal consistency across clock domains" without a distributed environment by injecting delay and jitter into one stream even on a single Mac.

---

## 11. Threats to Validity and Mitigations

- **Simulation-only limitation**: Mitigated by noise injection, domain randomization, and two-instance reality gap. Real-robot verification is explicitly noted as future work.
- **LLM non-determinism**: Temperature fixing, multi-seed execution, distribution reporting.
- **Asymmetric implementation effort**: Confounding suppressed by sharing common infrastructure.
- **Task overfitting**: Held-out sets of novel objects and novel placements are prepared.
- **Resilience to malicious/erroneous provenance**: Provenance corruption scenarios are included in Phase 4.
- **Semantic drift and scale limitations**: Recorded as known open issues; the scope of conclusions is explicitly bounded.

---

## 12. Deliverables

- PSL research prototype (implementation of core mechanisms).
- Testbed (heterogeneous MuJoCo robot group + pseudo-cloud + Schema Generator).
- Claude Agent SDK multi-agent + MCP tool groups.
- Dual evaluation harness (oracle + metamorphic) and reproducible experiment runner.
- Experimental results (dose-response curves, calibration plots, commutativity, extension cost, ablations) and report.
- Full environment definition, seeds, and configurations for reproduction.

---

## 13. Success Criteria (Project Pass/Fail)

1. Demonstrate a **degradation curve where PSL maintains success rate** while B0/B2 degrade as heterogeneity increases (H1).
2. **Declared fidelity contracts are statistically consistent with measured loss** (H2).
3. **Commutativity violations remain within tolerance thresholds** (H3).
4. **New robot integration is constant-order relative to N x N** (H4).
5. **Embedding-based grounding significantly outperforms symbol-only on novel objects** (H5).

If these are demonstrated, we can claim that PSL is "verifiably smooth."

---

## 14. Glossary

- **PSL (Physical Semantic Layer)**: An intermediary layer that mutually translates between the physical side and the symbolic side via dual grounding.
- **VLA (Vision-Language-Action)**: A model that translates perception -> semantics -> action.
- **Phyte**: The canonical data unit of PSL (Physically-Grounded Semantic Object).
- **Canonical IR**: A canonical intermediate representation that all agents mutually convert with their native language.
- **Fidelity Contract**: A declaration of what information a transformation preserves, what it loses, and how it increases uncertainty.
- **Dual Grounding**: Simultaneous grounding in both the physical (downward) and symbolic (upward) directions.
- **Commutativity / Composability**: The property that different translation paths yield consistent results (functoriality / commutative diagrams).
- **Metamorphic Verification**: A technique for verifying correctness through invariances and equivariances without ground-truth labels.
- **Affordance**: The action possibilities of an object (graspable, pushable, etc.).
- **LOD**: Multi-resolution (multi-abstraction-level) access.
- **R2R / A2R / A2A**: Data pathways between robots / agent <-> robot / agents.
- **Physical Consistency Gate**: A safety mechanism that rejects translations producing physically impossible states.

---

## 15. Related Documents

- `CLAUDE.md` — Development operations definition (coding conventions, build/test/execution commands, directory management, agent development workflow). Serves as the "How to build" companion to this document.
- (To be added) `docs/` — Detailed design of each mechanism (Phyte schema, formal description of fidelity contracts, list of metamorphic relations, MCP tool specifications, etc.).
