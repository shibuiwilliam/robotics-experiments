# PROJECT.md — Multi-World Search (MWS)

> **Role of this document**
> This is the top-level document that defines the **MWS project as a whole**. It states "what, why, and what counts as success."
> **Development operations** — coding conventions, build/run procedures, coding standards, the Claude Code workflow, etc. — live in a separate document, `CLAUDE.md` (they are not included here).
> For the division of roles, see [14. Document Structure](#14-document-structure).

---

## 0. Project Identification

| Item | Content |
|---|---|
| Project name | Multi-World Search (MWS) |
| One-line definition | A cross-cutting retrieval substrate that serves as the "collective memory organ" shared by many embodied instances (robotics, VLA, business AI agents) |
| Status | Research prototype (single-machine / simulation-verification phase) |
| Verification environment | Local MacBook (physics simulation via MuJoCo) |
| Cloud dependency | Gemini LLM (via Gemini ADK) / Gemini Embedding 2 only. Everything else is local |

---

## 1. Vision and Problem

### 1.1 Vision

In a world where robotics, VLA (Vision-Language-Action), business AI agents, and external business systems work together, many instances act in parallel, each producing and consuming different data. MWS provides the **"Collective Hippocampus"** shared by the entire swarm — not a bolt-on retrieval DB, but a shared cognitive substrate that functions as the swarm's nervous system.

### 1.2 The Problem to Solve

The naive idea of stuffing all data into a single vector DB and doing RAG breaks down, because the **time scales, modalities, update frequencies, and latency requirements** of the data differ by orders of magnitude.

| Data type | Main source | Time scale | Main modalities |
|---|---|---|---|
| Physical observation data | Robotics / sensors | ms–s | point clouds, RGB-D, pose, contact, telemetry |
| Action / skill data | VLA / teleoperated demonstrations | s–min | action trajectories, policies, affordances |
| Business data | External systems (ERP/WMS/CMMS/MES, etc.) | hours–months | structured records, transactions |
| Document knowledge | Cloud stores | nearly static | SOPs, manuals, SDS, tickets |

MWS's mission is to make these **four kinds of data with fundamentally different natures** searchable and retrievable across resources and across instances.

### 1.3 The Four Core Questions

MWS's design is structured as the answer to these four questions.

1. **Atom**: What is the minimal unit of memory?
2. **World model**: Where do we ground the truth of the world?
3. **Recall**: How do we search (multi-index fusion)?
4. **Metabolism**: How do we turn raw data into knowledge (consolidation)?

---

## 2. Objectives, Definition of Success, Scope

### 2.1 Project Objectives

1. Show that MWS's core mechanisms (below) are **demonstrable in a simulation environment**.
2. Reproduce and measure, on a MacBook and as a genuine condition, the core MWS premise that "multiple worlds and multiple instances share memory."
3. Through verification scenarios grounded in business use cases, demonstrate MWS's **business value** both quantitatively and qualitatively.
4. Establish, as a research result, a **design-level answer** to the single-machine constraint (especially the cloud-embedding latency problem).

### 2.2 Definition of Success (high level)

- For the major core research hypotheses ([8. Research Hypotheses](#8-research-hypotheses)), a **clear experimental conclusion** — affirmative or negative — has been obtained.
- At minimum, Scenario 1 of [9. Verification Scenarios](#9-verification-scenarios) (multi-actor maintenance handoff) runs end-to-end from ingest → fused retrieval → VLA → audit.
- The metrics of the evaluation framework ([Chapter 10](#10-evaluation-framework-and-success-criteria)) can be measured and reported reproducibly.
- Results are documented honestly, together with the threats to external validity ([Chapter 12](#12-risks-and-threats-to-external-validity)).

### 2.3 Out of Scope (Non-Goals)

Stating these explicitly keeps the research focused.

- **Verification on real robots**: This phase is simulation only. Real-hardware deployment is out of scope.
- **Production-grade availability / scale**: This is a single-machine research prototype; we do not build a distributed production platform.
- **Real integration with specific vendor business systems**: ERP/WMS/CMMS, etc., are replaced with **synthetic data / stubs**. Real system integration is out of scope.
- **Training new LLM / embedding models**: Foundation models depend on the cloud (Gemini). Embeddings run on a single `gemini-embedding-2`; distillation/alignment of a local student model is out of scope for this phase (cloud round-trip latency is mitigated with the Batch API, batching, and a content-hash cache).
- **Productizing UI/UX**: Visualization is limited to experimentation/verification purposes.

---

## 3. Core Concepts and Terminology

The central concepts for understanding MWS. See [Chapter 13](#13-glossary) for the detailed glossary.

- **Experience Atom**: The unified representation for all data. Composed of an "envelope (spatiotemporal coordinates, provenance, modality, embedding, trust/freshness, access policy) + modality-specific payload." Heavy raw data (video, point clouds) carry only a reference, summary, and embedding; the body sits in an object store and is fetched lazily.
- **4D Scene Graph (World Model)**: The "3D + time" scene graph that grounds the truth for retrieval. Physical atoms are connected to entity nodes (objects, places, agents) on this graph, and observations, documents, business records, and skills all hang off the same entity.
- **Multi-Index Retrieval**: Five index types — spatial / temporal / semantic / symbolic-relational / structured — run in parallel and a query planner fuses across them. A multi-objective score (semantic similarity, spatial proximity, temporal freshness, trust, task relevance) is combined by a learned reranker.
- **Consumer-Aware Projection**: The same atom is returned differently per consumer (pose + tensor for VLA, cited text for LLM, low-latency numerics for control loops, aggregates for dashboards).
- **Memory Metabolism (Consolidation)**: A background process clusters episodic memory, distills recurring patterns into semantic/procedural memory, and performs deduplication and TTL pruning.
- **Federation**: Each instance holds a local cache/index of the relevant subset; the cloud holds the full store. Routing uses task-plan-based predictive prefetch and a Quality-of-Retrieval (QoR) class. *(Implementation status: QoR routing and predictive prefetch are both implemented and used in Scenario 3. A falsifiability test confirms that with prefetch enabled the local instant-answer coverage equals the whole swarm = 3 observers, dropping to 1 when disabled.)*
- **Provenance and Governance**: Every atom carries its source; every search is logged. ABAC is applied at index time so that "who knew what, when" can be reconstructed after the fact.
- **Retrieval-Augmented Policy**: Just before generating an action, similar episodes, skill demonstrations, affordances, and safety constraints are retrieved and injected in-context.
- **Active Curiosity Loop**: To fill retrieval gaps (missing, occluded, or stale information), exploration tasks are issued and new observations close the gap.
- **Standing Query**: In addition to one-shot recall, a subscription-style query that notifies when a condition is met. The union of retrieval and pub-sub.

---

## 4. System Architecture Overview

MWS consists of six layers (this chapter is a conceptual definition; for the implementation-module mapping see [Chapter 7](#7-repository--component-structure)).

```
+-------------------------------------------------------------+
|  Consumers: robot control / VLA / business AI agent (ADK) / audit & analytics  |
+----------------^----------------------------^---------------+
                 | consumer-aware projection  | standing query / curiosity
+----------------+----------------------------+---------------+
|  (3) Multi-Index Retrieval Engine                            |
|      spatial / temporal / semantic / symbolic-relational / structured + fusion reranker  |
+----------------^--------------------------------------------+
                 |
+----------------+----------------+  +-------------------------+
|  (2) 4D Scene Graph (World Model)|  | (4) Memory Metabolism (Consolidation)|
+----------------^----------------+  +-------------------------+
                 |
+----------------+--------------------------------------------+
|  (1) Experience Atom (unified representation) + polyglot persistence  |
+----------------^--------------------------------------------+
                 |
+----------------+--------------------------------------------+
|  (5) Federation (edge↔cloud) / QoR   (6) Provenance & governance  |
+-------------------------------------------------------------+
```

**Polyglot persistence (purpose-specific multi-storage)**

| Target | Storage type |
|---|---|
| Raw blobs / tensors | Object store (local files / Parquet, etc.) |
| High-frequency telemetry | Time series (DuckDB/Parquet) |
| Embeddings | Vector DB (local; multimodal-capable) |
| Scene graph / knowledge | Graph DB (embedded property graph) |
| Geometry / space | Spatial index (KD-tree / occupancy grid) |
| Business data | Synthetic data / stub (no migration; mimics a virtualized connection) |

---

## 5. Execution Environment and Tech Stack

### 5.1 Basic Environment Policy

> **Only the LLM and Embedding are cloud (Gemini); everything else is local (MacBook).**

| Layer | Placement | Adopted technology (assumed) |
|---|---|---|
| Physics simulation | Local | MuJoCo (multi-world, multi-instance, sensor generation, state forking) |
| Agent runtime | Local | Gemini ADK (only inference is cloud Gemini. The search tools are exposed MCP-compatibly. A2A direct communication is not implemented — coordination is demonstrated via shared-store stigmergy) |
| Inference (LLM) | Cloud | Gemini (via ADK) |
| Embedding (all uses) | Cloud | Gemini Embedding 2 (native multimodal, 768/1536/3072 dims with MRL, asymmetric task instructions for document/query, async and low-cost via Batch API) |
| Vector search | Local | **Default for scenario runs = Elasticsearch** (dense_vector+kNN, docker-compose) / LanceDB (embedded ANN) / in-memory (test default, offline) — selected via the registry |
| Graph | Local | NetworkX (prototype) → embedded property graph |
| Time series / structured | Local | DuckDB / Parquet |
| Spatial | Local | scipy KD-tree / Open3D / occupancy grid |
| Messaging | Local | ZeroMQ / NATS (between multiple instances) |

### 5.2 The Core Tension the Environment Defines (important)

Because "Embedding is also cloud," **a cloud round-trip happens on every observation and every query**. This can be fatal for robotics retrieval. MWS treats this as a central design problem:

- **Indexing (the document side) is pushed offline**, made async and low-cost with the Batch API.
- **Cloud round-trips on the hot path (text queries) are suppressed with a content-hash cache, single-shot multi-Content batching, and an async indexer** (no local student model is introduced).
- The cloud latency that remains at query time (measured p50 ~400 ms) is measured and disclosed as a known constraint. Fitting high-frequency control loops is out of scope for this phase.

### 5.3 Exploiting the Privilege of Simulation

Because simulation has **ground truth**, we can auto-generate the correct relevance labels for retrieval. This enables quantitative evaluation (Recall@k, etc.) without human annotation. We make this privilege — unavailable on real hardware — a pillar of the evaluation design.

---

## 6. The Original Ideas Running Through the Research

The core of MWS's novelty (details in the experiment-plan document).

1. **Privileged-oracle differential evaluation**: Isolate and measure the "degradation from perception, description, and embedding (the perception tax)" purely, as the gap between a ground-truth upper-bound retriever and the real pipeline.
2. **Single cloud embedding + round-trip reduction**: Unify embeddings on a single `gemini-embedding-2`, drawing out quality with asymmetric document/query task instructions, while suppressing the cost and latency of cloud round-trips with a content-hash cache, single-shot multi-Content batching, and the Batch API.
3. **Counterfactual simulation retrieval**: Fork MuJoCo state, roll it out, and turn the result into a searchable atom. The simulator doubles as a retrieval backend, recalling an "imagined future."
4. **Memory stigmergy**: Test whether swarm coordination emerges with no direct communication, only via a shared store.
5. **Time-dilated worlds**: Spin some worlds fast to generate "month-equivalent" slow events, reproducing the ms-to-month multi-time-scale problem on a single machine.

---

## 7. Repository / Component Structure

Responsibility-level decomposition (**directory details, naming conventions, dependency management, etc., are in CLAUDE.md**).

```
mws/
├── core/            # Experience atom schema, envelope, provenance, access policy
├── worldmodel/      # 4D scene graph (entity nodes, relations, time)
├── storage/         # Polyglot persistence adapters (vector/graph/timeseries/spatial/blob)
├── embedding/       # Gemini Embedding 2 (single model), content-hash cache, async indexer
├── retrieval/       # Multi-index search, query planner, fusion reranker, consumer-aware projection
├── consolidation/   # episodic→semantic distillation, dedup, TTL pruning
├── federation/      # edge↔cloud, QoR routing, predictive prefetch
├── reactive/        # standing query (pub-sub), curiosity loop
├── agents/          # Gemini ADK agent definitions, A2A, MWS search tools (MCP-compatible) exposure
├── vla/             # retrieval-augmented VLA, recall/injection of skill-demo atoms
├── sim/             # MuJoCo worlds, sensors, multiple instances, state forking
├── business/        # synthetic business data (ERP/WMS/CMMS/MES/SOP/SDS) generation, stubs
├── scenarios/       # verification-scenario implementations (§9)
├── eval/            # metrics, auto relevance-label generation, latency decomposition, reports
└── configs/         # world / experiment / model configuration
```

Responsibility summary per component (implementation rules are in CLAUDE.md):

- **core / worldmodel / storage**: The data foundation. Normalize atoms, connect them to entities, persist per purpose.
- **embedding / retrieval / consolidation**: The cognitive layer. Index, recall, and turn into knowledge.
- **federation / reactive**: Distribution and reactivity. Placement, look-ahead, subscription, active exploration.
- **agents / vla / sim / business / scenarios / eval**: The experiment layer. Consumers, bodies, the external world, verification, measurement.

---

## 8. Research Hypotheses

Experiments are designed as hypothesis tests (stated falsifiably).

| ID | Hypothesis | Mainly verified by scenario |
|---|---|---|
| H1 | Shared memory improves the task performance of an inexperienced instance (transfer) | 4, 1 |
| H2 | Swarm coordination emerges with no direct communication, only via a shared store (stigmergy) | 5, 3 |
| H3 | The perception tax can be quantified as the gap between oracle retrieval and real-pipeline retrieval | 1, general |
| H4 | World change raises the stale-hit rate, and TTL, trust, and re-observation recover it (freshness) | 2, 7 |
| H5 | Consolidation curbs store bloat while preserving task-relevant recall | 3 |
| H6 | A retrieval-gap → exploration curiosity loop lowers the task failure rate | 5, 6 |
| ~~H7~~ | ~~Two-tier teacher(Gemini)/student(local) embedding~~ → **withdrawn**: embeddings unified on a single `gemini-embedding-2` (the student tier is dropped; round-trip reduction is handled by cache/batch/Batch API) | — |
| H8 | Consumer-aware projection raises utility for each consumer | 1, 7 |
| H9 | Provenance-weighted multi-observer fusion improves pose estimation over a single observation | 2 |

---

## 9. Verification Scenarios

A set of scenarios in which the four data types necessarily intersect within a business context and which can be reproduced and measured in MuJoCo. Detailed design is in a separate document.

1. **Multi-actor maintenance handoff (flagship)**: Anomaly detection → fused recall of observation + manual + CMMS + inventory + shift → instruction generation → a differently-embodied robot performs the work via skill recall. Audit log over the whole process.
2. **Reconciling physical reality with the system of record**: Adjudicate discrepancies between robot observations and ERP/WMS records using provenance, trust, freshness, and observer agreement, then write back / file a discrepancy ticket. "Physics is the truth, records are records, and MWS adjudicates the divergence."
3. **Collective discovery of weak signals by the swarm**: Consolidation bundles individually-harmless observations across the swarm and across time, correlates them with business data, surfaces a latent pattern (e.g., a defective lot), and registers a standing query.
4. **Instant rampup of a new SKU / new site (transfer)**: A single demonstration/exploration is reused across the whole swarm as skill/map atoms.
5. **Real-time coordination for incident response**: Standing query fires → fused recall of SDS / floor plan / roster → notify + dispatch robots. Occluded areas are scouted via the curiosity loop to finalize the plan.
6. **Counterfactual-based on-site safety decision**: Just before a dangerous operation, fork the digital twin and roll out, then recall the result atom to choose a safe action. MuJoCo doubles as the counterfactual engine.
7. **End-to-end order to fulfillment**: External order → confirm physical inventory via robot observation → picking VLA → exception handling (damage → reorder/notify) → ERP update. Orchestration across multiple external systems.

**Mechanism coverage** (◎ primary / ○ secondary)

| Mechanism | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| Cross-modal / source fusion | ◎ | ○ | ◎ | ○ | ◎ | ○ | ○ |
| Retrieval-augmented VLA / skill propagation | ◎ |  |  | ◎ | ○ | ○ | ○ |
| Multi-instance handoff / coordination | ◎ | ○ | ○ | ◎ | ◎ |  | ○ |
| Physical-vs-record consistency / freshness | ○ | ◎ | ○ |  | ○ |  | ◎ |
| Multi-observer fusion |  | ◎ | ○ |  | ○ |  |  |
| Consolidation |  |  | ◎ | ○ |  |  |  |
| Standing query |  |  | ○ |  | ◎ |  | ○ |
| Active curiosity |  |  |  | ○ | ◎ | ○ |  |
| Counterfactual / sim retrieval |  |  |  |  |  | ◎ |  |
| Provenance / audit | ◎ | ○ | ○ |  | ○ | ○ | ○ |
| External-system write-back | ○ | ◎ |  |  |  |  | ◎ |

---

## 10. Evaluation Framework and Success Criteria

### 10.1 Metric Categories

- **Retrieval quality**: Recall@k / MRR / nDCG (relevance auto-defined from sim ground truth).
- **Task**: success rate, completion time, path length, collision count, sample efficiency.
- **System**: e2e latency p50/p95 (decomposed into local ANN / Gemini embedding call / Gemini inference), cloud-call count & cost, virtual edge↔cloud bandwidth, local memory / index growth curves.
- **Multi-instance**: transfer gain, coordination gain, stale-hit rate, conflict-resolution accuracy.
- **Consolidation**: compression ratio, recall retention, latency vs. store size.

### 10.2 Statistical Discipline

Report confidence intervals over multiple seeds × multiple worlds. Pre-register metrics to prevent cherry-picking.

### 10.3 Per-Phase Go/No-Go Gates

| Phase | Gate condition |
|---|---|
| Phase 0 | Cross-modal recall works and the latency breakdown can be captured |
| Phase 1 | The perception tax can be quantified and MRL-dimension quality/latency curves can be drawn |
| Phase 2 | Twin-instance transfer gain can be measured significantly |
| Phase 3 | A consolidation compression↔recall Pareto can be drawn and freshness recovery can be shown |
| Phase 4 | The curiosity loop, standing query, and counterfactual retrieval work as a closed loop |
| Phase 5 | Stress/ablation isolates each mechanism's contribution, ready for write-up |

---

## 11. Development Phases and Roadmap

| Phase | Goal | Main mechanisms | Main scenarios |
|---|---|---|---|
| Phase 0 | Harness, schema, baseline | core / worldmodel / storage / embedding | — |
| Phase 1 | Cross-modal retrieval quality, perception tax | retrieval / embedding | 1 |
| Phase 2 | Twin-instance transfer (the core) | federation / vla | 4, 1 |
| Phase 3 | Stigmergy, consolidation, freshness | consolidation / federation | 3, 2 |
| Phase 4 | Curiosity, standing query, projection, counterfactual | reactive / retrieval / sim | 5, 6, 7 |
| Phase 5 | Stress, ablation, write-up | eval | general |

As a vertical slice, right after Phase 0→1 we push a minimal end-to-end implementation of Scenario 1 to pierce all MWS layers once.

---

## 12. Risks and Threats to External Validity

| Risk | Description | Mitigation |
|---|---|---|
| Sim-to-real gap | Sim observations are too clean and underestimate the perception tax | Inject sensor noise, partial observation, domain randomization |
| Cloud nondeterminism | Gemini rate limits, cost, jitter | Batch API, cache, fixed seed, always record cost |
| Single-machine resource contention | Distorted latency measurement | Isolate measurement, pin CPU, report under-load measurements too |
| Embedding-space incompatibility | A model update changes the coordinate space and forces re-indexing | Make embedding versioning an explicit experimental target |
| Scope creep | Chasing all 7 scenarios at once | Manage in stages with phases and go/no-go gates |

---

## 13. Glossary

| Term | Definition |
|---|---|
| MWS | Multi-World Search. This project's shared cross-cutting retrieval substrate |
| Experience atom | The unified representation of data (envelope + payload) |
| 4D scene graph | The backbone of the world model, composed of 3D + time |
| Multi-index retrieval | Retrieval fusing five types: spatial / temporal / semantic / symbolic / structured |
| Consumer-aware projection | The mechanism that changes how an atom is returned per consumer |
| Consolidation | The episodic→semantic metabolism of memory (distillation, pruning) |
| Federation | Edge↔cloud distribution, caching, QoR routing |
| QoR | Quality of Retrieval. The retrieval-quality contract: latency, freshness, authority, etc. |
| Provenance | An atom's source and the search log. The basis for audit and reconstruction |
| Standing query | A subscription-style query that notifies when a condition is met |
| Curiosity loop | The active mechanism that fills retrieval gaps with exploration tasks |
| Retrieval-augmented VLA | A policy that injects relevant experiences, skills, and constraints before generating an action |
| Perception tax | The retrieval-quality loss due to degradation in perception, description, and embedding |
| Stigmergy | Indirect coordination via a shared environment (memory) |
| VLA | Vision-Language-Action model |
| ADK | Gemini Agent Development Kit |

---

## 14. Document Structure

| Document | Role | Contains | Does not contain |
|---|---|---|---|
| **PROJECT.md** (this doc) | Whole-project definition | Vision, objectives, scope, concepts, architecture overview, environment, hypotheses, scenarios, evaluation, roadmap, risks, glossary | Dev conventions, build procedures, coding standards |
| **CLAUDE.md** | Development-operations guide | Directory conventions, dependency management, run/test procedures, coding standards, the Claude Code workflow, tool-usage policy | Project strategy, hypothesis definitions |
| Experiment-plan document (separate) | Experiment details | IV/DV per hypothesis, condition tables, procedures, metric definitions | — |
| Scenario-design document (separate) | Scenario details | Actor responsibilities, data flow, query specs, world definitions, business-data schema | — |
