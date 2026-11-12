# ADR-0014: Action execution enters through the decision queue

- Status: Accepted
- Date: 2026-10-04
- Related: ADR-0012 (learning-worker applies human decisions), ADR-0011 (requests go on the work queue), design doc §9.2 (execution flow), §9.3 (safety guards), M8-21

## Context

In M5 we implemented the action layer (semantic contract, precondition verification, approval, execution, compensation), but **the operational path had no entry point**.
The commands known to `build_command_handler` are `resolve_discrepancy` and `curate_concept`,
and the path that executes an action exists only in the library and in tests (finding 89).

The execution flow in design doc §9.2 is "precondition verification -> approval -> execution -> `ActionEvent` -> `Expectation` generation", and
**`riskClass >= medium` requires approval** (§9.3). In other words, execution **waits for a human decision**.

## Decision

**Action execution enters the `learning_decisions` work queue as `kind='action'`, and learning-worker applies it.**

- Add `/commands/execute_action` to the API; it validates an `ExecutionCommand` and enqueues it (it does not apply it).
- learning-worker pulls it and applies it in the order of design doc §9.2.
- Expectations are written to the `expectations` table (M8-18 / M8-20).

## Options

### A. Decision queue (adopted)

Pros:

- **Idempotency already exists.** With `decision_id` + `ON CONFLICT DO NOTHING`, the table side prevents
  the same action from being executed twice due to a UI resend or an HTTP retry. **Double execution of an action is the worst
  failure on this platform** (it changes the business system twice). There is no reason to rebuild this ourselves.
- **The shape, a human decision, is the same.** What ADR-0012 built is a path that "receives a human decision and applies it", and
  execution with approval is the most natural example of it. Approval is a decision, and execution is its application.
- **The separation between "accepted" and "applied" is preserved.** The HTTP response does not return whether execution succeeded
  (it can fail on precondition verification or the rate limit, and telling a human about that is the screen's job).
- Neither services nor workers increase.

Cons:

- learning-worker is named for "learning" but executes actions. **Its role is consistently "applying human decisions",
  but the name is too narrow.**
- A DDL is needed to widen the table's `kind` CHECK (alembic 0008).

### B. Add a dedicated worker (action-worker)

Pros: The name fits the job. Operations can stop execution alone.

Cons: **It would be a sixth worker.** We would rebuild idempotency, the queue, periodic execution, and the liveness check, and
end up with two structures identical to ADR-0012. It conflicts with CLAUDE.md's "adding services just because it is convenient".
If we want to stop execution, the right design is to stop it through the contract's `riskClass` or the approval policy.

### C. The API executes synchronously

Pros: Short implementation. The result returns immediately.

Cons: **Rejected.** (1) Precondition verification involves belief queries and execution involves write-back to the business system,
so an HTTP timeout would lose the human's approval. (2) We would have to implement idempotency ourselves. (3) The API
process would gain a path that changes the business system, mixing execution responsibility into a read-centered layer.

### D. Send it through the event log

**Rejected.** Same reason as ADR-0011: an execution request is **not a claim**, so `EventEnvelope` has no
seat for it (it matches none of the modality lattice). A repeat of findings 62 / 63.

## Consequences

### What gets better

- The order in design doc §9.2 is gathered in one place in production code (it was previously inside the G5 test).
- The table prevents double execution.
- By default, **nothing can be executed**: until an operator explicitly wires the contract registry, belief reads, and expectation writes,
  applications are recorded as failures (same shape as the write-back in ADR-0012).

### What gets worse, and the remaining gaps

- **The rate limit lives in the worker's memory.** `RateLimitState` is in-process and disappears on restart.
  The idempotency key prevents "the same decision", but "two different decisions in a short time" gets through after a restart. Moving it into a table is
  a separate task (the "repeated actions on the same target in a short time" of §9.3 must be persisted).
- **`ActionEvent` is not emitted to the event log.** There is no topic, and `ActionEvent` is not an
  `EventEnvelope` (it is a dataclass). For now `Expectation.action_event` merely holds the ID, and
  the execution record itself does not remain in a table.
- **Precondition beliefs are checked against the "current belief".** Not the belief at the time the human approved. If the
  state changes between approval and application, the precondition fails. This is **correct** (do not execute on stale preconditions), but to a human
  it looks like "I approved it but it did not run". A path to return the reason to the screen is needed.

## Impact on invariants

- Invariant 4 (event idempotency): No impact. The queue is not the event log.
- Invariant 2 (append-only): No impact. The table is a work item, not assertions.
- **Invariant 3**: Expectations are not beliefs, so they carry no modality. The beliefs that execution produces (observations of the effect) enter
  through a separate path as `observed` / `inferred`.

## Ontology version

No change (0.5.0). `ActionEvent` / `Expectation` are already defined.

## Open questions

1. **Persisting the rate limit.** Whether to move it into a table, or whether a form that avoids introducing Redis (counting `expectations`'
   `created_at`) suffices.
2. **Persisting `ActionEvent`.** A table, or a mapping to carry it in `EventEnvelope`. For the latter,
   we would have to answer "is execution a claim?" (reading it as "having executed is an observable fact", there is a seat
   for `observed`).
3. **The entry point for compensation.** Whether to enqueue it in the same shape as execution, or run it automatically from an execution failure.
   Automatic is dangerous (compensation itself changes the business system), so no entry point has been built for now.
