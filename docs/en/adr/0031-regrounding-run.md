# ADR-0031: Re-grounding execution is a job that "replays from the beginning and compares" (M9-01)

- Date: 2026-11-08
- Status: Accepted
- Layers touched: reasoning (`regrounding_run`), scripts (`reground`)

## Context

`sbp.knowledge.regrounding` held only the plan and the diff report, and had no execution (`run(plan)`) (finding 110). The G7 integration test
wrote "the probability per probe version" directly into assertions in the test itself, but the belief in operation is **the posterior obtained when the belief-worker passes the events promoted by the bridge-worker through the
filter**, not the probe's output itself. Re-grounding on real data amounts to rebuilding that chain with the new version.

## Decision

1. **Execution replays from the beginning using operational components.** Latent → probe (`ProbeSet`) → `lift_events` → `BeliefIngest` (business claims are mixed in by time order).
   `sbp.reasoning.regrounding_run.reground` replays the plan's range and compares. `knowledge` cannot import `bridge` / `reasoning`, so
   execution is placed in `reasoning`.
2. **Dry-run by default.** It writes only with `--apply`. The write order is "add the new statements → close the `tx_to` of the old statements" (belief does not vanish even if interrupted; duplicates are repaired by re-running).
3. **Replay with segmentation close to live.** The bridge-worker promotes `batch_size` at a time. The replay also promotes `batch_size` at a time from the start, but live segmentation varies with start time, restarts, and watermark restoration, so **a match cannot be guaranteed** (change points at the boundaries can differ). The diff is an approximation that includes both version differences and segmentation differences, and a human reads the report to decide.
4. **To carry over filter state, replay from the entity's first latent, and write only the plan's period.**
5. **Matching is by `(predicate, valid_from)`.** If they match and the confidence is the same (1e-9), leave it untouched (re-running changes nothing). Something present in the replay but not in the old statements is "new";
   something present in the old statements but not in the replay is "vanished". The old statements compared are only those the belief-worker wrote from observations and claims (provenance `<prefix>/…`). Human corrections, coupling statements (`/coupling/`), and
   provenance with similar names are not closed.
6. The job writes `regrounding_duration` (seconds) to `metrics`.

## Consequences

- For a simple series with all points present (single batch, no coupling), replaying with the same probe yields 0 diffs (unit test). In operation with different live segmentation it will not be 0 (item 3 above).
- The plan's priors (`GraphPlans`) are injected by the script in the same way as live. Even when multiple fluents switch at the same time, each is replaced (reproduction test for a review finding).
- The old belief can be retrieved with `as_of`, and the new belief with the new `tx` (integration test on real Oxigraph / Postgres / S3 / Redpanda).

## Limitations

- Claims are read from the beginning of the business topic in the event log. Periods beyond the retention period are replayed without claims and show up in the diff.
- Latents are read in pages from the entity's start to the plan's end (right-open), up to 1 million points per entity (if exceeded, move `--end` earlier). The series of all entities is held in memory.
- **Avoid `--apply` while the belief-worker is running.** Statements written live between the read and the close can be closed as "vanished". The script refuses `--apply` whose end extends into the future.
  There is no mechanism to prevent two concurrent `reground` runs (statements are added twice; re-running closes the duplicates). Coupling statements and contribution records (`ContributionRecord`) are not replayed, so the explanation of the new statements says "no contribution recorded".
- Claims are considered fully read after idle silence (default 3 seconds). If the broker is slow, claims are missed and "vanished" increases.
- The provenance of the new statements is `<belief-worker's provenance>/regrounding/<plan_id>`, and carries no original event ID (replayed events are not sent to the log).
  Explanations are traced from the plan (reason, versions).
- Belief coupling (loopy BP, `_couple`) and reconciliation (re-raising discrepancies) are not replayed. Only the belief posterior is swapped. Discrepancies after the swap are picked up by the reconcile-worker in its
  next cycle. The report fields for new and vanished discrepancies remain empty (M9 onward).
- When a WM version promotion changes the latent dimension, migration to the table of the new dimension comes first (`make encode`).
