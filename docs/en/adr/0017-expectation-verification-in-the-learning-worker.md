# ADR-0017: Expectation verification is a periodic task in learning-worker, verifies only expired expectations, and looks up record existence from a projection table

- Date: 2026-11-07
- Status: Accepted
- Layers touched: knowledge (projection of record existence, reading expired expectations), reasoning (pure part of verification), services (learning-worker, ingest-worker), infra (alembic 0011)

## Context

Design doc §8.5 defines "action execution -> expectation generation -> verification by the deadline -> learning". Through M8-21,
"approval -> execution -> writing the expectation" is in operation, but there is no operational code that calls `verify_expectation` (M5-04), and
expectations stay `Active` and do not move even past their deadline (`docs/PROGRESS.md` finding 103).

Facts confirmed before design (finding 118):

1. `verify_expectation` turns an expectation into `Undetermined` **even before the deadline** if there is no observation (`Expectation.verify` yields
   `Undetermined` if any surface is left with a `None` verdict). Unlike `expire` for expired ones, it does not look at the deadline. If a periodic job
   naively loops over all `Active`, "cannot be verified yet" gets recorded as "undeterminable".
2. Verification of the symbolic side (business effects) needs `records_present[record type IRI]`, but **there is no place in operation to look up "does this entity
   have a record of this type"**. Ingestion (ADR-0016) only emits existence events, and records other than the register are not persisted.
3. For the latent side (physical effects), `current_beliefs` can be used (the belief-worker's `inferred`).
4. `ExpectationStore.upsert` rejects state regression, and `ObservationQueue.enqueue` is idempotent on
   `(discrepancy_id, fluent)`, so re-running the verification pass does not double up.

## Options

### Question 1: Who runs verification

1. **Add a periodic task to learning-worker.**
   - Pros: The writer of expectations, the reader of beliefs, and the writer of the observation-request queue are already here (ADR-0012 / 0014).
     "Execute -> expect -> verify -> learn" closes within one responsibility, and no new process or dependency is added.
   - Cons: `step()` has two kinds of period (the decision queue and verification).
2. A seventh worker (expectation-worker). The wiring (Postgres, Oxigraph, queue) mostly duplicates, and
   the responsibility is thin relative to the added worker count.
3. scheduler-worker. The scheduler has a read-centered responsibility of "choosing what to observe", and mixing in the responsibility of writing expectation state
   would break the separation of ADR-0011.

### Question 2: When to verify

1. **Only `Active` expectations past the deadline (`Expectation.deadline` = the deadline of the latest effect).**
   Wait even if observations arrive early. Beliefs can move later, so an early verdict cannot be retracted (`Fulfilled` is close to terminal).
   The misrecording of finding 118-1 also does not occur.
2. Verify before the deadline once observations are in. Learns sooner, but cannot undo the verdict if beliefs later change. Deferred to a future extension.

### Question 3: Where to look up record existence

1. **A projection table `business_records` (alembic 0011).** ingest-worker appends
   `(site, entity, record_type, external_id, revision, recorded_at)` for each record (`ON CONFLICT DO NOTHING`,
   after sending and before the cursor). Verification looks up "is there a record whose `recorded_at` is at or after the expectation's creation time".
   - It is an append-only projection, not a truth flag (the business system remains the system of record).
2. Re-read the business topic. It depends on the retention period and becomes a full scan on every verification.
3. Hold records as assertions in the knowledge graph. This would overturn ADR-0016's decision that "records are events; only the register and plans are assertions".

### Question 4: How to handle the result

| Result | Handling |
|---|---|
| `Fulfilled` / `Violated` / `Undetermined` | Advance the state with `ExpectationStore.upsert` (no regression) |
| `Undetermined` (expired) | Enqueue an observation request for each **physical effect** that could not be observed (`discrepancy_id = "expectation:<id>"`). A record type cannot be made an observation request (it is not a fluent) |
| `Violated` | If the symbolic side missed -> file a type 1 (existence) `Discrepancy`; if the latent side missed -> type 2 (state) (`detector="expectation"`, the expectation's ID as evidence). Whether to suspect the WM or the process ontology is left in the expectation's payload by `VerificationResult.blames_*` |

## Decision

Question 1: learning-worker. Question 2: expired only. Question 3: the `business_records` projection. Question 4: the table above.

## Consequences

- **Invariant 2**: Expectations only write state progression. `business_records` is append-only. Assertions are not touched.
- **Invariants 3 and 6**: Verification only reads beliefs (`inferred`) and record existence, and creates no modality. It is not a path that feeds
  an audited record into inference (expectation verification is not a discrepancy audit but a confirmation of an action's result).
- **Invariant 11**: No LLM is used.
- **Configuration**: Worker count stays at 6. alembic 10 -> 11. One setting is added (verification period).
- **Tests**: Unit (do not touch before the deadline; expired with no observation -> `Undetermined`; symbolic-only miss -> type 1;
  latent-only miss -> type 2; observation requests target physical effects only), integration (real Postgres / Oxigraph. Three cases: fulfilled, violated, and
  no observation. A second cycle changes nothing).

## Open questions

- **Early verification before the deadline** (Question 2, option 2).
- **Transition to `Learned`.** Turning the verification result into a learning signal (WM retraining, revisiting T) and moving to `Learned` is a separate task.
- **Retention period for `business_records`.** It is append-only and grows monotonically. Verification only needs rows at or after the expectation's creation time, so
  deleting old rows (a retention window) is to be decided operationally.
- **Filing to norms (type 5).** How to handle the case where an expectation includes a norm violation.
