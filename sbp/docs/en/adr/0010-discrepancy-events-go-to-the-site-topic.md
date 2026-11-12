# ADR-0010: Discrepancy filing events go to the site topic, and central aggregation is a separate job

- Date: 2026-10-04
- Status: Accepted
- Layers touched: reasoning (filing notifications), services (wiring of the reconciliation worker)

## Context

The reconciliation worker (`services/reconcile_worker.py`) detects discrepancies of the five types, but **no output destination
was configured** (`TODO(question)`; known constraint in `docs/PROGRESS.md`). It starts with `sink` left as
`None`, so even when it detects something, nobody receives it.

Both topics have already been created at the time of `make seed` (`scripts/topics.py`).

| Topic | Description in `scripts/topics.py` |
|---|---|
| `<site>.reasoning.discrepancy` | Discrepancies filed by the reconciliation engine (five types) and their state transitions |
| `central.reasoning.discrepancy` | Discrepancies aggregated across sites (for central reconciliation and audit) |

The topic-naming example in `.claude/rules/knowledge.md` lists `central.reasoning.discrepancy`, which makes it hard to tell
whether "publishing to the center is the default". Related: design document §6.1 (topic naming and
per-entity ordering), §8.2 (discrepancy lifecycle).

## Options

1. **Publish to the site topic (`<site>.reasoning.discrepancy`). Central aggregation is the job of a forwarder.**
   - Benefits: the detector only needs to know about its own site (consistent with the placement of one worker per site).
     Because the site holds its own record, filings are not lost even if the center is down. `scripts/topics.py`
     itself defines the central topic as discrepancies "**aggregated** across sites", so reading it as the detector's
     output destination contradicts the definition.
   - Drawbacks: subscribers who want to see all sites (central audit) need a forwarder. The forwarder is not implemented.
2. Publish directly to the central topic (`central.reasoning.discrepancy`).
   - Benefits: no forwarder needed. All sites can be subscribed to via a single topic.
   - Drawbacks: requires each site's worker to be able to reach the central broker. No record remains at the site,
     so a filing whose transmission to the center failed cannot be reproduced from the site side. The definition of the central topic
     (already aggregated) and its content (raw detection results) are inconsistent.
3. Publish to both.
   - Benefits: satisfies subscribers of either.
   - Drawbacks: **the same discrepancy gets two `event_id`s.** Invariant 4's idempotency
     depends on `event_id`, so sending two would make "the same discrepancy a different event", and downstream would double-count.
     If the same `event_id` is sent to two topics, the idempotency record (`processed_events`) is keyed on
     `(consumer_group, event_id)`, so only one of them would be processed. Either way it breaks.

## Decision

**Option 1.** The default is the site topic, and it can be switched to the center with `SBP_REASONING_DISCREPANCY_SCOPE=central`
(the original open question that per-site operation is also possible is retained through configuration).

**The option of publishing to both is not adopted.** Because idempotency is defined by `event_id`, the shape of sending the same discrepancy twice
cannot be expressed on this platform. When central aggregation becomes necessary, we will build a **forwarder** (subscribe to the site topic,
republish to the central topic, and put the original `event_id` in `causation_id`), so that
"the central event was derived from the site event" appears in provenance.

## Consequences

- **Invariant 3**: the modality of the filing event is `inferred` (the detectors for the five types are rules).
- **Invariant 4**: one event per discrepancy. `discrepancy_id` is put in `correlation_id`,
  so that state-transition notifications are tied to the same discrepancy.
- **Invariant 1**: `ontology_version` uses the one the discrepancy itself holds (the version at detection time).
- **Ontology version**: unchanged (0.5.0). The event type is assembled from `<core#Discrepancy>/kind<n>` and
  `vocab` (no new individuals are placed).
- **Tests**: unit tests pin the contents of the envelope (modality, confidence, how the subject is chosen, type IRI). Integration tests cover
  reconciliation worker → Redpanda → subscription.

## Open questions

- **There is no persistence or API exposure of discrepancies.** An event is a notification and not the discrepancy itself
  (`EventEnvelope` has no place to hold the whole thing). The UI has the type and logic for a discrepancy queue, but
  the API side has no resource, and discrepancies are written to neither the knowledge graph nor the projection tables. **A notification alone does not let the UI
  read the contents**, so this was filed as a separate task (M8-12).
- **Subject of a discrepancy involving multiple entities.** Only one partition key can be chosen, so for type 4
  (identity) the lexicographically smallest entity is the subject, and the rest fall outside the unit of ordering guarantee. The smallest was chosen over the first
  so that the same discrepancy goes to the same partition when it is re-detected.
- **The forwarder** (site → center) is not implemented. It will be built when it becomes necessary.
