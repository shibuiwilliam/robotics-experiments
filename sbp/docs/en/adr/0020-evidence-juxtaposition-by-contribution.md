# ADR-0020: Three-source evidence juxtaposition uses "contribution" as the unit, is looked up by entity x fluent x interval, and keeps contributions in a projection table

- Date: 2026-11-07
- Status: Accepted
- Layers touched: knowledge (projection of contributions), services (belief-worker), api (GraphQL resource), apps/ui, infra (alembic 0013)

## Context

Design doc §8.2 and §10.3 require showing the three sources (observation, record, plan) side by side when a human judges a discrepancy or a belief.
In M8-26 the operational belief update became three-source. The UI's discrepancy queue stops at a list of reference IRIs and displays on screen that "the three-source juxtaposition
cannot be built yet" (finding 84). Finding 84 and the TASK identified what is missing as (a) an API that looks up beliefs by assertion ID and
(b) a path that determines the source from the provenance `contrib`.

On checking before starting, **two premises were wrong** (finding 125):

1. **Contributions (`contrib`) are not persisted anywhere.** `BeliefUpdate.contribution` is used only for metrics, and
   an assertion's provenance is `provenance: str` (a bundle IRI); contributions remain in neither Oxigraph nor Postgres.
   The explanation service (`reasoning/explain.py`) produced contributions by recomputation. There is no path that "reads the source from provenance".
2. **A discrepancy's `assertions` are not assertion IDs.** Reconciliation rules put in `assertions=(single.fluent,)` (a fluent type) or
   an event type. Even if we built an ID-lookup API, there is nothing to look up with it. What can be looked up is **entity x fluent x interval**.

## Options

### Question 1: Where to keep contributions

1. **A projection table `belief_contributions` (Postgres, append-only).** When the belief-worker writes a belief,
   it writes the three sources' contributions keyed by `assertion_id`. The assertion body is not touched.
2. Expand the assertion's provenance bundle into a PROV graph (Oxigraph) and hold contributions there. This is the direction of design doc §6.4, but
   it adds several triples per statement and also requires designing the bundling (the evidence set is held by reference). Reading for juxtaposition is
   a time-range fetch, so a table is more natural than SPARQL. Leave room for future expansion.
3. Recompute on every read (the explanation service's shape). Recomputation must re-fetch all inputs (the records and observations of the time), and
   record claims are not persisted. The values at the time cannot be reproduced.

### Question 2: The unit and key of juxtaposition

1. **The unit is the contribution, the key is (entity, fluent, interval).** From the discrepancy's `subjects` x the fluent of `assertions` x `interval`,
   line up the time series of `inferred` beliefs valid in the interval and each point's contributions. For plans, line up `planned` statements as is.
2. Split into three columns by modality. Does not work (the three sources do not split by modality. Finding 84: business-record-derived ones also pass through inference and become `inferred`).
3. Lookup by assertion ID. There is nothing to look up with.

### Question 3: Write order

**Contribution -> assertion.** The reverse can leave statements that exist but whose source is unknown. An orphan where only the contribution remains is harmless
(the read looks at the contribution time series, not the presence of the assertion, so to keep orphans from appearing as points, the read
cross-checks against the `AssertionStore` beliefs).

## Decision

Question 1: a projection table. Question 2: contribution as the unit, entity x fluent x interval. Question 3: contribution -> assertion.

- Table `belief_contributions`: `assertion_id` (primary key) / site / entity / fluent IRI / time / prior / posterior /
  observation contribution / record contribution / plan contribution / originating event ID / number of excluded records.
- The API is a new GraphQL resource `beliefEvidence(entity, fluent, from, to)`. It returns belief points (probability, contributions, dominant source, originating
  event) and the interval's plans. ABAC goes through the same path as existing beliefs.
- The UI shows three columns (observation / record / plan) + posterior, opened from a discrepancy row. **It does not indicate which is correct.** A source with 0 contribution is
  explicitly labeled "no contribution" (so a blank is not misread as "no evidence"; finding 32), and past beliefs whose contributions were not recorded
  show "contribution not recorded". Beliefs written by the coupling (`_couple`) carry no contributions and are displayed separately as "coupling".

## Consequences

- **Invariants 1 and 2**: The assertion body is not changed. Contributions are an append-only projection, not a truth flag (a read-only explanation).
- **Invariant 6**: The number of excluded records is kept (so one can later confirm that audit isolation took effect).
- **Invariant 7**: A contribution is the absolute value of the log-likelihood ratio (raw magnitude), not a `belief`. The UI normalizes it for display.
- **Configuration**: alembic 12 -> 13. Worker count stays at 6. The belief-worker writes to Postgres (it is already connected via the idempotency store and metrics).
- **Tests**: Unit (write order, coupling beliefs are not written, unwired reporting, the UI's pure functions), integration (belief-worker ->
  table -> API returns the three sources' contributions).

## Open questions

- **Extending to a belief view (per-entity three sources + a posterior timeline, an as-of slider).** The API can be reused.
- **Backfilling contributions for past beliefs.** Beliefs before this change have no contributions (shown as "not recorded").
- **Persisting the record claims themselves.** Ingestion's `business_records` holds only existence. The claim probability remains as a contribution (record contribution), but
  individual claims do not remain.
- **Expansion into the PROV graph** (Question 1, option 2). Integrate it when implementing bundle-level provenance (design doc §6.4).
