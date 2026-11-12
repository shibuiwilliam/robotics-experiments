# ADR-0013: Keep discrepancies as work items in a table, and leave events as notifications

- Date: 2026-10-04
- Status: Accepted
- Layers touched: knowledge (discrepancy table), api (GraphQL resource), services (reconcile worker writes, learning-worker reads), infra (alembic)

## Context

In M8-04 we made it possible to emit the filing of a discrepancy **as an event**, and what we learned then is that
**`EventEnvelope` has no place to put the whole discrepancy** (`docs/PROGRESS.md` finding 62).
The envelope requires `modality` + `belief` + `evidence`, and has no fields for the supporting assertions, the state history,
the severity breakdown, the resolution, or the learning signal.

As a result:

- The UI (M7-04) has the type and sorting logic for the discrepancy queue, but **has nowhere to read from**
  (`fetchDiscrepancies` reads `/stream/{topic}` and assumes each event's `payload` is a
  `Discrepancy` itself; no real envelope has that shape).
- `learning-worker` (M8-08) cannot read the discrepancy body, so it works around this by **reconstructing a minimal discrepancy
  from the decision row** (it has no evidence, so no record-correction proposal is produced).
- There is no path to look up "which discrepancies are unresolved".

Related: design doc §8.2 (discrepancy lifecycle and the five types), §6.1 (event envelope),
§6.8 (projections), ADR-0010 (filing events), ADR-0011 (precedent for the work queue), ADR-0012 (decision queue).

## Options

### Question 1: Where to keep the discrepancy body

1. **Keep it in a projection table (work items).**
   - Pros: Queries like "unresolved ones in severity order" and "the history of this discrepancy" are straightforward. It is a
     work item with a state machine, and has the same shape as `quarantine` / `observation_requests` / `learning_decisions`.
     This is the shape the UI reads.
   - Cons: It looks like a departure from the "state is a projection of events" principle (and, as described below, it is one).
   - Cost on a MacBook: one table. The row count equals the number of unresolved discrepancies, which is small.
2. Write it to the knowledge graph as assertions.
   - Pros: `sbp-core:Discrepancy` is an ontology concept and maps naturally onto RDF. Bitemporality and provenance
     attach as-is.
   - Cons: **Queries as work items are heavy.** Writing "50 unassigned, in descending severity" in SPARQL
     means gathering the severity breakdown (score, value, confidence, persistence) from separate triples and sorting.
     The state-transition history also becomes a sequence of triples, which the UI must reconstruct on every read.
     Moreover, **the bitemporal semantics do not fit**: a discrepancy's state is "who holds it right now",
     not "when it was so in reality".
3. Keep it in the event log only, and let subscribers fold it themselves.
   - Cons: **The whole does not fit in the envelope** (finding 62). Fitting it would mean adding an arbitrary payload,
     which breaks the premise of §6.1 that "all events have the same shape".

### Question 2: Is the table a pure projection

**It cannot be a pure projection.** For "state is a projection of events" to hold, state must be reconstructible
from events. But the filing event is a notification, not the discrepancy itself
(same reason as Option 3 of Question 1). Therefore:

- **The reconcile worker writes to the table directly** (it is the only one that holds the body),
- **Events remain notifications** (subscribers learn that it "was filed" and look up the details in the table).

This is an exception to the discipline, so **we record why it is an exception in the table comment and in this ADR**.

### Question 3: Expand into columns or keep as jsonb

1. **Expand only the columns used for queries, and keep the body as jsonb.**
   - Pros: `Discrepancy` (pydantic) is the contract and can be validated on read. No migration is needed
     when fields are added to the model.
   - Cons: The contents of jsonb are awkward to handle from SQL (but only the expanded columns need to be handled).
2. Expand all fields into columns.
   - Cons: `Discrepancy` has nesting (`severity` / `resolution` / `learning` / `suppression` /
     `history`), and flattening it exceeds 20 columns. **Every model change becomes a migration**,
     making additive changes (additions to Appendix A.2) heavy.

## Decision

- **Question 1: Option 1** (projection table). The `discrepancies` table (alembic 0006).
- **Question 2**: It is not a pure projection. The reconcile worker writes, and events remain notifications. The reason is
  recorded in the table comment.
- **Question 3: Option 1**: query columns (`site` / `status` / `kind` / `severity_score` /
  `detected_at` / `subjects`) + the body in `payload jsonb`. On read it is validated into `Discrepancy`.

**Reject state regression.** Writes are assumed to arrive only as the result of passing through `Discrepancy.transition()`,
but the table side also rejects "an update that moves a row from a terminal state back to a non-terminal one". Multiple paths (the reconcile worker and
learning-worker) touch the same row, so an accident where one overwrites with a stale state is possible.

**The API is a GraphQL resource.** The UI reads beliefs over GraphQL, so discrepancies are aligned onto the same
path. The current UI, which reads `/stream/{topic}`, **was written on the premise that the event contains the whole discrepancy,
and that premise does not hold**, so the UI side must be replaced (split out as M8-16; doing the API and UI
on the same day would exceed the granularity).

## Consequences

- **Invariant 2 (append-only)**: The table is not assertions. However, **history is not overwritten**:
  `history` is an append-only list held by `Discrepancy`, to which `transition()` appends.
- **Invariant 3**: The table has no modality (it is a work item, not a claim). The beliefs a discrepancy
  rests on are referenced by `assertions` IRIs and remain on the belief graph side.
- **Invariant 6 (audit isolation)**: The table is not an input to inference (the UI and learning-worker only read it).
  It is not subject to `AuditMask`.
- **Ontology version**: No change (0.5.0). `sbp-core:Discrepancy` already exists.
- **Resolving the M8-08 workaround**: Since `learning-worker` can now read the body, reconstruction
  (`reconstruct_discrepancy`) is **demoted to a fallback for when the body is absent**. When a discrepancy with evidence can
  be read, a record-correction proposal is produced.
- **Tests**: Unit tests for rejecting state regression and for validation; integration tests for the round trip to a real Postgres and for the chain
  "reconcile worker writes -> learning-worker reads".

## Open questions

- **Replacing the UI** (M8-16). Point `fetchDiscrepancies` at GraphQL and build a path that
  looks up beliefs from evidence IRIs. The current UI type (`observedEvidence: Belief[]`) **assumes beliefs
  are embedded in the event**, which does not hold.
- **A path to look up beliefs from evidence IRIs**. `AssertionStore.query` filters by subject and predicate, but
  cannot look up by assertion ID. For the UI to juxtapose evidence, an ID lookup is needed (to be decided in M8-16).
- **Cleaning up old discrepancies**. The practice of dropping rows that became `Learned` / `Suppressed` (partitioning,
  archiving) is undecided. The row count is proportional to the number of unresolved ones, so this is not a problem for now.
