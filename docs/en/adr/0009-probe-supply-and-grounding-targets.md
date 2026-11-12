# ADR-0009: Load probes from a store by pinned version, and look up the entities to ground from the knowledge graph

- Date: 2026-10-04
- Status: Accepted
- Layers touched: bridge (probe set), knowledge (artifact store and entity queries), services (wiring of the grounding worker)

## Context

The grounding worker (`services/bridge_worker.py`) was implemented in M7, but two points, the **source of probes** and
the **list of entities to ground**, were left as `TODO(question)` (`docs/PROGRESS.md`,
"Known constraints and unstarted items"). Until these are decided, the worker starts but `step()` always
returns 0, a state of "silently doing nothing".

Related: design document §5.2 (probe registration metadata and adoption criteria), §4.8 (version management and the ban on `latest`),
§6.2 (placement of latents in layers), §8.8 (as-of reconstruction). As a premise, M8-01 created probe serialization
(`sbp.bridge.serialization`) and the artifact store (`sbp.knowledge.artifacts`).

## Options

### Question 1: Where do probe weights come from?

1. **Load them from a store (S3 / local FS) by pinned version.**
   - Benefits: the `probe_id` recorded in provenance (which includes the version) and the loaded weights correspond one to one. The as-of reconstruction of design document §8.8
     holds: when asked to explain a past belief, the weights of that version remain in the store.
     The worker is deterministic (same version → same output).
   - Drawbacks: the procedure for putting artifacts into the store (train → evaluate → promote → save) is needed first.
   - Cost on a MacBook: loading is one JSON file (a few KB for a linear probe). Negligible.
2. Train at process startup.
   - Benefits: no need to operate a store.
   - Drawbacks: **conflicts with invariant 10.** Training needs labels, and the ground truth of the synthetic world cannot be read from outside `sbp.testing`
   (the import-linter contract covers `services` as well). There is a way to train with weak labels (`TimeBag`), but that is a training pipeline
   that takes business records and the reliability model as input, and is not the grounding worker's job. Furthermore, **the weights would differ per process**, so the weights
   that a `probe_id` points to would no longer be unique, and the point of banning `latest` would be lost (design document §4.8).
   - Cost on a MacBook: startup becomes slower by several seconds to tens of seconds every time.
3. Write the weights in configuration (`.env` / YAML).
   - Benefits: no store or training needed.
   - Drawbacks: writing weights of tens to hundreds of dimensions in configuration is not operable. No version management or provenance attaches.

### Question 2: Where does the list of entities to ground come from?

1. **Query the knowledge graph by type.**
   - Benefits: it reads the equipment registry itself, so when equipment increases, it automatically becomes a target.
   - Drawbacks: the grounding worker gains a dependency on the graph store (one more connectivity check). Narrowing by site relies on the IRI convention
     (`/site/<site>/...`), which is weak.
   - Cost on a MacBook: one query at startup.
2. Write the list in configuration.
   - Benefits: no added dependencies. Easy to write a canary (only some equipment).
   - Drawbacks: **equipment for which the configuration was forgotten to be updated when equipment increased is silently not grounded.** No discrepancy or belief
     is produced, so there is no clue to notice (the same failure shape as `docs/PROGRESS.md` finding 32).

## Decision

- **Question 1: Option 1** (load from the store by pinned version). The deciding reason is invariant 10:
  the worker cannot read ground truth, so it cannot train in the first place. This is not "a design preference" but a case where
  "an invariant narrowed the options to one" (the "invariants come first" item in the handover notes of `docs/PROGRESS.md`).
- **Question 2: Option 1 as the default, with Option 2 retained as an override.** `BridgeWorker` keeps accepting
  `entities` explicitly (for canaries and tests), and `build_worker()` queries the knowledge graph when it is unspecified.
  Canary promotion (design document §4.8) is done for "some sites and some concepts", so the explicit path must be retained.

Only **`active` probes** are run (`sbp.bridge.runner.ProbeSet`). `candidate` probes are subject to
shadow evaluation, and their output does not enter the primary belief (design document §5.2). **If the number of probes to run is 0, construction fails**:
an empty set looks the same as "nothing is happening" and cannot be distinguished from grounding having stopped (finding 32).

## Consequences

- **Invariant 7**: `ProbeSet` goes through `LinearProbe.predict`, so it returns only calibrated values.
  An `active` probe lacking a calibrator is not admitted into the store as of M8-01 (rejected on both save and load).
- **Invariant 10**: because the worker does not train, no additional path touches the oracle boundary.
- **Invariant 5**: the type IRI of the grounding target is passed from `sbp.core.vocab` (the argument of `entities_of_type`).
- **Ontology version**: unchanged (0.5.0). No new concepts are introduced. Re-grounding is not needed.
- **Tests**: unit tests for `ProbeSet` (status, mixing of latent spaces, duplicates, empty set),
  unit tests for `entities_of_type` (embedded pyoxigraph, subclass expansion, site narrowing),
  and an integration test covering "save to the store → load in the equivalent of a separate process → ground → event".
- **Operations**: a procedure for putting probe artifacts into the store is needed (an ingestion script in `scripts/`.
  M8-02 builds only the path up to assembling a `ProbeSet`, and leaves ingestion as the training side's job).

## Open questions

- **Default type of grounding targets.** `vocab.core.Asset` is the default, but how far down the types to ground
  is a domain judgment (e.g., whether to include carriers `carrier`). It is made passable through configuration (`SBP_BRIDGE_GROUND_TYPES`),
  with the default set to `Asset`.
- **Narrowing sites by the IRI convention.** It would be more correct to introduce a predicate representing the site (`sbp:atSite`) and narrow with
  `?entity sbp:atSite <site>`, but that would be an ontology MINOR change.
  Narrowing by convention is weakly grounded, so the count is logged so that a count of 0 can be noticed.
- **When multiple WM versions run simultaneously.** `ProbeSet` is bound to one latent space, so to run two versions in parallel
  during a canary period, one would need two sets and to read the latent index split by version as well.
  The current worker looks at only one version.
