# ADR-0016: Business-integration ingestion runs periodically in a sixth worker, turns record claims into events, and holds the asset register as assertions + a projection

- Date: 2026-10-25
- Status: Accepted
- Layers touched: core (deterministic event_id), knowledge (ingestion cursor and register projection), integration (mapping claims, register sync, plans), services (ingest-worker), infra (alembic), ontology (0.6.0)

## Context

The **record path** of design doc §2.5 (CDC/API -> schema mapping -> BusinessEvent -> event log -> belief update) was
built as a library in M1 (`IngestionPipeline`, the mapping DSL, the reliability model, plan ingestion). But
**nobody calls it in operation** (`docs/PROGRESS.md` finding 101). Only the G1 integration test calls `IngestionPipeline`,
and `make seed` loads only the ontology. In a fresh environment `entities_of_type` returns 0 rows, the reconcile worker's
record column stays empty, and plans never become `planned`.

While planning M8-25 we found one more gap (finding 115). **Even if ingestion runs periodically, nothing
reaches the reconcile worker.** The events that `IngestionPipeline` emits have `type = record type` (`ent:OperationLog`) and `p = 1.0` (the record
exists). The reconcile worker picks up only types of the form `sbp-core:BusinessEvent/ent:Running` via `fluent_of_event`, and
reads `p` as "the record-derived probability passed through the reliability model" (`rules/kind2.py`). There was no stage that turns a record's **claim**
(MES says P-101 is running) into a fluent probability, and G3 assembled it by hand inside the test.

Furthermore, there is no entry point for the asset register (design doc §7.1 "master: periodic sync"). The readers (`entities_of_type` /
`most_specific_types` / `part_whole_edges`) read plain triples (`<P-101> rdf:type ent:Pump`), but
`AssertionStore` writes only reifiers and triple terms, not plain triples.

Related: design doc §2.5, §3.12 (entity IDs are stable IRIs derived from business IDs), §6.1 (idempotency), §7.1 to §7.4, §8.2
(record-derived likelihood for type 2). ADR-0006 (mapping DSL), ADR-0009 (grounding targets are looked up from the knowledge graph by type),
ADR-0012 (precedent for a dedicated worker).

## Options

### Question 1: Where to run ingestion periodically

1. **Add a sixth worker, `ingest-worker`.**
   - Pros: Same as ADR-0012. Latency or failures of the business system do not affect reconciliation or belief updates. It keeps the one-worker-one-process
     convention. SIGTERM, liveness checks, and idempotency can use `services/runtime.py` as is.
   - Cons: One more process and entry in the worker list (documents must be updated).
   - Cost on a MacBook: Not started by default (`scripts.dev --workers ingest`). It only queries tables periodically.
2. Piggyback on the reconcile worker.
   - Cons: Two kinds of drive (a Kafka consumer and business-system polling) get mixed, making the meaning of `step()` ambiguous.
     Waiting on the business system's response stalls window evaluation.
3. A periodic job (cron equivalent).
   - Cons: As with ADR-0012, we would reimplement the worker machinery.

### Question 2: Where to turn a record's claim into a probability

1. **The integration layer emits claim events.** Add an optional `claims` to the mapping DSL (`{fluent: ent:Running, field: running}`), and
   in addition to the existing "record exists" event, `IngestionPipeline` emits, per claim,
   `type = sbp-core:BusinessEvent/ent:<Fluent>`, `modality = observed`, and
   `p = σ(ReliabilityModel.log_likelihood_ratio(record_says))` (uniform prior).
   - Pros: The reliability model belongs to the integration layer (design doc §7.3), so there is one place for the conversion. The reconcile worker needs no
     change (modality `observed` enters the record column, finding 47). The three-source belief update (M8-26) can use the same events.
   - Cons: An extension to the mapping DSL (extends the scope of ADR-0006). Events grow to 1 + the number of claims per record.
2. The reconcile worker and the belief worker each interpret record types.
   - Cons: Knowledge of "which column of which record type is a claim about which fluent" is duplicated in two places in the reasoning layer.
     `EventEnvelope` does not carry the record contents (payload), so the claim value cannot be obtained in the first place.

### Question 3: How to place the asset register in the knowledge graph (chosen by the user on 2026-10-25)

1. **Assertions + a projection.** Append each register row as a bitemporal assertion (`observed`, provenance = register sync), and
   rebuild the plain-triple projection graph `urn:sbp:g:master:<site>` from the currently valid statements each time.
   - Pros: Honors invariants 1 and 2 (the register is also "someone's claim", and changes close `tx_to` and append). The projection can be
     recomputed from the statements ("state is a projection of events"). Readers and their tests do not change.
   - Cons: Writes happen in two stages. The projection is rebuilt (it is a projection, not statements, so replacing it does not
     touch invariant 2).
2. Assertions only. Rewrite the readers to go through reifiers (statements with open `tx_to`).
   - Cons: Rewriting three readers and the unit-test fixtures is large.
3. A reference graph only (treated like the ontology).
   - Cons: Provenance and the as-of history do not remain, departing from design doc §0.3 "every fact is someone's claim".

### Question 4: How to keep idempotency

Ingestion is at-least-once (the cursor advances after sending, so a crash in between re-reads the same row).

1. **Deterministic IDs.** Make the ingest event's `event_id` a deterministic UUIDv7 (time part = record time, random part =
   a hash of `(source, table, external_id, updated_at, claim)`), and reject resends via `processed_events`.
   Register and plan assertions get an `assertion_id` (uuid5) derived from content, so a rerun yields no diff.
   - Pros: Keeps the form of invariant 4 (idempotent by UUIDv7 `event_id`), and re-reading becomes harmless.
   - Cons: The random part of the UUIDv7 is filled by a hash (RFC 9562 permits implementation-defined filling).
2. Advance the cursor before sending.
   - Cons: A row whose send failed is never read again (the dropped-row problem of finding 52).

## Decision

- **Question 1: Option 1** (`services/ingest_worker.py`). Update `WORKERS` in `scripts/dev.py`,
  `docs/development-rules.md`, and the worker list in the layer rules in the same change. The CLAUDE.md list is added
  on the branch awaiting confirmation (X-15).
- **Question 2: Option 1**. The first target is `operation_log.running -> ent:Running`.
- **Question 3: Option 1**. Add the register's record type `ent:AssetRegisterEntry ⊑ sbp:Record` to the ontology (0.6.0,
  MINOR). Register rows become assertions of `rdf:type` (only types that actually exist in `vocab.ent`) and `sbp:partOf`.
- **Question 4: Option 1**. The cursor is `pulled_through` per `(site, source, table)` (alembic 0010
  `ingest_cursors`, an upsert that does not regress), and **advances after sending**.
- **The external world in development is a file-backed SQLite mock** (`MockBusinessSystem(db_path=…)`). Tests or developers
  pour in a script, and the worker ingests it as production code. For real-system connectors we provide only a hook to swap them in via configuration
  (if `SBP_INTEGRATION_MOCK_DB_PATH` is empty it starts with no connector and reports `unwired`).
- **Plans**: The mapping from plan kind to intended fluent sits next to the compliance-rate table in `plans.py` (PlannedOperation ->
  Running, PreventiveMaintenance -> UnderMaintenance, Inspection -> Inspected). Unknown plan kinds are not guessed at;
  they are skipped and logged. When a plan changes, close the old statement's `tx_to` and add a new one.
- **Order within one cycle**: register -> resolver update (read known entities from the projection) -> business records (send) -> plans. If the register is not
  first, records for new equipment become `UnresolvedRef`.

## Consequences

- **Invariants 1 and 2**: Register and plan assertions carry the required fields, and changes close `tx_to` and append. The projection
  graph is not statements (it is a derivative that can be rebuilt from statements).
- **Invariant 3**: Claim events are `observed` (that the record makes that claim is an observed fact). Building a
  belief from them is the reasoning layer's job, and its output is `inferred`. Register assertions are `observed`.
- **Invariant 4**: The ingest event's `event_id` becomes deterministic while keeping the UUIDv7 form.
- **Invariant 5**: Fluents and types are drawn from `vocab` and the mapping's CURIE expansion; no IRIs are hardcoded.
- **Invariant 6**: The claim event's probability is built from the reliability model only, and no belief is an input. Audit isolation continues to be applied by
  the consumers (reconciliation, belief update).
- **Ontology version**: 0.5.0 -> 0.6.0 (MINOR, additive). Existing probes, queries, and `schemas/` do not break.
  Re-grounding is not needed (the register individuals are new).
- **Configuration**: Workers 5 -> 6. alembic 9 -> 10.
- **Tests**: Unit (claim declaration and checking, claim event probability, properties of deterministic IDs, register diff, projection, plan
  replacement, the order of one worker cycle and the cursor), integration (external SQLite -> ingest-worker -> Kafka and knowledge graph ->
  the reconcile worker's record column; a second cycle adds nothing).

## Open questions

- **Persisting schema fingerprints and sending `MappingDriftDetected`.** The fingerprint is currently in in-process memory, so
  the comparison right after a restart is always empty. The `<site>.integration.mapping` topic exists but nobody sends to it.
- **A register for SKUs and site layout.** The register in this ADR covers only equipment (and the lines that become the whole side of part-whole).
- **Real-system connectors and credentials.** Not handled in this repository (use-case validation is in a separate repository,
  ADR-0001). Where credentials live is tied to `docs/HITL.md` H-01.
- **Beliefs about equipment that disappeared from the register.** The register's statements are closed by `tx_to`, but how to handle existing beliefs about that equipment
  (whether it is enough for it to just drop out of the grounding targets) is not decided.
