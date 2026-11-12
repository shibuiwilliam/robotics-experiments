# STATUS

> English translation of the Japanese original (snapshot of 2026-11-12). The Japanese file is canonical; numbers may lag behind it.

Current status: **M0–M10 complete (gates G0–G10 passed. G8 / G9 / G10 on 2026-11-08, ADR-0025 / ADR-0030 / ADR-0035)**.

Last updated: 2026-11-08 (status cleanup after the completion of M8 and M9. The table "Reach of the operational paths" contains descriptions as of 2026-10-25; later changes are appended in the section directly below it). There were no code changes from 2026-10-04 to 10-24, and
the 2026-10-17 inventory was left uncommitted (`docs/PROGRESS.md` finding 107). On 10-25 we committed the inventory,
merged the M8 branch into main (X-10), and then carried out the following, one branch per task, fast-forwarding each into main:

| Kind | Task |
|---|---|
| Bug fixes (reproduction test first) | X-11 (per-layer thresholds are also read from `.env`), X-12 (multiple profiles for `make up`), X-14 (pass the top-level `.env` to compose), `fluent_of_event` reading a full IRI as a fluent (finding 116, within M8-25-6) |
| M8 | M8-28 (read Situation participation via `hasParticipant`), M8-30 (pass business value, reliability, and entity type to the reconciliation worker), **M8-25 (periodic execution of the recording path, plans, and the asset ledger. Carried out in 6 sub-tasks after the plan was approved. ADR-0016, ontology 0.6.0, ingest-worker as the 6th worker)** |
| Documents | X-09 (bring convention documents in line with the ADRs), X-13 (commands and settings in the entry-point documents). The proposed change to CLAUDE.md (X-15) was merged on 2026-11-08 after human confirmation |

The main points of the inventory (re-verifying the 10-17 claims against the code and confirming that all were correct, running the integration tests, and adding findings 107–112 by investigating outside the 10-17 scope) are in the reach section of `docs/PROGRESS.md`. Findings made during task execution are 113–116.

M8 "wiring" is the milestone that fills in the places where, up to M7, "the types and judgements were implemented but the suppliers and periodic execution were not" (the task list and the definition of gate G8 are in `docs/TASK.md` §3). **All 3 conditions of gate G8 are met (2026-11-08)**:

| G8 condition | State |
|---|---|
| Maintain G0–G7 | ✅ On 2026-11-08, `make check` 1952 tests, **integration 236 (executed)**, and UI 126 are green (maintained after M9 completed too) |
| `TODO(question)` is 0 | ✅ 0 in the code (re-scanned on 2026-10-25. Resolved in ADR-0009–0015) |
| M8-11 (simulating only the outside world, the 6 workers return a non-zero `step()` through the chain of operational code) | ✅ `tests/integration/test_g8_workers.py` (ADR-0025). 6 chain contracts fixed (ADR-0026) |

| Milestone | Completion condition (summary) | State |
|---|---|---|
| M0 Skeleton | core types, vocab generation, eventlog, knowledge bitemporal API, Compose, `make check` green | **Complete (G0 passed 2026-10-03)** |
| M1 Integration | BusinessEvent conversion, mapping, reliability model, plan/norm ingestion, write-back dry-run | **Complete (G1 passed 2026-10-03)** |
| M2 Grounding | probe IF, calibration, event promotion, verification (synthetic latents) | **Complete (G2 passed 2026-10-03)** |
| M3 Reasoning | belief update (three sources), reconciliation (five types), explanation | **Complete (G3 passed 2026-10-03)** |
| M4 WM | koopman / jepa_ts, registry, symbolic conditioning, health | **Complete (G4 passed 2026-10-03)** |
| M5 Closed loop | semantic contracts, Expectation verification, forecasting/What-if, API | **Complete (G5 passed 2026-10-03)** |
| M6 Evolution | concept discovery, scheduler, LLM integration | **Complete (G6 passed 2026-10-03)** |
| M7 Operations | re-grounding job, UI, observability | **Complete (G7 passed 2026-10-03. Only the third-party executability of the runbooks is conditional)**. **2026-10-25 note**: re-grounding's `run(plan)` did not exist, and the runbook's execution procedure was running the integration tests (finding 110) |
| M8 Wiring | fill in suppliers, periodic execution, persistence, and API resources so the workers run on real services | **Complete (30 / 30, G8 passed 2026-11-08)** |
| M9 Entrances to operation | execution of re-grounding (`make reground`), periodic forecast job (`make forecast`), GraphQL forecasts, G9 | **Complete (4 / 4, G9 passed 2026-11-08, ADR-0030–0032)** |
| M10 Entrances to operation, part 2 | quarantine queue (write side, read/write API), What-if job and read path, G10 | **Complete (5 / 5, G10 passed 2026-11-08, ADR-0035, 0036)** |

"Complete" for G0–G7 means that **integration tests confirmed the pieces could be assembled as a library**. How far each is on the operational routes
(workers, API) is shown separately in the next section.

## Reach of the operational paths (2026-10-25, after task execution)

The result of tracing the three data paths of design document §2.5 from `services/` and `scripts/`. On 10-17 **there was no code to write the head of each path in operation** (findings 101–103). On 10-25 **the head of the recording path went into operation** (M8-25). The head of the inference path
did not exist yet, so on a new environment grounding (bridge-worker) fails at construction.

| Path | Stages that run in operation | Stages absent from operation (exist only inside the library and tests) |
|---|---|---|
| Recording path | **business system (SQLite mock in development) → ingest-worker → mapping → existence events + claim events → `integration.business`** (M8-25) | Connectors to real systems (outside this repository, open in ADR-0016) / persistence of schema fingerprints and sending `MappingDriftDetected` / ledgers of SKUs and site layouts |
| Asset ledger and plans | **ledger → observed assertions + projection graph (individual pieces of equipment, `partOf`), plans → `planned`** (ingest-worker) | Handling of beliefs about equipment that disappeared from the ledger (open in ADR-0016) |
| Inference path | latent index → probe → event promotion → `bridge.physical` (bridge-worker). **The entity to ground can now be looked up from the ledger projection** | observation → `encode` → latent index (M8-24) / placing the probe bundle in the store (M8-23) / SHACL validation, identity, spatial binding (H-24) |
| Belief update | `bridge.physical` → forward filter → Oxigraph, part-whole coupling (belief-worker. `partOf` enters from the ledger projection) | **Three sources** (M8-26, 2026-11-07: subscribe to `bridge.physical` and `integration.business`, feed claim events as the recorded likelihood and `planned` statements as the prior). **The contributions of the three sources are kept in `belief_contributions` and shown in 3 columns in GraphQL `beliefEvidence` and the side-by-side view of the discrepancy queue** (M8-17, ADR-0020). However, no one writes the physical-side events yet (M8-24) / no one writes the participation relation of Situation |
| Reconciliation | Detection of type 2 → `discrepancies` table + site topic (reconcile-worker). **Claim events enter the record column**, and business value is looked up by entity type (ledger projection) (M8-25 / M8-30) | Head of the physical-side input (M8-24) / **suppression is structurally 0** (the suppression rule of type 2 is always false, finding 113) / types 1, 3, 4, 5 |
| Human decision | API commands (`resolve_discrepancy` / `curate_concept` / `execute_action` / **`answer_observation`** / **`review_quarantine`**) → decision queue → learning-worker (observation requests, curation, action execution, **human answer → `observed` claim event → belief**. M8-29, ADR-0018) | Authentication of the command API (H-25) / answer form (only up to the API command) / write-back (deliberately not wired in the operational assembly) |
| Action | precondition verification → execution → writing the expectation → **periodic verification of expired expectations** (learning-worker. Violation → discrepancy, undecidable → observation request. M8-27, ADR-0017) | `ActionEvent` / compensation (H-07 / H-08) / early verification before the deadline and transition to `Learned` |
| Other | Selection of observation requests (scheduler-worker. Destination not configured), 11 of 25 metrics, `/metrics`, **periodic execution of concept discovery** (learning-worker. The entrance is recurring discrepancies only; re-proposals are suppressed by the signal fingerprint. M8-09, ADR-0019) | **Execution of re-grounding** (`run(plan)` is absent, finding 110) / entrances for forecasting, What-if, explanation, and LLM features / discovery entrances (a)(b) (waiting on M8-24 / M8-15) / live LLM (H-04) — anything outside the purpose of M8 is H-24 |
| Settings and startup | Both the top level of `Settings` and the per-layer thresholds take effect from `.env` (X-11). `make up` passes the top-level `.env` to compose (X-14) and can start multiple profiles (X-12) | `make dev` starts only the API (workers are chosen explicitly with `--workers`) |

## Addendum to the reach of the operational paths (2026-11-08, after M8 and M9 completed)

What has gone into operation since the table above (10-25). Of the "stages absent from operation" in that table, the following are resolved:

| Item | What resolved it |
|---|---|
| Head of the inference path | observation → `encode` (M8-24, ADR-0023) → latent index → probe bundle (M8-23, ADR-0024) → bridge-worker. G8 (ADR-0025) simulated only the outside world and drove the 6 workers' non-zero `step()` |
| Belief update | Three sources (M8-26); repeated claims from the same information source are **replaced** (H-26, ADR-0027) |
| Re-grounding | `make reground` (M9-01, ADR-0031). **Agreement with live is an approximation** (batch boundaries and plan priors differ) |
| Forecasting | `make forecast` (M9-02, ADR-0032). Binary events are scored by Brier and the coverage of the predicted set. The measurement is the belief-worker's belief, **not ground truth** |
| Queries | GraphQL `forecasts` (M9-03. Goes through ABAC; samples are not returned), `beliefEvidence` (M8-17) |
| Commands | The "who" is determined from the requester of `principal_resolver` (H-25, ADR-0029). **However, the current resolver is a development header and cannot prevent spoofing** (waiting on production authentication, H-02) |
| LLM | Live path (anthropic only, direct HTTP. M8-10, ADR-0022). Used by the discovery loop |

**Stages still absent from operation** (H-27 and others): operational routes for identity binding, spatial binding, and SHACL quarantine; entrances for LLM-based features (document extraction, NL→query, explanation text generation); the What-if entrance;
reconciliation of types 1, 3, 4, 5; `suppression_rate` is structurally 0 in operation; command authorization (the remainder of H-25); write-back. `/stream` was removed in ADR-0033.

## Scale (measured 2026-11-08, after M9 completed. Some values updated from 10-25)

| Item | Value |
|---|---|
| Tests | unit 1851 / property 25 / contract 76 (1952 in `make check`) / **integration 236 (executed)** / UI 126. At the time of the inventory (10-17): 1332 / 21 / 67 = 1420, integration 187 |
| Breakdown of `make check` | ruff, pyright strict (0 errors), import-linter 7 contracts KEPT, ontology-check 7 items, secret scan. 2 warnings (1 dependency deprecation warning, 1 intentional socket block) |
| Ontology | 0.6.0 (unchanged in M9. 945 triples, 108 terms subject to annotation checks, FluentType 7 / EventType 9) |
| ADRs | 36 (0001–0036. Since 10-25: 0017–0027 are for M8, 0028 secrets only in `.env`, 0029 subject of commands, 0030–0032 are for M9, 0033 removal of `/stream` and restricting the UI to localhost, 0034 proposed policy for erasure requests (proposal, awaiting legal review)) |
| Workers | 6 (bridge / belief / reconcile / scheduler / learning / ingest). Also periodic jobs `make reground` (dry-run by default) and `make forecast` (M9). Metrics are written by each worker and job |
| DB migrations | 17 (`0001_initial_projections` … `0016_quarantine_review_decisions`, `0017_scenarios`) |
| Metrics | 25 metrics in 6 areas. **23 have their writes wired up** (2026-11-08. 20 in M8-15, plus `regrounding_duration` / `forecast_crps` / `forecast_coverage` in M9). The remaining 2 (`identity_bind_rate` / `identity_revoke_rate`) are not wired because there is no operational route for identity binding (H-27). `suppression_rate` is structurally 0 in operation (finding 113) |
| Compose services | 4 (+ optional profiles: ui / console / observability). Images are pinned versions such as seaweedfs 3.80 / oxigraph 0.5.11 |
| API | GraphQL: 8 Queries (beliefs / discrepancies / expectations / proposals / forecasts / scenarios / quarantine / health), 0 Mutations, 5 commands (resolve_discrepancy / curate_concept / execute_action / answer_observation / review_quarantine). There is no `/stream` (removed in ADR-0033) |
| Repository | Fast-forwarded into main one branch per task (154 commits, linear). About 36,500 lines in src / about 34,500 lines in tests. **No remote → CI has never run** (H-23) |

## What to do next (recommended order)

**Items needing the user's confirmation or decision**: H-01 (rotation of the real keys. Decided; the procedure is `docs/runbooks/secret-rotation.md`, execution is by a human), H-23 (create the remote and push),
the remainder of H-25 (command authorization, `decision_id` not including the subject. The production authentication method was decided in H-02 as "not published", so re-file before publishing), the remainder of H-27 (identity binding and entrances for LLM-based features. The SHACL quarantine and the What-if entrance went in with M10).

**M8, M9, and M10 are complete** (2026-11-08). M10 is the quarantine queue (write side M10-01, read/write M10-02), the What-if job (M10-03) and read path (M10-04), and G10 (M10-05). M9 is the execution of re-grounding (M9-01), forecasting and scoring (M9-02), the `forecasts` GraphQL (M9-03), and G9 (M9-04).

**Cross-cutting**: X-07 (mechanical check of the numbers and descriptions in documents), the monthly query of X-05 (last 2026-11-08, updated to 0 vulnerabilities. The next is due around 2026-12-08).

## Remaining nature of the work (3 categories)

At the time M7 passed, it split into three: **(a) awaiting a human decision / (b) dependent on other unimplemented work /
(c) cannot be verified until applied to real data**. M8 resolved the place to hold the work items for (b), but the inventory
found (b) items of the kind **the head of the data paths** (10-17) and **wiring assembled with empty defaults** (10-25). Within 10-25, the head of the
recording path and the wiring of the reconciliation worker were filled in:

- **(a) Awaiting a human decision** → 27 items in `docs/HITL.md` (measured 2026-11-08: waiting 10 / provisional 8 / overdue 1 /
  decided 6 / decided and awaiting execution 1 (H-01) / partially decided 1 (H-25)). The top priorities needing the user's action are **rotating the keys in H-01**
  (decided. The real key in the development machine's `.envrc` is still plaintext. Not executed since it was recorded as overdue on 2026-10-04) and **H-23** (remote and CI. Preferably after H-01).
  On 2026-11-08 we decided H-04 / H-22 / H-24 / H-26, partially decided H-25, and added H-27.
- **(b) Dependent on other unimplemented work** → M8 and M9 are complete. The remaining operational gaps (identity binding, spatial binding, SHACL quarantine, entrances for LLM-based features, the What-if entrance) are
  awaiting a decision on the priority of H-27 (the H-24 decision was settled with option A on 2026-11-08, and re-grounding, forecasting, and the forecast API went in with M9).
- **(c) Cannot be verified until applied to real data** → cannot be resolved in this repository. Use-case validation is done in a
  separate repository (ADR-0001). The arrangement is decided in H-18 (as of 2026-10-17, no other project in the parent directory
  references SBP).

This file holds only a milestone-granularity summary. Task-granularity plans, completion conditions, and gate definitions are managed in
`docs/TASK.md`, facts found in `docs/PROGRESS.md`, and items awaiting a human decision in `docs/HITL.md`.
This file is updated when a gate passes, when the progress of an in-progress milestone such as M8 moves, and at inventories.
