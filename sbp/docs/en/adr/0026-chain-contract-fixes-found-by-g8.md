# ADR-0026: Chain contract fixes found by G8 (promotion event type and probability, window assignment, consumer drops, calibration intercept)

- Date: 2026-11-08
- Status: Accepted
- Layers touched: bridge (lifting, training), reasoning (windows), eventlog (consumer), services (bridge-worker, scheduler-worker)

## Context

When the chain was run in G8 (ADR-0025), the contracts between stages disagreed in 6 places. None of the unit or integration tests exposed them, because each placed the stage's inputs itself.

## Decision

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | The probe has AUROC 1.0 but ECE 0.39, so it does not pass the gate | Training on weak labels (MIL) shifts the whole score, and temperature scaling can only fix the scale | In the calibration split, fit only the intercept first (by bisection on a monotone function) and then apply temperature (weights are not moved, so ranking does not change). Labels for evaluation and calibration are attached only around the claim time (the core) |
| 2 | belief / reconcile skip every promotion event from bridge | bridge emitted `PhysicalEvent/running` (snake_case), while downstream reads only `…/ent:Running` (the ontology local name) | bridge emits `PhysicalEvent/ent:<local name>` (`fluent_local_name`) |
| 3 | A stop event is counted as "evidence of running" | The event probability is "confidence that the event occurred" (≈0.5), but downstream reads it as "the probability that the fluent holds". The type carries no direction | Make the event probability "the probability that the fluent holds after the change" (`state_probability`; low for a stop). The event confidence (`LiftedEvent.belief`) is no longer carried on the event (`uncertainty_s` has a floor of one tick, so the confidence does not survive; add it to the evidence if it becomes necessary) |
| 4 | reconcile sometimes raises a discrepancy and sometimes does not | A window assigned a record to the nearest tick at the time it arrived, and dropped it if there was no tick. Records and observations arrive from different topics, so their order is not fixed | Keep records with their timestamps and assign them to ticks when building the sequence (for the same tick, the newer timestamp wins). A record more than half a tick interval earlier than the oldest tick is not assigned |
| 5 | Events are silently lost when `max_events` is small | `getmany` was called without a limit, and when returning without processing the remainder beyond the limit, only the position advanced | Make the fetch limit the remaining count |
| 6 | When there are several beliefs with the same period, the scheduler's prior is indeterminate | The "latest" was chosen by record time alone | Unify the "current belief" in `current_beliefs` (the statement with the latest valid start) |

## Consequences

- Regression tests: `test_lifting` (probability per direction), `test_windows` (arrival order), `test_consumer_budget` (drops; before the fix,
  100 out of 250), `test_probe_training` (the intercept correction converges even under saturation), `test_g8_workers`.
- Open question (human decision): the cause of belief saturating to 1.0 under history replay (H-26). It is less likely to surface in real-time operation, but it is worth
  revisiting together with the handling of late-arriving events (an event whose time goes backwards within an entity is skipped).

## Limitations (confirmed in independent review)

- Restricting the evaluation and calibration labels to around the claim time (the core) is a change that loosens the ADR-0024 gate. The roughness of the weak labels
  (item 4 of ADR-0024) was chosen so that it measures calibration error rather than label noise.
- G8 restricts belief updates to one per step, so #6 (choosing the current one from several beliefs) is not exercised in G8. It is covered by a unit test.
- G8's bridge-worker receives the entity, probe, and WM version as arguments (the entity lookup `entities_of_type` is exercised by the ingest integration test).
- The position of G8's discrepancy depends on the rule that a record falls on the nearest tick (noted in the test).
