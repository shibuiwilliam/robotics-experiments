# ADR-0019: Concept discovery is a periodic task in learning-worker, the operational entry point is only recurring discrepancies, and re-proposal is suppressed by a signal fingerprint

- Date: 2026-11-07
- Status: Accepted
- Layers touched: knowledge (reading resolved discrepancies), reasoning (signal source, suppression, proposal generation, prompt), services (learning-worker), tests (fixture)

## Context

The concept discovery of design doc §8.6 (detect -> propose -> curate) passed in G6 as a library, but **nobody runs it periodically**.
The `concept_proposals` table (M8-19, ADR-0015) and curation (learning-worker's `_apply_curation`) exist, but
nothing writes proposals to the table, so the UI's concept curation stays empty. H-10 (whether to suppress re-proposal of rejected proposals) becomes a real
harm only once periodic execution is introduced: the next cycle emits the same proposal again from the same signal.

Facts confirmed:

1. Entry (a) low-coverage clusters needs the latent, and (b) persistence of consistency residuals needs a residual metric, but **neither input exists in operation**
   (M8-24 / M8-15). Entry (c) recurring discrepancies is the only operational input (the `discrepancies` table) and is sufficient.
2. Proposals need an LLM (`sbp.llm`, the `concept_proposal` task). **The live provider is waiting on H-04**, so what an operational
   assembly can build is up to replay (fixtures required, fail if absent). Discovery is not included in the hot-path-prohibition contract
   (invariant 11), so it may use an LLM.
3. The prompt assembly and output schema exist only inside the test (`test_g6_evolution.py`).

## Options

### Question 1: Who runs it periodically

1. **Add a periodic task to learning-worker** (same shape as ADR-0017). "Execute -> expect -> verify -> learn -> evolve" closes within one responsibility, and
   no new process is added. Con: another period is added to `step()`.
2. A seventh worker. The wiring for LLM, Postgres, and Oxigraph duplicates, and the responsibility is thin.
3. scheduler-worker. The scheduler is read-centered ("choosing what to observe") and should not be mixed with the responsibility of writing proposals to a table.

### Question 2: The operational entry point

1. **(c) Recurring discrepancies only.** Pass the `(entity, type, resolution outcome)` of resolved (`Resolved` / `Learned`) discrepancies to
   `repeated_discrepancy_signals`. The signal supply is pluggable so that (a) and (b) inputs can be added when they reach operation.
2. Build all three entries and run entries with empty input as empty. This makes a non-working path look "implemented" (the shape of finding 32).

### Question 3: Suppressing re-proposal (H-10)

1. **Suppress by a signal fingerprint, before calling the LLM.** Fingerprint = `(trigger, set of entities, type)`. For a fingerprint that already has a proposal,
   suppress always if `proposed` / `accepted` / `revised`, and during a cooldown period if `rejected`. If the same signal
   appears again after the cooldown, re-propose (the situation may have changed; a human can judge again).
2. Suppress by proposal name. The LLM may give a different name to the same signal, so names cannot prevent it.
3. Do not suppress. A human would reject the same proposal every cycle, and trust in curation would drop.

### Question 4: Proposals per cycle

**Set a cap** (default 3), starting from the strongest signals. The cap protects the human effort of curation (the same idea as the cap on the frequency of questions to humans in ADR-0012).

## Decision

Question 1: learning-worker. Question 2: (c) only, with a pluggable signal source. Question 3: suppress by fingerprint (30-day cooldown). Question 4: cap of 3.

- Period `SBP_REASONING_DISCOVERY_PERIOD_S` (default 86400 seconds), cooldown `SBP_REASONING_DISCOVERY_REJECTION_COOLDOWN_DAYS`
  (default 30), cap `SBP_REASONING_DISCOVERY_MAX_PROPOSALS_PER_CYCLE` (default 3).
- **If the LLM is unavailable or fails, no proposal is created.** Leave it in the log and the counts, and do not silently count it as success. If there is no provider, count it as
  `unwired` (the shape of M8-30). A replay with no fixture is also counted as a failure.
- The output is validated by pydantic (schema) and then made a `ConceptProposal` (`PROPOSED`, with `provider / model / prompt_version` in provenance).
  **No automatic adoption** (design doc §8.6, invariant 11). A proposal with empty `rejected_alternatives` is rejected by the existing validation.

## Consequences

- **Invariant 3**: A proposal is a hypothetical work item, not an assertion (no seat in the modality lattice; ADR-0015).
- **Invariant 9**: No concept about an individual is created from cases featuring people (a prompt constraint). Latent centroids are not placed in the table (ADR-0015).
- **Invariant 11**: The LLM is used only for discovery and proposals. It is not called from the hot path (kept by the import-linter contract).
- **Configuration**: Worker count stays at 6. No alembic change. Three settings are added.
- **Tests**: Unit (fingerprint, each suppression state, cooldown, cap, LLM failure, no provider), integration (real Postgres. A resolved discrepancy ->
  one proposal; the second cycle adds nothing; a rejection is not re-proposed during cooldown but is re-proposed after it). The fixture is a hand-written replay JSON.

## Open questions

- **Operational connection of entries (a) and (b).** Add them when the latent (M8-24) and the consistency-residual metric (M8-15) reach operation.
- **The live LLM provider (H-04, M8-10).** Until then, operational discovery produces proposals only when a replay fixture exists.
- **Tracking the ontology MINOR filing after acceptance** (open question 1 of ADR-0015).
- **Per-proposal tuning of the cooldown period** (e.g. varying the cooldown by rejection reason).
