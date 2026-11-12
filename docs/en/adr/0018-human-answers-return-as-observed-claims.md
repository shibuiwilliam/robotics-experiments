# ADR-0018: Human confirmation answers are received by a fourth command and returned to beliefs as `observed` claim events

- Date: 2026-11-07
- Status: Accepted
- Layers touched: api (command), knowledge (decision kind, reading observation requests), integration (answer event), services (learning-worker), infra (alembic 0012)

## Context

Step 4 of design doc §14 states that "a human answer has the highest factuality as `observed`, and turns into record correction and reliability-model update
(a learning signal)". The scheduler can choose to ask a human a question (`HUMAN_QUESTION`), but **there is no entry point for the answer**. The commands are
`resolve_discrepancy` / `curate_concept` / `execute_action`, and observation requests, even when
enqueued and issued, are never satisfied (nothing calls `ObservationQueue.mark_satisfied`; H-13, an open question in ADR-0011).

In M8-26 the belief-worker began to take the business topic's claim events (`BusinessEvent/<fluent>`, `observed`, probability p) into beliefs
as record likelihoods (`BeliefIngest` applies audit isolation to `RecordEvidence`).
**If a human answer is made into an event of the same shape, the belief update can treat the answer as evidence without change.**

## Options

### Question 1: The entry point for answers

1. **Receive them through a fourth command, `answer_observation`, via the work-queue path (ADR-0012).** The API only validates and
   enqueues to `learning_decisions`, and learning-worker applies. The idempotency key, retries, and failure recording come from the existing machinery.
2. Include the answer in the outcome of `resolve_discrepancy`. Resolution (record correction, reliability update) and an answer (adding one piece of evidence) differ
   in the weight of the decision, and the observation request's key does not fit (some do not originate from a discrepancy, e.g. expectation expiry, ADR-0017).
3. The API sends directly to the event log. The shape ADR-0012 rejected (the read layer gains side-effect authority, and a failure loses the decision).

### Question 2: How to bring the answer into beliefs

1. **Send an event of the same shape as the ingestion claim event to `<site>.integration.business`.**
   `type = BusinessEvent/<fluent>`, `modality = observed`, `p` is the human's confidence (c if it holds, 1 - c if it does not),
   the evidence is `RecordEvidence(record=urn:sbp:human-answer:<decision_id>, source=<answerer>)`, and `event_id` is derived from
   `decision_id` (a resend does not double up; invariant 4). Belief update, reconciliation, and audit isolation work as they are.
2. Write the `observed` assertion directly to the knowledge graph. It bypasses the belief update, and the `inferred` current belief remains
   at odds with the human's answer (the belief-worker reads events).
3. Add a dedicated evidence kind (`HumanEvidence`). Not worth changing the event contract (backward-compatibility checks, schema regeneration).
   Putting the answerer in `RecordEvidence.source` suffices.

### Question 3: Whether a request is "satisfied" (the human-answer part of H-13)

1. **The answer names the request's key `(discrepancy_id, fluent)`. A key match satisfies it.** The human chooses which request the answer is for, so
   no matching on entity x fluent x interval is needed.
2. Infer from the overlap of entity x fluent x interval. This leaves the problem of H-13 that "how much measurement counts as satisfied" differs by means.

Satisfaction when a sensor observation returns remains as H-13 (out of scope for this ADR).

## Decision

Question 1: the command `answer_observation`. Question 2: a claim event. Question 3: key match.

- Command: `decision_id` (idempotency) / `discrepancy_id` (a string; there is also the expectation-derived `expectation:<id>`) / `fluent` /
  `entity` / `state` (whether it holds) / `note` (required) / `answered_by`.
- Application is send -> record the satisfaction -> mark the decision applied. Sending is idempotent through deterministic IDs, so a failure and retry does not double up.
- **An answer to a request not in the queue fails** (a mis-send is likely). **An answer to an already-satisfied request is accepted**
  (a correcting answer is possible; satisfaction is idempotent).
- The human's confidence is `Settings.reasoning.human_answer_confidence` (default 0.98). It is not set to 1.0 (humans err too.
  Beliefs saturate in log-odds, so they can later be overturned by other evidence).
- Record correction and reliability update remain with `resolve_discrepancy`. An answer only brings evidence into beliefs and does not
  automatically make a resolution decision (one answer does not move a record correction or the likelihood of every reconciliation; same line as Question 3 of ADR-0012).

## Consequences

- **Invariant 3**: The answer's modality is `observed` (the human is an observer). Building a belief from it is the belief-worker's job, and the output is `inferred`.
- **Invariant 4**: `event_id` is a UUIDv7 deterministic from `decision_id`.
- **Invariant 5**: Fluent types are drawn from `vocab`, and no IRIs are hardcoded.
- **Invariant 6**: Since the evidence is `RecordEvidence` (source = answerer), the existing `AuditMask` can exclude the answerer and answer ID.
- **Invariant 7**: p is the configured value of the human's confidence, not a raw score.
- **Invariant 11**: No LLM is used.
- **Configuration**: Worker count stays at 6. alembic 11 -> 12 (extending the CHECK on `learning_decisions.kind`).
  learning-worker holds a Kafka producer (the same `KafkaEventProducer` as ingest-worker).
- **Tests**: Unit (command validation, event shape and deterministic ID, application order and failure), integration (API handler -> queue ->
  learning-worker -> business topic -> belief-worker, where the belief moves in the direction of the answer and the request is satisfied).

> **Addendum 2026-11-08 (ADR-0029)**: Commands now bind the subject field to the requester resolved in the authentication layer. The "self-declared" below is the description before that change.

## Open questions

- **A UI answer form.** Only up to the API command. The screen is a separate task.
- **Answerer authentication, authorization, and confidence.** Like the other three, the command API does not pass through authentication, and `answered_by` is self-declared
  (`docs/HITL.md` H-25; answers can be injected in someone else's name). Any answer enters with the same confidence. Whether to
  differ between experts and field workers is also undecided.
- **Proposing a resolution from an answer.** Whether to produce a draft resolve command when an answer strongly suggests resolving a discrepancy.
- **Satisfaction by sensor observation (the remainder of H-13).**
