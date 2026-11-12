# ADR-0035: Start M10 and build the entrances for the quarantine queue and What-if (H-27 (3)(4)). The quarantine queue is written from "inputs that failed mapping"

- Date: 2026-11-08
- Status: Accepted (human instruction: of H-27, carry out the operational path for SHACL quarantine and the What-if entrance, 2026-11-08)
- Layers touched: knowledge (quarantine queue), integration (return failed rows from ingest), services (ingest-worker, learning-worker), api, reasoning (What-if; ADR-0036)

## Context

H-27 (3) the quarantine queue (the `quarantine` table is in alembic 0001 but no one writes to it) and (4) the What-if entrance (no one calls `run_scenario`). (1) identity binding and (2) the LLM entrance
require a perception contract and a cost policy, so they are not addressed.

Facts confirmed in the code about the quarantine queue:

1. **Structurally broken inputs are currently discarded.** A row that fails the ingest mapping is only accumulated in `IngestResult.failed` as `(external ID, reason)`, holds no raw row, and a warning goes to the log.
   The ingest cursor advances to the maximum `updated_at` of the rows read, so **a failed row is never read again**. Likewise, an asset-ledger row whose type is not in the ontology merely goes into `skipped`, and
   although the ledger is read in full every cycle, no human ever sees it. This is inconsistent with design document §3.9 / §5.7: "structural constraint violations go to the quarantine queue (not registered, a human reviews, not discarded)".
2. **There is currently no form in which validation by `pyshacl` (`validate_graph`) can be applied to operational inputs.** `AssetShape` requires `locatedIn`, but the asset ledger has no location column
   (location is a probabilistic predicate produced by spatial binding, which has no operational path). Applying ledger rows to `AssetShape` would make every row a violation and stop ingest. Loosening the shape is an ontology change (MAJOR-equivalent) and is
   outside the aim of building an operational entrance.
3. The quarantine table has `event_id uuid PRIMARY KEY` and no site column.

## Decision

1. **Start "M10: Operational Entrances, Part 2".** The aim of M9 and G9 are unchanged. Tasks: M10-01 quarantine queue (write side), M10-02 quarantine read/write (GraphQL `quarantine` and commands),
   M10-03 What-if job, M10-04 What-if read (GraphQL `scenarios`), M10-05 G10.
2. **What is written to the quarantine queue is input that ingest did not accept for structural reasons**: rows that failed mapping, and ledger rows whose type is not in the ontology.
   The reason is kept as a human-readable sentence, and **the raw row (`fields`) is stored as is in `payload`** (not discarded). An entrance that can also accumulate from a SHACL `ValidationOutcome`
   (`QuarantineStore.add_outcome`) is provided with the same type, but **evaluating shapes against operational inputs is future work** (because the need for a location in `AssetShape` depends on the operational path of spatial binding; left in H-27).
3. **Idempotent.** The quarantine identifier `event_id` is a uuid5 of `(site, source, table, external ID, reason)`. The ledger is read in full every cycle, so even if the same row keeps failing for the same reason it becomes only one row.
   If the reason changes, it becomes a different row. A reviewed row (`reviewed_at`) is not reopened even if it fails again for the same reason (do not re-accumulate what a human has decided "needs no action").
4. **Add a site column to the table** (alembic 0015, `site`). There are no existing rows (no one was writing).
5. **Quarantine is a work item, not a belief** (same treatment as ADR-0011 / 0013). It is not an input to inference (invariant 6). The review record is accumulated by a command as a new kind of `learning_decisions`, `quarantine_review`,
   and the learning-worker fills in `reviewed_at` / `resolution` (acceptance and application are separated; ADR-0012). The reviewer follows the principal binding of ADR-0029 (`reviewed_by`).

## Consequences

- Failed ingest rows remain in a form a human can review. The `mapping_violations` metric is as before.
- Reads of quarantine follow ABAC site isolation. `payload` is a raw row of the business system and may contain personal information — add it to ADR-0034 as a location covered by H-05 (erasure requests).
- **Handling added in review** (independent review, 2026-11-08): so that broken input does not break the write, NUL characters and NaN are sanitized before storing. The reason sentence does not include input values
  (only the type and location; `describe_failure`), so the identifier is stable. For an unreviewed row with the same reason, the raw input is updated to the latest. Healthy rows are sent before the quarantine failure.
  **Even if a ledger row receives a broken edit, the asset's immediately preceding valid statement is not closed** (it is put in quarantine for a human to review).
- **A quarantine row is not resolved automatically even if the original row is fixed** (review is done by a human. Automatic resolution requires a design that cross-checks against re-ingest of the original row, so it is separate).
- Limitations: shape (SHACL) evaluation is not applied to operational inputs. No quarantine screen is built in the UI (only up to GraphQL and commands).

## Open questions

- Whether to hold `AssetShape`'s `locatedIn` as a ledger column or to loosen the shape side (an ontology decision; to be decided together with the operational path of spatial binding).
