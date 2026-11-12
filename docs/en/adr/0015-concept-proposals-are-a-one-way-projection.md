# ADR-0015: The concept-proposal work-item table is a one-way projection and does not hold latent centroids

- Status: Accepted
- Date: 2026-10-04
- Related: ADR-0013 (discrepancy work-item table), ADR-0012 (learning-worker applies human decisions), design doc §8.6 (concept discovery), §11.2 (curation), M8-19

## Context

The UI has a concept curation screen, but **it has nowhere to read from**. It reads a nonexistent topic
(`central.reasoning.concept`), and its types were also different from the backend's (finding 85).

Discrepancies (ADR-0013) and expectations (M8-18) were resolved with a work-item table + GraphQL resource. Concept proposals get the same shape,
but **one circumstance differs**: `ConceptProposal.signals[].cluster` holds a
`LatentCluster` (which has `centroid: NDArray[np.float64]`).

Furthermore, **the material the UI uses to decide is not in the backend type**. The UI inspects a proposal by "the number of supporting samples" and "the coverage by existing concepts"
(`checkProposal`), and both live inside `LatentCluster` (`size` and
`mean_probe_activation`).

## Decision

1. **Do not put latent centroids in the table.** `cluster` is dropped from the payload.
2. **Derive and keep the two scalars needed for judgment.** `support_count` (= the total number of constituent samples) and
   `coverage` (= the mean probe activation) are **computed at write time** and put in the payload.
3. **State explicitly that reading back does not restore the cluster.** `from_record` is provided, but
   the signals' `cluster` is always `None`. This is pinned by a test.
4. **Curation advances the state.** learning-worker's `_apply_curation` updates the table's `status`;
   until now it only recorded, so even after an accept/reject decision was entered the proposal stayed `proposed`.

## Rationale

### Dropping the centroid

- **Latent vectors belong to the latent index.** Putting them in the work-item table leaves the same value in two places, and
  one goes stale on re-grounding. The index is invalidated by WM version, but this table is not.
- **Nobody reads them back.** Curation is work where a human reads and decides, and does not look at centroids. `_apply_curation`
  also does not use the proposal body.
- Invariant 9 **permits** persisting latents (what it forbids is raw video and audio). So we do not put them here not "because it is forbidden" but
  because "the owner is elsewhere".

### Keeping the derived scalars

- **The UI's checks would stop working.** "Fewer than 10 samples cannot be distinguished from a chance cluster" and "if coverage by existing concepts
  exceeds 50%, adjusting existing concepts suffices" are judgments from design doc §11.2, and discarding them would turn curation into
  "decide by reading the name and definition".
- **They cannot be computed on read.** Since the centroid is dropped, the reader does not know the sample count.
- **Treat them the same as projection columns** (the lesson of finding 90): derived values are computed once at write time and
  not recomputed on read.

### Do not hide that it is one-way

In M8-18 we created "a column and a property with the same name but different values" (finding 90). To avoid the same failure,
**we write in the function docstring and in tests that it does not round-trip**. We make it readable that `from_record` returning `cluster=None` is
a decision, not a bug.

## Options

### A. Drop the centroid and keep derived scalars (adopted)

As above. Con: the round trip is not complete. Mitigation: pin it in tests and write it in the docstring.

### B. Put the centroid in the payload as a JSON array

Pros: Complete round trip.
Cons: **Rejected.** (1) It duplicates the latent index (they diverge on re-grounding). (2) The payload grows with dimension
(64 dimensions x float = several KB per proposal). (3) No reader uses it.

### C. Do not put it in a table; carry proposals in the event log

**Rejected.** Same reason as ADR-0010 / ADR-0011: `EventEnvelope` has no place for the whole proposal.
A proposal is a work item, not a claim (no seat in the modality lattice).

### D. Change `ConceptProposal` to pydantic

Pros: Serialization becomes automatic and read validation is aligned.
Cons: **Not doing it this time.** Migrating while carrying `__post_init__` validation, `curate()`, and `LatentCluster`'s numpy
is a separate change and exceeds the scope of M8-19. `ConceptProposalRecord` (the table row) will be
pydantic, so **the shape the API returns is validated**. Validation of the proposal body is done by `__post_init__`
(which always runs on read-back).

## Consequences

### What gets better

- The UI's concept curation reads a resource that actually exists (all four views become GraphQL).
- **Curation advances the proposal's state.** A proposal for which an accept/reject decision was entered disappears from the queue; until now
  `_apply_curation` only recorded, and the same proposal stayed on screen forever.
- The material for judgment (sample count and coverage) remains in the table and can later be audited at the values from when it was proposed.

### What gets worse, and the remaining gaps

- **There is no writer yet.** Running concept discovery periodically is M8-09, and that is what will write to this table.
  At M8-19 the table stays empty, but **empty and "cannot read" are distinguished in the UI** (finding 32).
- **The round trip is incomplete** (above).
- **The derived scalars are fixed at their write-time values.** Even if probes are later added and coverage changes, the proposal's
  row is not updated. This is **correct** (it keeps the judgment material from when it was proposed), but an operation that wants to know the "current coverage" needs a
  separate query.

## Impact on invariants

- Invariant 9 (persisting latents): Not touched. This is a decision **not to place** the centroid, not a workaround of the prohibition.
- Invariant 2 (append-only): The table is a work item, not assertions. State is updated with UPDATE
  (same as ADR-0013).
- Invariant 11 (do not call an LLM from the hot path): Proposals originate from an LLM, but curation is a human decision, and
  no LLM is involved in reading or writing the table.

## Ontology version

No change (0.5.0). **Accepting** a concept files an ontology MINOR, but that is manual work by a human
(the M6-04 decision).

## Open questions

1. **Whether to track filing after acceptance.** Currently the platform does not know whether a proposal that became `ACCEPTED` was actually
   entered into `ontology/`. Tracking would require an "accepted but not implemented" state.
2. **Whether to suppress re-proposal of rejections.** The reason remains in `curation_note`, but there is no mechanism to stop the next discovery cycle from emitting the same proposal
   from the same cluster.
