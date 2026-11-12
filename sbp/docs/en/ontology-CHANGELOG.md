# Ontology Changelog

Criteria for the semver judgment (CLAUDE.md, "Procedure for ontology changes"):

- **MAJOR**: deletion of a class or relation, or a change of meaning. An ADR and a re-grounding plan are required.
- **MINOR**: additive changes. Backward compatibility of existing probes, queries, and `schemas/` is proven by contract tests.
- **PATCH**: changes to annotations only (label / definition / comments).

Deprecation is marked with `owl:deprecated true` and `dcterms:isReplacedBy`, and the item is kept for 2 MINOR versions.

## 0.6.0 — 2026-10-25

MINOR (additive). There are no changes to existing classes, relations, shapes, fluent types, or event types. Backward compatibility of probes,
queries, and `schemas/` is preserved (contract test `test_ontology_compat.py`). Re-grounding is not needed
(the register individuals are new, and the meaning of existing statements does not change).

### Added (domain — `ontology/domain/equipment-maintenance.ttl`)

- `ent:AssetRegisterEntry` (Class, a subclass of `sbp:Record`): one row of the asset register (the master in design document section 7.1).
  It is a record that asserts equipment types and part-whole relations. The mapping (`mappings/asset_register.yaml`) turns a row of the business-side
  register into this record type, and the ingest worker converts it into `observed` assertions of `rdf:type` / `sbp:partOf` and their
  projection (the plain-triple graph `urn:sbp:g:master:<site>`) (ADR-0016). It is **held as a record type**
  so that the mapping's three preservation checks and the reliability model (source x record type) can be used as they are.


MINOR (additive). There are no changes to existing classes, relations, or shapes.

### Added (core — `ontology/core/sbp-core.ttl`)

- `sbp:Role` (Class): a business role that is the target of approval and assignment. Declared as a **subclass**
  of BFO's role (BFO_0000023), not redefined (design principle P10). Approvers are specified by role rather than by individual so that
  a change of personnel does not make an action unexecutable (design document section 9.3).
- `sbp:compensatedBy` (ObjectProperty): ActionType -> ActionType. Because registration of an action type with no declared
  compensation is rejected (action-layer rule), **the relation is held on the ontology side**.
  If it were held only on the YAML side, the presence of compensation could not be read from the ontology.

### Added (domain — `ontology/domain/equipment-maintenance.ttl`)

- 4 action-type individuals: `CreateEmergencyInspection` <-> `CancelWorkOrder`,
  `CorrectOperationLog` <-> `RevertOperationLogCorrection` (each pair is a mutual compensation relation).
- 2 role individuals: `MaintenanceLead`, `ProductionSupervisor`.

The body of the semantic contract (preconditions, expected effects, risk class, approval conditions) is placed in `actions/*.yaml`. This is to keep
threshold adjustments from becoming ontology version bumps; only "existence" and "compensation relations" are placed in the ontology.

## 0.4.0 — 2026-10-03

MINOR (additive). There are no changes to existing fluent types, event types, or shapes, so backward compatibility of probes,
queries, and `schemas/` is preserved (confirmed by the contract test `test_ontology_compat.py`).

### Added (core — `ontology/core/sbp-core.ttl`)

2 properties + 1 class + 2 individuals needed to hold inter-entity coupling (design document section 8.1, "relational constraints")
declaratively. The code side (`sbp.reasoning.coupling`) reads the coupling structure from here.

- `sbp:partOf` (ObjectProperty, TransitiveProperty): the part-whole relation of physical entities. Declared as a **subproperty**
  of BFO's `part of` (BFO_0000050), not redefined (design principle P10).
  It is one of the "strong relations" of design document section 8.1.
- `sbp:aggregatesAs` (ObjectProperty): FluentType -> AggregationRule. Declares, **per fluent type**, how the state of parts is
  aggregated into the whole. `Running` is all (the system is running only when every part is running), and `Faulted` is any
  (the system is faulted if any one part is broken). This difference is placed in the ontology rather than in code branching.
- `sbp:AggregationRule` (Class) and the individuals `sbp:AggregateAll` / `sbp:AggregateAny`.

### Added (domain — `ontology/domain/equipment-maintenance.ttl`)

- `sbp:aggregatesAs` assigned to the 7 existing fluent types (an annotation-like addition; no new types).

## 0.3.0 — 2026-10-03

Semver judgment: **MINOR** (only additive ``initiates`` / ``terminates``; no deletion
or change of meaning of existing types or relations). There are no existing probes, and the `schemas/` artifacts do not change, so
backward compatibility is not broken. Re-grounding is not needed.

### Added (L2 equipment maintenance — events that change `Accessible`)

During implementation of M2-06 (event promotion), SynthWorld's invariant test ("the synthetic world only produces
state changes that the event calculus can explain") detected a **gap in the ontology**. `Accessible` is defined as a FluentType and is used
in design document section 9.1 as a physical precondition of actions (`P(Accessible(?pump)) >= 0.8`), but
**there was not a single event type that makes it hold or stop holding**. A fluent that cannot be changed
does not take part in event-driven belief updates, and as an action precondition it stays at its initial value forever.

Following the physics, the following were added.

- `StartCmd` -> `terminates Accessible` (while running, people cannot approach)
- `StopCmd` -> `initiates Accessible` (once stopped, it can be approached)
- `Fault` -> `initiates Accessible` (a fault stop also makes it approachable)
- `MaintStart` -> `initiates Accessible`

In addition, one check was added to `make ontology-check` (item 7): **every FluentType must be changeable by at least one
EventType**. This is the dual of the existing `EventTypeShape`
(every EventType changes at least one FluentType). Only one side had been checked, which is why this gap got through.

## 0.2.0 — 2026-10-03

Semver judgment: **MINOR** (additive only; no deletion or change of meaning). There are no existing probes or queries, so
backward compatibility is not broken, and re-grounding is not needed.

### Added (L1 core — belief-metadata predicates; ADR-0005)

Predicates attached to the reifier were defined in order to represent assertions with RDF 1.2 triple terms and `rdf:reifies`.
An open interval is represented by **not placing** a `validTo` / `txTo` triple (no null literal is used).

- Bitemporal: `validFrom`, `validTo`, `txFrom`, `txTo`
- Status and confidence: `modality`, `confidence`
- Provenance: `provenance`, `sourceReliability`, `ontologyVersion`
- Prediction: `horizonOrigin`, `leadTime`
- Isolation and context: `scenario`, `inSituation`
- Modality individuals (the range of `modality`, the 6 values of invariant 3): `Observed`, `Inferred`, `Predicted`,
  `Planned`, `Normative`, `Hypothetical`

The term count went from 78 to 91.

## 0.1.0 — 2026-10-03

Initial version. Semver judgment: not applicable, as it is the initial version.

### Added (L1 core — `ontology/core/sbp-core.ttl`)

- Annotation properties: `layer`, `owner`
- Physical entities: `PhysicalEntity`, `Asset`, `MaterialItem`, `Person`, `Carrier`,
  `UnidentifiedPhysicalObject`, `Location`, `Zone`, `Anchor`
- Occurrents: `PhysicalEvent`, `BusinessEvent`, `ActionEvent`, `Situation`
- ICE: `Assertion`, `FluentType`, `EventType`, `Record`, `PlanItem`, `Norm`, `ActionType`,
  `Expectation`, `Discrepancy`, `IdentityClaim`, `Track`, `Probe`, `WorldModelVersion`,
  `OntologyVersion`, `DataSource`, `Forecast`
- Relations: `hasParticipant`, `locatedIn`, `containedIn`, `initiates`, `terminates`,
  `mutuallyExclusiveWith`, `subjectType`, `groundedBy`, `evidenceFor`, `concerns`, `boundTo`,
  `expects`, `conditionedOn`, `targets`
- Data, geometry, quantity: `assetId`, `sku`, `discrepancyKind`, `probeStatus`, `geometry`, `quantity`,
  `validInterval`

Adopted standards (not redefined): BFO 2020 (upper-level categories), IAO (ICE and is_about),
SOSA/SSN (observations and virtual sensors), PROV-O (provenance), OWL-Time (time), GeoSPARQL (space), QUDT (units).

### Added (L2/L3 equipment maintenance — `ontology/domain/equipment-maintenance.ttl`)

Per ADR-0002, the initial domain is manufacturing and equipment maintenance.

- Physical entities: `RotatingEquipment`, `Pump`, `ProductionLine`, `SparePart`
- Business records: `WorkOrder`, `OperationLog`, `MaintenancePlan`
- Observable quantities: `VibrationRms`, `MotorCurrent`, `BearingTemperature`
- Fluent types (7): `Running`, `UnderMaintenance`, `Faulted`, `VibrationAnomaly`,
  `Accessible`, `Inspected`, `Present`
- Event types (9): `StartCmd`, `StopCmd`, `Fault`, `MaintStart`, `MaintEnd`, `AnomalyOnset`,
  `AnomalyCleared`, `GoodsIssue`, `GoodsReceipt`
- Data sources: `CmmsSource`, `MesSource`, `ErpSource`

The type count is 29 (within the range of design principle P6, "initial 20-30 types").

### Added (shapes — `ontology/shapes/core-shapes.ttl`)

- `sh:Violation` (structural constraints -> quarantine queue): `AssetShape`, `IdentityClaimShape`,
  `FluentTypeShape`, `EventTypeShape`, `DiscrepancyShape`, `ProbeShape`
- `sh:Warning` (consistency constraints -> discrepancy candidates): `MutuallyExclusiveFluentShape`,
  `ExclusiveFluentCollisionShape`

### Added (event calculus — `ontology/process/equipment-maintenance.lp`)

initiates / terminates, exclusion constraints, conditional firing (during maintenance, a start command does not move to running),
declaration of delay distributions (the parameters of design document section 3.8), inertia rules, explicit derivation of exclusion violations,
and the inverse mapping for event promotion (enumeration of candidate event types).
