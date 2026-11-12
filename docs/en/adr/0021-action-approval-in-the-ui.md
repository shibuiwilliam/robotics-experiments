# ADR-0021: UI action approval is backed by two resources that read the contract summary and decision state, and failures return their reason as is

- Date: 2026-11-08
- Status: Accepted
- Layers touched: knowledge (decision history), api (read resources), apps/ui (approval form)

## Context

`/commands/execute_action` was stood up in M8-21 (ADR-0014), but **the UI has nowhere to press it**. We need a screen where a human approves, from the discrepancy queue,
things like "file an emergency inspection", and a path that returns the reason for a failure (precondition not met, rate limit) to the human.
ADR-0014's "What gets worse" says "to a human it looks like 'I approved it but it did not run'. A path to return the reason to the screen is needed."

Facts confirmed:

1. The UI has no means of knowing **which actions it can propose**. The contracts (`actions/*.yaml`) cannot be read from the API.
2. There is **no resource that reads the state** of decisions (`learning_decisions`). The table has `applied_at` / `failed_at` / `error` /
   `artifacts`, but the API does not return them.
3. **A failed decision is not retried automatically.** `mark_failed` sets `failed_at`, and `pending` pulls only rows with
   `failed_at IS NULL`. The docstring of `_apply_action` said "it becomes a retry target", which
   disagreed with the implementation (finding 127). The precondition may be met later, but a human **resubmits as a new decision** to rerun.
4. The execution request `ExecutionCommand`'s `target`, `severity`, and `correlation_id` can be filled from the discrepancy.

## Options

### Question 1: How the UI learns which actions it can propose

1. **A GraphQL resource `actionTypes` that returns a contract summary.** From the contracts (`ActionRegistry`), return the action type, target type, risk class,
   whether approval is required and its conditions, per-surface precondition descriptions, expected effects and deadlines, compensation, and rate limit. The contract is the only source of truth and is not copied into the UI.
2. Have the UI hold a copy of the contracts. Each contract change leaves the UI stale and gets approval requirements wrong (the shape of finding 83).

### Question 2: How to return the result

1. **A resource `decisions(subject, kinds, limit)` that reads decision state.** Returns `pending` / `applied` / `failed`, the failure reason,
   and a summary of the application (expectation ID, etc.). The subject is a discrepancy ID or an entity IRI.
2. Return the success or failure of execution in the HTTP response. The shape ADR-0012 rejected (a human waits for the application, and a failure loses the decision).
3. Notify via the event log. Work-item state is not a claim (no seat in the modality lattice).

### Question 3: Handling failures

1. **Show the reason as is, and state that it can be resubmitted as a new decision.** The same decision is not retried (fact 3), so
   we do not let people read it as "it will fix itself if you wait". Resubmission uses a new idempotency key.
2. Retry failures automatically. Rate limits and unmet preconditions need a human's judgment of the situation, and automatic retry of operations that change the business system is dangerous.

## Decision

Question 1: `actionTypes`. Question 2: `decisions`. Question 3: show the reason and prompt resubmission.

- Both are GraphQL (read-only), and require a requester (ABAC) (deny by default).
- The UI form selects the action type from the contract list, and fills the target, severity, and `correlation_id` automatically from the discrepancy
  (a human does not rewrite them). **A reason is required.** For contracts that require approval, show the approval conditions and role.
  **Overriding an unmet precondition requires a reason in a separate collapsed section that is closed by default.**
- One idempotency key per form (same as the resolve form).
- **Compensation is not executed** (awaiting the H-08 decision). The compensation target is only displayed in the contract summary.

## Consequences

- **Invariant 2**: Read-only. Neither table nor contracts change.
- **Invariant 12**: No secrets appear in reading contracts.
- **Configuration**: Worker count and alembic do not change. Two resources are added to the API.
- **Tests**: Unit (contract summary, decision formatting, UI input validation), integration (API `execute_action` -> learning-worker ->
  `decisions` returns `applied` / `failed`).

## Open questions

- **Approver identity (H-25).** `approved_by` is self-declared by the caller, and the UI puts in a fixed subject. The approval provenance
  (`Approval.approved_by` / `executed_by` / `Override.overridden_by`) cannot be trusted until the command API is bound to an authenticated
  subject. The impact is large now that it is reachable from a button (the M8-22 review).
- **Per-row authorization for `decisions`.** It only confirms the existence of a requester and does not look at per-subject visibility (a decision is not a statement and
  is not a target of `visible_rows`). Site separation is kept by `DecisionQueue(site)`. `error` contains the target's IRI and the precondition description.
- **"Accepted" does not mean enqueueing is complete.** The command handler fires off the enqueue and returns the receipt immediately (ADR-0012). Even if the enqueue
  fails, the UI shows "accepted" and the history stays empty (it remains in the log, and can be resent with the same `decision_id`).

- **The server keeps a floor on severity.** The execution request's `severity` is self-reported, and sending `0` means no approval record is created. If the originating discrepancy
  can be read from the table, its severity becomes the floor (`LearningWorker._trusted_severity`).
- **Pre-evaluating preconditions.** To announce "will be met / will not be met" in the UI before execution, the UI needs a layer that reads beliefs and evaluates.
- **Assisting resubmission of failed decisions.** After fixing the cause of a failure, issue a new-key decision with the same content in one click.
- **H-06 / H-07 / H-08 / H-25** (persisting the rate limit, persisting `ActionEvent`, the entry point for compensation, authentication of the command API) are not decided here.
