# ADR-0012: Receive human decisions in a work queue, and have a fifth worker apply them

- Date: 2026-10-04
- Status: Accepted
- Layers touched: knowledge (decision work queue), api (command handlers), services (learning-worker), infra (alembic)

## Context

Resolving a discrepancy (`resolve_discrepancy`) and accepting or rejecting a concept proposal (`curate_concept`) are **human decisions**, and
the UI (M7-04) has been built up to the point of POSTing to `/commands/*`. However,

- the `command_handler` of `create_app` is `None` (it was actually wired in M8-13, but no handler is passed),
- there is no code that takes a decision and executes write-back, probe retraining, or concept adoption,
- therefore the `LearningArtifacts` emitted by `apply_resolution` (M3-06) are **applied nowhere**.

Moreover, the same shape of gap appeared four times through M8 (`docs/PROGRESS.md` finding 72): the probe store,
the observation-request queue, metrics, and Situation relations. In every case, reading and periodic execution exist, but
**"who puts in the first value" is left open**. In many of them, a human decision is the entry point.

Related: design document §8.2 (table of resolution result → learning signal), §9.2 (approval flow), §12.2 (worker structure),
ADR-0008 (UI), ADR-0011 (precedent for work queues).

## Options

### Question 1: Where are human decisions received?

1. **Write them to a work queue (projection table), and have a worker apply them.**
   - Benefits: "a human decision was accepted" (fast, must not be lost) and "applying it" (slow, can fail) can be separated. Write-back
     is an external call to the business system that takes seconds to tens of seconds and fails when the other side is down. Retrying application can be
     expressed on the queue.
   - Drawbacks: one more table. The UI can see only up to "accepted" (the result of application is read later).
2. Apply synchronously in the API handler.
   - Benefits: no extra table. The UI can see the result on the spot.
   - Drawbacks: **the human waits for write-back to complete.** On failure the decision itself is lost (the UI would have to
     ask for re-entry, and the same decision could be entered twice). The API process would hold credentials for the business system,
     adding side-effect authority to a read-only layer.
3. Put them in the event log.
   - Drawbacks: they do not fit, for the same reason as ADR-0011. "The physical side was right about this discrepancy" is a claim, but
     "apply it" is a command, and there is no seat for it in the modality lattice. **The resolution itself is recorded as the state of the `Discrepancy`**,
     so what should go in an event is the result, not the command.

### Question 2: Who executes the application?

1. **Add a fifth worker (`learning-worker`).**
   - Benefits: failures and delays in write-back do not affect reconciliation or belief updating. Keeps the convention of one worker per process.
   - Drawbacks: the workers listed in CLAUDE.md go from 4 to 5 (documents need updating). One more process.
   - Cost on a MacBook: not started by default (explicit via `make dev --workers`). Even if resident, it
     only pulls the queue periodically and is nearly idle.
2. Piggyback on the reconciliation worker (`reconcile-worker`).
   - Benefits: no additional process.
   - Drawbacks: **reconciliation is a hot path that runs windows**, and write-back is a synchronous call to an external system. A stuck write-back
     stops detection. Moreover, two kinds of drivers (a Kafka consumer and a DB queue) would be
     mixed, making the meaning of `step()` ambiguous.
3. Make it a periodic job (cron equivalent).
   - Drawbacks: we would have to reimplement graceful shutdown on SIGTERM, connectivity checks, and the idempotency machinery (`services/runtime.py`).
     Aligning it to the worker shape keeps operations to one.

### Question 3: How much is applied automatically?

There are five kinds of learning outcomes (`LearningArtifacts`). **Not all are applied automatically.**

| Outcome | Treatment in this ADR | Reason |
|---|---|---|
| `observation_requests` | **Applied automatically** (placed on the work queue) | It only places an observation request, with no side effects. The scheduler does the choosing, and questions to humans have a frequency cap |
| `corrections` (write-back) | **Executed in dry mode by default** (`SBP_WRITEBACK_MODE`) | It changes the business system, so `live` is for the operator to specify explicitly. Compensation is a required field of `WritebackCommand` |
| `reliability_updates` | Recorded only (not applied) | The reliability model affects the likelihood of all reconciliations. Moving global weights from a single resolution should wait until a human has looked |
| `bags` (negative examples) | Recorded only | Probe retraining is a separate job that passes the promotion gate (AUROC ∧ ECE ∧ consistency residual) |
| `concept_candidates` | Recorded only | Filing an ontology MINOR change remains manual human work (the decision of M6-04) |

## Decision

- **Question 1: Option 1** (work queue). The `learning_decisions` table (alembic 0005). The same shape as ADR-0011's
  `observation_requests`, with **rows never deleted**; `applied_at` / `failed_at` are set.
- **Question 2: Option 1** (a fifth worker). `services/learning_worker.py`. The worker lists in CLAUDE.md and
  `docs/development-rules.md` are updated in the same commit.
- **Question 3: the table above.** What is applied automatically is only observation requests and (default dry) write-back; the rest is
  **recorded as outcomes, and application is left on a separate path that goes through human approval**.

The API command handlers (`sbp.api.commands`) go **only as far as placing items on the queue** and do not apply.
If a handler did heavy work, an HTTP timeout would cause a human decision to be lost.

## Consequences

- **Invariant 2 (append-only)**: the decision queue is not an assertion. The result of a resolution is recorded as a state transition of the `Discrepancy`,
  which follows the append-only discipline.
- **Invariant 3**: a human decision is `observed` (in the trace of design document §14 as well, a human's answer has the highest
  factuality). However, within the scope of this ADR the decision is **not registered as an assertion**: the `resolution` of
  the `Discrepancy` is the record, and nothing is written twice into the belief graph.
- **Invariant 11**: no LLM enters this path (concept proposals are generated in M6-04, and acceptance or rejection is by a human).
- **Ontology version**: unchanged (0.5.0).
- **Layout**: workers go from 4 to 5. CLAUDE.md, development-rules.md, and `WORKERS` in `scripts/dev.py`
  are updated.
- **Tests**: unit tests for queue semantics, handler validation, and the scope of the worker's application (only observation requests
  and dry write-back are applied automatically); integration tests for round-tripping the queue against a real Postgres.

## Open questions

- **Retry count and cutoff for application.** For now a failure is recorded in `failed_at` and removed from the candidates
  (no infinite retrying). A path for a human to resubmit cut-off items is not implemented.
- **There is no persistence of `Discrepancy`** (M8-12). The decision queue holds a `discrepancy_id`, but
  there is nowhere to read the discrepancy body, so the worker applies **using only the information contained in the decision**.
  `apply_resolution` requires a `Discrepancy`, so within the scope of this ADR the discrepancy passed to
  `convert_resolution` is **reconstructed from the queue row**; this will be replaced once
  the body can be read in M8-12.
- **Application of concept adoption (`curate_concept`).** It goes only as far as recording the acceptance or rejection of proposals, and filing the
  ontology MINOR change after adoption remains manual human work (maintaining the decision of M6-04).
