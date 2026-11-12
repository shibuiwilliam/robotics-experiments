# ADR-0011: Hold additional-observation requests in a work queue (a projection table), and assign observation means through configuration

- Date: 2026-10-04
- Status: Accepted
- Layers touched: knowledge (work queue), reasoning (registry of observation means), services (wiring of the scheduler), infra (alembic)

## Context

The worker of the epistemic scheduler (`services/scheduler_worker.py`) implements everything from converting requests to
queries through selection by utility, but the **source of requests** and the **assignment of observation means** were left
as `TODO(question)` (known constraint in `docs/PROGRESS.md`). With no source, `step()` always returns 0.

Related: design document §8.7 (epistemic scheduler), §6.1 (event envelope),
§3.5 (modality), M3-06 (resolution → learning signal; `LearningArtifacts.observation_requests` emits requests).

## Options

### Question 1: Where do `ObservationRequest`s come from?

1. **Put them in the event log** (add a `<site>.reasoning.request` topic).
   - Benefits: ordering and idempotency (`event_id`) attach. Provenance remains.
   - Drawbacks: **they do not fit in `EventEnvelope`.** The envelope carries "someone's claim" and requires `modality` +
     `belief` + `evidence`. A request for additional observation is **a command, not a claim**, and
     there is **no seat** for it in the modality lattice (`observed` / `inferred` / `predicted` / `planned` / `normative` /
     `hypothetical`). "Please measure this" is none of them. To carry it, an arbitrary
     payload would have to be added to the envelope, breaking the premise of §6.1 that "all events have the same shape".
     There is also no place to put the `reason` and `utility` that a request holds.
2. **Hold them in a projection table (work queue).**
   - Benefits: it is natural to pull "the list of requests not yet satisfied" (this is a query about state, which
     cannot be answered by replaying events). The existing `quarantine` (the isolation queue for SHACL violations) is a
     **work queue of the same shape**, and it is a precedent for directly updating `reviewed_at` / `resolution`.
   - Drawbacks: looks like a departure from the principle "state is a projection of events".
   - Cost on a MacBook: one table. The row count is proportional to the number of unresolved discrepancies, and is small.
3. Hold them in memory (the current state).
   - Drawbacks: lost on restart. The design that re-evaluates unselected requests in the next cycle (the premise of submodularity) is
     broken by restarts.

### Question 2: How are observation means (`ObservationMeans`) assigned?

1. **Hold them in a configuration file (`observation/<domain>.yaml`).**
   - Benefits: the same shape as `reliability/*.yaml` (source reliability, M1-06) and `value/*.yaml` (business value).
     Cost and confidence are **properties of the instruments a site has**, and differ by site.
   - Drawbacks: one more configuration file. One more top-level directory as well.
2. Hold them in the ontology as a correspondence of `Probe` × `FluentType`.
   - Benefits: "which concepts can be verified, and how" looks like knowledge about the world.
   - Drawbacks: **cost and confidence differ by site**, so putting them in the ontology would mean managing site differences through ontology versions:
     each time a site updates an instrument the ontology version would rise, mixing changes irrelevant to
     the re-grounding decision. Furthermore, the correspondence between probes (sensor-derived) and concepts is
     **already held by `ProbeSpec.grounds`**, so it would be double management.
3. Hard-code them in code (the current `_DEFAULT_MEANS`).
   - Drawbacks: violates the convention (CLAUDE.md) that thresholds and settings go in `Settings` or configuration files.

## Decision

- **Question 1: Option 2** (work queue). The deciding point is that "a request is not a claim":
  a shape that puts something with no seat in the modality lattice into the event log cannot be expressed on this platform.
  The principle "state is a projection of events" is a discipline about the **state of the world** (beliefs), and the work queue is not
  the state of the world. The existing `quarantine` is a precedent for this distinction.
- **Question 2: Option 1** (configuration). `observation/<domain>.yaml` holds
  `fluent IRI → means (kind / reliability / cost)`, and a default means is applied to fluents with no declaration. **The default is not
  "ask a human"**: cycles would occur in which nothing is selected because of the frequency cap, and the behavior of "automatically keep
  asking humans" is undesirable.

## Consequences

- **Invariant 2 (append-only)**: the work queue is not an assertion, so it is out of scope. However,
  **rows of requests are not deleted**: only `satisfied_at` is set, so that a history of "observation was requested but not
  satisfied" remains (the same treatment as `quarantine`).
- **Invariant 3**: the result of an observation returns by a separate path as an `observed` event. The request itself has no
  modality (because it is not an event).
- **Invariant 5**: fluent IRIs are written in the configuration file but not in code. That the configuration
  keys exist in the ontology is checked at load time.
- **Ontology version**: unchanged (0.5.0). No new concepts are placed (not placing them is part of this ADR's
  decision).
- **Tests**: unit tests for the means registry (how defaults are applied, rejection of unknown IRIs) and queue semantics
  (return only unsatisfied, do not delete rows); integration tests for round-tripping the queue against a real Postgres.
- **Layout**: `observation/` is added at the top level. The directory table in `docs/development-rules.md` §3 is updated in the same commit.

## Open questions

- **The side that writes requests is not implemented.** Requests come out of reconciliation resolution (`apply_resolution`), but resolution is a human
  judgment, and the worker that receives that input path and executes it is M8-08. This ADR decides only up to the shape of the queue and
  the read side (the scheduler); **M8-08 will connect the write side**.
- **Determining that a request has been satisfied.** The process that matches which request an incoming `observed` event satisfied
  is not implemented. The `mark_satisfied` API will be provided, but the caller is undecided
  (the natural way is to judge by overlap of entity × fluent × interval, but "how much measurement counts as satisfied" differs by
  means).
- **Fluents with multiple means.** Currently one means per fluent. Choosing among multiple means
  (e.g., try in order of cheapness) would add a means dimension to the scheduler's selection and needs a separate design.
