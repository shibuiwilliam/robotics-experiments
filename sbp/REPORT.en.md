# SBP Development and Verification Report

> English translation of the Japanese original (snapshot of 2026-11-12). The Japanese file is canonical; numbers may lag behind it.

Date written: 2026-11-08 / Target: main (`f006db2`, 169 commits, linear) / Current status: **M0–M10 complete (gates G0–G10 passed)**

This report summarizes the development and verification to date so that it can be read on a single page. The canonical sources are the documents below. Numbers are measurements as of 2026-11-08.

| What you want to know | Document |
|---|---|
| Current status and scale | `docs/STATUS.md` |
| Definitions of tasks and gates | `docs/TASK.md` |
| Facts found (findings 1–144) | `docs/PROGRESS.md` |
| Awaiting human decision | `docs/HITL.md` |
| Design decisions | `docs/adr/` (36 ADRs) |

---

## 1. Summary

- SBP is a "belief-maintenance system" that uses a world model (WM) and an ontology to line up observations of the physical world and records in business systems on the same vocabulary, time axis, and confidence. This repository holds only the platform core; use-case validation is done in a separate repository (ADR-0001).
- M0–M10 were carried out in this order: one branch per task → `make check` green → independent review → fixes for the findings → fast-forward merge.
- All automated tests are green. However, **verification on real data has never been done** (only the synthetic data `SynthWorld` and mocks of business systems). Because there is no remote, CI has never run either.
- Some items still require the user's decision or action before this can be used in production (§8).

| Verification | Result |
|---|---|
| `make check` (lint, pyright strict, layer dependencies, ontology-check, secret scan, unit/property/contract) | **1952 green** |
| Integration tests (real services on Compose) | **236 green** |
| UI tests | **126 green** |

---

## 2. What was built

### 2.1 Structure

Layer dependencies are one-directional (checked mechanically with 7 import-linter contracts).

```
core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge ← reasoning ← actions ← api
```

| Item | Value |
|---|---|
| Code size | about 36,500 lines in src / about 34,500 lines in tests |
| Workers | 6 (bridge / belief / reconcile / scheduler / learning / ingest) |
| Periodic jobs | `make reground` (dry-run by default), `make forecast`, `make whatif` |
| Compose services | 4 (Redpanda / Postgres+Timescale+pgvector / Oxigraph / SeaweedFS) + optional profiles |
| Ontology | 0.6.0 (945 triples, 7 FluentTypes, 9 EventTypes) |
| DB migrations | 17 |
| Metrics | of 25 metrics, 23 have their writes wired up |
| API | GraphQL: 8 Queries, 5 commands (`/stream` was withdrawn in ADR-0033) |

### 2.2 Data paths

The three paths of design document §2.5 are connected through to the operational entrances.

| Path | Flow |
|---|---|
| Recording path | business system → ingest-worker → mapping → existence/claim events → `integration.business` |
| Inference path | observation → `encode` → latent index → probe → event promotion → `bridge.physical` |
| Belief update | three sources (physical, business, plan) → forward filter → Oxigraph. Contributions are kept in `belief_contributions` and can be viewed side by side in 3 columns through GraphQL `beliefEvidence` |

Beyond these come reconciliation (discrepancy detection), human decisions (API command → decision queue → learning-worker), action execution and expectation verification, and forecasting / What-if.

---

## 3. Course of the milestones

| Milestone | Content | Gate |
|---|---|---|
| M0 Skeleton | core types, vocab generation, eventlog, bitemporal API, Compose | G0 (2026-10-03) |
| M1 Integration | mapping, reliability model, plan and norm ingestion, write-back dry-run | G1 |
| M2 Grounding | probes, calibration, event promotion, verification | G2 |
| M3 Reasoning | belief update, reconciliation (five types), explanation | G3 |
| M4 WM | koopman / jepa_ts, registry, symbolic conditioning | G4 |
| M5 Closed loop | semantic contracts, Expectation verification, forecasting / What-if, API | G5 |
| M6 Evolution | concept discovery, scheduler, LLM integration | G6 |
| M7 Operations | re-grounding job, UI, observability | G7 (third-party execution of the runbooks is conditional) |
| M8 Wiring (30 items) | fill in the suppliers and periodic execution | G8 (2026-11-08, ADR-0025) |
| M9 Entrances to operation | re-grounding execution, forecasting and scoring, `forecasts` API | G9 (2026-11-08, ADR-0030–0032) |
| M10 Entrances to operation, part 2 | quarantine queue, What-if job and read path | G10 (2026-11-08, ADR-0035, 0036) |

**Important background**: "Complete" for G0–G7 meant that integration tests had confirmed the pieces could be assembled as a library. An inventory on 2026-10-17 revealed that **there was no code to write the head of each path in operation** (findings 101–103). We therefore built the "wiring" of M8 and redefined G8 as "the test simulates only the outside world, and the inside of the platform is driven through operational code" (decision on H-22, ADR-0025). M9 and M10 use the same criterion.

---

## 4. Details of M10 (the most recent work)

The goal was to build two entrances, whose design decisions are light, among the H-27 items (stages absent from operation).

### 4.1 Quarantine queue (M10-01, M10-02)

- **What it picks up**: rows that failed mapping, and ledger rows whose type is not in the ontology. Previously the ingestion cursor advanced and the rows were never read again, or they only appeared in the log. Now each raw row is stacked in a quarantine table.
- **Idempotency**: the id is a uuid5 of `site|source|table|subject|reason`. Even if the same input fails every cycle, it stays one row. An unreviewed row is updated with the latest input; a reviewed row is not reopened.
- **Read/write**: GraphQL `quarantine` and the command `review_quarantine`. The reviewer is determined from the authenticated requester (ADR-0029). Both reading and reviewing require observed permission over all concepts of the requester's own site. The learning-worker applies it.

### 4.2 What-if (M10-03, M10-04)

- `make whatif` returns two trajectories, with and without the intervention, side by side under **the same seed**. The resulting statements are written to a `hypothetical` scenario graph. They do not enter the main belief (invariant 3).
- It runs only on a WM that was trained with interventions (`KoopmanModel.action_dim`). Otherwise it is rejected with a stated reason and exit code 2 (0 success / 1 runtime error / 2 rejection / 3 configuration error).
- Reading GraphQL `scenarios` requires `hypothetical` permission. The development resolver does not hold that permission, so by default nothing is visible.

### 4.3 Gate G10

`tests/integration/test_g10_entrances.py` checks the following with a real Postgres, a real Oxigraph, and the real API.

| Check | Result |
|---|---|
| Broken rows are not discarded; they are stacked in quarantine and readable through the API | ✅ |
| One row each even if it fails every cycle | ✅ |
| The review command is recorded under the requester's name (claiming another person's name gives 403) | ✅ |
| In the same cycle, healthy rows next to a broken row are sent | ✅ |
| What-if results appear neither in the main belief nor to the default requester | ✅ |
| A WM without interventions is rejected | ✅ |

---

## 5. Verification method

| Layer | Content |
|---|---|
| Unit 1851 | External I/O is mocked via Protocols. The network is blocked with `pytest-socket`. |
| Property 25 | Bitemporal as-of / valid-at, modality lattice, interval algebra, idempotency, via hypothesis |
| Contract 76 | Schema ↔ pydantic round trips, envelope backward compatibility, mapping invariants, **X-07** (mechanical check that numbers in the documents match reality) |
| Integration 236 | Run eventlog → knowledge → reasoning on Compose with synthetic data |
| UI 126 | Vitest |
| Static | ruff, pyright strict (0 errors), import-linter 7 contracts, ontology-check 7 items, secret scan |
| Independent review | A different Agent from the implementer reads each task's committed diff (`git diff main...HEAD`). |

- Bug fixes begin with a reproduction test.
- Mutation checks are also done. In M10-01 we found a test that still passed when the set of items to stack in quarantine was emptied, and changed it so that it fails.
- Real data and a real LLM are not used in tests. The LLM uses `replay` fixtures.

---

## 6. Defects found by independent review and verification

These are the ones fixed in M10. Some were pre-existing defects.

| Target | Defect | Response |
|---|---|---|
| Quarantine | Writes fail on input containing NUL characters or NaN | Sanitize before storing |
| Quarantine | The reason text contains input values (information leak) | Use only the type and location |
| Ledger (pre-existing) | The last valid statement of an asset that received a broken edit gets closed | Do not close it (`hold`) |
| Ingestion | A quarantine failure stops the sending of healthy rows | Send first |
| Ingestion | Missing import of `Rejection` (found by a test added after review) | Fixed |
| Review command | No permission check | Require the same permission as the read path |
| Review command | Re-applying the same review fails | Make it succeed (so a decision that failed midway can be resubmitted) |
| What-if | The scenario identifier key lacks seed, samples, and probe, so a re-run with a different seed leaves stale curves | Add them to the key |
| What-if | Configuration errors and rejections share an exit code | Make configuration error 3 |

The "6 inconsistencies in the chain contracts" that G8 found in M8 and M9 were handled in ADR-0026, and repeated claims from the same information source by replacement (ADR-0027).

There was also an accident of my own making during the work. A `git checkout` once lost uncommitted edits, which were restored from a backup. Since then I have checked the diff before any operation that discards changes.

---

## 7. Constraints, and cautions when reading the results

These items are here so that the results are not over-read.

1. **It is unverified on real data.** Only synthetic data and SQLite mocks. The accuracy and calibration numbers are evidence that "the mechanism works", not evidence of performance in the field.
2. **What-if curves are not causal claims.** The effect comes from a linear control term, and the number of cases in which the intervention was actually observed (validity) is not counted.
3. **As things stand, an operational WM always rejects What-if.** The field edge needs to place an `actions` column in the spool (`docs/runbooks/whatif.md`).
4. **The SHACL shapes have not yet been applied to production input.** `AssetShape` makes `locatedIn` mandatory, but the ledger has no location column, so applying it would make every row a violation. Whether to give the ledger a column or loosen the shape is an ontology decision, and is open in ADR-0035.
5. **The counterpart for scoring forecasts is the belief-worker's belief, not ground truth.**
6. **Re-grounding's "agreement with live" is an approximation** (batch boundaries and the plan priors differ).
7. **The "who" of a command is not authentication.** The development resolver uses a header and cannot prevent spoofing (H-02, on the premise that it is not published).
8. **`suppression_rate` is structurally 0 in operation** (the suppression rule of type 2 is always false, finding 113).
9. **CI has never run** (no remote, H-23).

---

## 8. Incomplete items, and items requiring the user's decision or action

| Item | Content | Owner |
|---|---|---|
| **H-01** | Rotation of the real keys. A plaintext real key remains in the development machine's `.envrc`. The procedure is the rotation runbook (kept locally, not published). It has not been done since it was recorded as overdue on 2026-10-04. | User |
| **H-23** | Create the remote, push, and run CI for the first time. Preferably after H-01. | User |
| H-05 | Legal review of ADR-0034 (pseudonymization for erasure requests) | User |
| Remainder of H-25 | Command authorization, collisions because `decision_id` must be a globally unique primary key (there is no subject namespace), production authentication (H-02) | After the user's decision |
| Remainder of H-27 | Operational route for identity binding, entrances for LLM-based features (document extraction, NL→query, explanation text generation) | Decision on priority |
| Design gaps | Entrances for plan-diff and counterfactual What-if (conditioning not yet trained, ActionEvent not persisted = H-07), the entrance for `promote_scenario`, UI display of quarantine and scenarios, reconciliation of types 1, 3, 4, 5, write-back | After a decision |

As a cross-cutting task, there is X-05 (monthly query of dependency vulnerabilities. Last run 2026-11-08 with 0 findings; the next is due around 2026-12-08).

There are 27 items awaiting a human decision in `docs/HITL.md` (10 waiting / 8 provisional / 1 overdue / 6 decided / 1 decided and awaiting execution / 1 partially decided).

---

## 9. Recommended next steps

1. **H-01 → H-23**: rotate the keys, create the remote and push, and run CI for the first time. The biggest risk right now is that it is unconfirmed whether the local green reproduces in another environment.
2. **Decide the priority of H-27**: which comes first, identity binding or the LLM entrances.
3. **Handling of AssetShape**: whether to give the ledger a location column or loosen the shape. This lets the SHACL quarantine be applied to production input.
4. Agree on who owns the field edge and where `actions` is placed (so that What-if can be used in operation).

Verification on real data is outside the scope of this repository (ADR-0001). The arrangement for doing it in a separate repository will be decided in H-18.
