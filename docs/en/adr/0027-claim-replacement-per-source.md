# ADR-0027: Repeated claims from the same source are replaced (H-26)

- Date: 2026-11-08
- Status: Accepted
- Layers touched: reasoning (belief, ingest)

## Context

When history was streamed at once in G8 (ADR-0025), belief saturated to 1.0 (H-26). There were two suspects: (a) skipping events whose time goes backwards within an entity, and
(b) adding repeated claims from the same source as independent evidence.

A reproduction test (`tests/unit/reasoning/test_claim_saturation.py`) confirmed that **(b) is the cause**. The business system records the same state every period.
A claim does not advance time (no transition is applied) and adds its log-odds to the previous posterior, so with 300 "running" claims the belief sticks at 1 − 10⁻⁹,
and it does not come back even when a dozen or so later "stopped" claims arrive. Observations (promotion events) exist only at state changes, tens of times fewer than claims, so they are buried.
**(a) is not occurring**: in the real chain (G8 history replay), `OUT_OF_ORDER` is 0.

## Decision

1. **A claim from the same source (`RecordEvidence.source`) replaces the previous one.** For each entity × fluent, hold per source "the log-odds of the claim
   currently contained in the belief", and subtract the previous amount before adding a new claim (`update_belief(superseded_claim_log_odds=…)`).
   A business record repeats the same state, and errors are repeated too — they are not conditionally independent, so the count is not evidence.
2. **Keep the held amount even when an observation arrives.** Emptying it would let claims pile up again in an operation where observations and claims alternate (independent review).
   Since the posterior is a sum of log-odds, even after an observation the previous claim's amount can be subtracted and the new claim added.
3. **Different sources are counted as different evidence** (one per source). A claim with no source is treated as a single anonymous source.
3b. **An older claim from the same source (`tx_time` earlier than the held amount's time) is skipped** (`SkipReason.STALE_CLAIM`). A late-arriving old claim does not overwrite the latest.
4. Undoing a failed write (`undo`) also restores the held claim amount.
5. **(a) is left unchanged**: an observation whose time went backwards is skipped and counted as `SkipReason.OUT_OF_ORDER`. We will decide on reordering within a window after observing how often it occurs (option 1 of H-26).

## Consequences

- No matter how many claims continue, the posterior is the same as for a single claim. If the final claim says the opposite, it reverts regardless of the count.
- In the history-replay stage of G8 (the rest consumed in full with the same consumer group), we verify 0 `OUT_OF_ORDER` and landing on the final state.
- Migration: stored beliefs do not change (only the filter's state changes). After a restart the held amount is empty, and the first claim is added as one claim's worth.

## Limitations

- Replacement is an approximation that "subtracts the previous claim's amount in full". If an observation transition (decay with dwell time) intervenes, the amount remaining in the belief and the amount subtracted diverge.
  Also, when the posterior hits the log-odds clamp (±20), the value before subtraction is smaller than the actual sum, and replacement by an opposing claim drops too far. The practical harm is small but it is not exact.
- The source is the first entry of `RecordEvidence.source`, and its granularity depends on the ingest configuration (if made as fine as the record ID, replacement has no effect. Currently it is the source system's IRI).
- Even if the same source truly performs an independent re-measurement, it is counted as only one claim (the conservative side). Giving the reliability model the independence of re-measurements is an open question.
- G8's script ends with a stopped interval, and the replay stage passes even before the fix (measured). Discriminating saturation is handled by the unit test, and the integration test verifies 0 `OUT_OF_ORDER` and landing on the final side.
- The record likelihood of design document §8.1 remains an "additive" model, and only repeats from the same source are replaced. Adding this to the design document waits for human confirmation, as with X-15 (this ADR is authoritative).
