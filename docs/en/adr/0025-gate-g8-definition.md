# ADR-0025: Gate G8 is green when "only the outside world is simulated and the inside of the platform runs through operational code"

- Date: 2026-11-08
- Status: Accepted
- Layers touched: tests (`tests/integration/test_g8_workers.py`). Concretizes the decision of HITL H-22 (option A)

## Context

The literal wording of G8 was "run the 4 workers on real services, and each `step()` returns non-zero". If the test is allowed to place
the inputs directly, each worker runs in isolation (a repeat of G5's "it was the test code that drove it to completion"; finding 89). In H-22 a human chose
**option A (simulate only the outside world; run the inside through operational code)**. M8-24 (the head of the inference path) and M8-23 (feeding the probe bundle) were
prerequisites for it.

## Decision

1. **Only three things belonging to the outside world may be simulated**: the physical observation window (`SynthWorld` → spool JSONL), the business system (a SQLite
   mock and a script), and human operations (API commands). The ground truth (oracle) is used by the test to build the business script and is never passed to platform code.
2. **The chain is run in the following order, entirely through operational code (`build_worker` / `scripts/*`)**:
   `train_koopman` → `encode` (latent index) → ingest-worker (ledger, records, claim events) → `train_probe` (weak labels → gate →
   registry) → bridge-worker → belief-worker → reconcile-worker → (human command `resolve_discrepancy`) →
   learning-worker → scheduler-worker.
3. **Conditions for G8**: (a) the G0–G7 integration tests remain green, (b) `test_g8_workers.py` is green (each `step()` is non-zero in the chain above),
   (c) there are 0 `TODO(question)` items in the code.
4. **How workers are counted**: the original "4 workers" are bridge / belief / reconcile / scheduler. Because ingest and
   learning, the entry and exit of the chain, are run in the same test, we verify that 6 workers and 2 jobs are non-zero.
5. **The condition of history replay is stated explicitly**: the test streams 8 hours of history at once. Belief updates saturate to
   1.0 as more events are streamed, and the scheduler's observation utility becomes 0 (it is correct behavior that there is no value in re-observing a target we are confident about).
   For scheduler verification, restrict belief updates to one per step and run on beliefs that are still uncertain.

## What G8 found

On the first day the chain was run, 6 **breaks in the chain** were found that existed while each worker's unit and integration tests were green
(ADR-0026, PROGRESS finding 133). The point of simulating only the outside world is to actually exercise such "contracts between stages".

## Consequences

- The literal M8 gate now means that the platform runs in operation.
- Limitation: history replay differs from real-time operation (the arrival order of events is not fixed). Places that depend on arrival order were made
  independent of arrival order in ADR-0026. Validation on real data is in a separate repository (ADR-0001).
