# ADR-0006: Implement mappings with a minimal mapping DSL instead of an existing RML processor

- Date: 2026-10-03
- Status: Accepted
- Layers touched: integration (mappings), tests (contract tests)

## Context

`.claude/rules/integration-llm.md` states that mappings are written in "RML / R2RML (`*.rml.ttl`)", and design document §7.2 treats a mapping as a functor `F: S→O`, requiring `Σ_F` / `Δ_F` and that "for each mapping, preservation of keys, preservation of required constraints, and preservation of multiplicity are checked in CI".

Result of surveying the [R2]RML processors available in Python at the start of M1-03 (as of 2026-10-03):

| Candidate | Status | Verdict |
|---|---|---|
| `morph-kgc` 2.10.0 (updated 2026-01-20) | Depends on **`pyoxigraph<0.4.0,>=0.3.0`**. It additionally requires `duckdb`, `pandas`, `geopandas`, `neo4j`, `kafka-python`, `databricks-sqlalchemy`, `kuzu`, `falcon`, and others | **Not adoptable** |
| `pyrml` 0.5.1 | Last upload **2016-07-31**. No declared dependencies, no updates in 10 years | Not adoptable |
| `rdfizer` 4.7.5 | SDM-RDFizer. Active, but its configuration file format is proprietary and its input mainly presumes files and RDBs | Candidate for consideration |

The `pyoxigraph<0.4.0` constraint of `morph-kgc` is a **decisive obstacle**. Per ADR-0005, representing statement-level metadata with RDF 1.2 triple terms + `rdf:reifies` requires the pyoxigraph 0.5 line, and the 0.3 line supports only the old notation that places a quoted triple in the subject. The bitemporal assertion API (M0-11, implemented and tested) would stop working, so downgrading the dependency is not an option.

The weight of the dependencies also violates our constraints. `duckdb` + `pandas` + `geopandas` + `neo4j` + `databricks` are inconsistent with CLAUDE.md's policies of "a scale that can be trained and run on a MacBook" and "show that the existing 4 services suffice". It would mean bringing in clients for RDBs, graph DBs, and analytics engines for the sake of a single mapping.

## Options

1. **Implement a minimal mapping DSL ourselves.** Declarations in YAML, execution by a small interpreter in `sbp.integration.mappings`.
   - Benefits: zero dependencies (only `pyyaml`, which is already a dependency). No conflict with pyoxigraph 0.5. Can be narrowed to the mappings SBP actually needs (one table row → `Record` + `BusinessEvent`). The three preservation properties required by design document §7.2 (keys, required constraints, multiplicity) **can be declared as part of the mapping spec**, making them easy to check mechanically in contract tests.
   - Drawbacks: departs from the standard RML representation. External RML assets cannot be brought in. Expressive power of mappings must be added ourselves.
2. Use `rdfizer`.
   - Benefits: close to the RML standard.
   - Drawbacks: input presumes RDB/CSV, and there is no route to pass a `RawRecord` (an already-ingested dict) directly. The configuration format is proprietary, so we would end up writing an adapter on the SBP side anyway. Checking preservation would be separate work of our own. Dependency weight is moderate.
3. Downgrade pyoxigraph to the 0.3 line and use `morph-kgc`.
   - Drawbacks: **ADR-0005 would no longer hold.** We would have to rewrite the bitemporal assertion API and revert the representation of statement metadata to the old RDF-star. It goes against the direction of standardization and discards the M0 implementation and tests. Rejected.
4. Write mappings directly in code (no declaration).
   - Drawbacks: diffs cannot be tracked when the business schema changes. The practice of "bumping the version of a mapping" (§7.2) becomes impossible. It contradicts the intent of the design document, which treats mappings as first-class deliverables. Rejected.

## Decision

**We adopt Option 1.** Mappings are declared in YAML and interpreted by `sbp.integration.mappings`.

```yaml
# mappings/work_order.yaml
mapping: work_order
version: 1.0.0
source: ent:CmmsSource
table: work_order
record_type: ent:WorkOrder

# Key preservation: the business-side primary key maps one-to-one to the platform-side external_id
key:
  columns: [wo_number]
  external_id: "{wo_number}"

# is_about: the reference used to resolve the subject entity (resolution is the resolver's responsibility)
subject_ref:
  column: asset_tag
  kind: asset

# Required-constraint preservation: fail the mapping if a column required on the business side is missing
required: [wo_number, asset_tag, status, recorded_at]

# Multiplicity preservation: one row yields exactly one Record (1:1)
cardinality: one_to_one

time:
  recorded_at: recorded_at
  occurred_at: completed_at

fields:
  wo_type: wo_type
  status: status
```

The extension is `.yaml`, and the `*.rml.ttl` that the rule specifies is not used (we do not give the name RML to something that is not RML). The relevant line of `.claude/rules/integration-llm.md` is updated with a reference to this ADR.

The three preservation properties of a mapping are checked from the declaration (the CI requirement of design document §7.2):

| Preservation | Declaration | What is checked |
|---|---|---|
| Key preservation | `key.columns`, `key.external_id` | Two rows with different business-side primary key values never get the same `external_id` |
| Required-constraint preservation | `required` | A row missing a required column makes the mapping fail, and it does not silently create an incomplete `Record` |
| Multiplicity preservation | `cardinality` | A mapping declared `one_to_one` never creates two or more `Record`s from one row |

These are checked in `tests/contract/test_mapping_<name>.py` (as the integration rule requires).

If a future migration to the RML standard becomes necessary (importing external RML assets, sharing mappings with other systems), the mapping declaration is confined to a single YAML file, so it can be converted once `morph-kgc` or similar supports pyoxigraph 0.5. The migration cost is proportional to the number of mappings, which is currently 4.

## Consequences

- Impact on invariants: none. The output of a mapping is a `Record` (pydantic), and the path beyond that (BusinessEvent conversion, belief registration) is unchanged.
- Ontology version: no impact.
- Dependencies: none added (`pyyaml` is already a dependency).
- Reflection in rules: update "RML / R2RML (`*.rml.ttl`)" in `.claude/rules/integration-llm.md` to "mapping DSL (`mappings/*.yaml`, ADR-0006)".
- Impact on testing: `tests/contract/test_mapping_<name>.py` checks the three kinds of preservation (the file-name convention stays as in the rule).
- Relation to design document §7.2: the **idea** of functorial data migration (checking preservation of keys, required constraints, and multiplicity as invariants of a mapping) is maintained, and only the implementation means changes. A reference to this ADR is added to §7.2.

## Open questions

Implementation of `Δ_F` (the direction that pulls ontology-side questions back to the business side). The current mapping DSL handles only `Σ_F` (business → ontology). Pulling back becomes necessary when computing the business-side key in reverse during write-back (M1-08), and there it should suffice to use the `key.external_id` template in the reverse direction. To be confirmed in M1-08.
