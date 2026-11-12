# ADR-0002: Make "manufacturing / equipment maintenance" the initial domain

- Date: 2026-10-03
- Status: Accepted
- Layers touched: ontology / bridge / reasoning (validation scenarios in general)

## Context

Design document §1.2 lists four candidate initial domains (manufacturing / equipment maintenance, warehouse / inventory, facilities / energy, logistics yard). Following design principle P6 "minimal ontology, observation-driven growth" and §16 Phase 0 "one domain, 20–30 types", the initial scope must be narrowed to one domain. This is an open question from `docs/TASK.md` §7-2.

This choice determines the content of `ontology/domain/`, the subject matter of `SynthWorld` (M2-01), and the validation scenarios for gates G3 and G5.

## Options

1. **Manufacturing / equipment maintenance** — Physical: vibration, current, temperature, PLC state. Business: CMMS work orders, MES operating records, maintenance plans. Discrepancies targeted: state discrepancy (running / stopped), unperformed work.
2. Warehouse / inventory — Discrepancies targeted: existence discrepancy (phantom inventory), identity discrepancy.
3. Facilities / energy — Discrepancies targeted: normative discrepancy (contract overrun).
4. Logistics yard — Discrepancies targeted: temporal discrepancy, early warning of SLA violation.

## Decision

We adopt Option 1, "manufacturing / equipment maintenance". Reasons:

- **The worked example in design document §14 (pump P-101 on line 3) is in this domain.** §14 describes the full sequence from observation, through three-source integration, discrepancy filing, confirmation, prediction and action, to Expectation verification, with concrete values, and this **can be used as-is as the validation scenario for gates G3 and G5** (TASK.md M3-09 / M5-09). Choosing another domain would mean designing validation scenarios from scratch, weakening the correspondence check against the design document.
- **The event calculus example (§3.8) is written for equipment maintenance.** `Running` / `UnderMaintenance` / `StartCmd` / `StopCmd` / `Fault` / `MaintStart` / `MaintEnd` and even the delay distributions are already given, so the initial implementation of `ontology/process/*.lp` can be started directly.
- **The world model family is clear.** §4.2 recommends a Koopman operator model for the equipment/process domain, noting that "Koopman eigenfunctions map easily onto fluents, and the consistency residual is easy to interpret". The koopman implementation of M4-02 and the initial domain coincide.
- **It works with multivariate time series alone.** Because no video is needed, friction with invariant 9 (do not persist raw video; do not handle data that shows people) is minimal. Warehouses and logistics yards presume cameras, and appearance embeddings become involved in identity binding (M2-07), which makes the load in early development high.
- §14 also touches on an existence discrepancy for spare-parts inventory (SKU-77), so **elements of the warehouse domain (type 1) can be included at minimal cost**. The diversity needed to validate the detection rules for the five types (M3-05) can be secured.

## Consequences

- Impact on invariants: works to minimize friction with invariant 9 (no persistence of raw video). Others unaffected.
- Ontology version: determines the content of the first version of `ontology/domain/` (the starting point of a MINOR additive change). Re-grounding is not needed (no existing data).
- Impact on testing and operations: `SynthWorld` generates multivariate time series corresponding to vibration, current, and PLC state. G3 is accepted on injected scenarios for types 1–3, and G5 on running §14 to completion.
- Adding a second domain (warehouse / inventory) corresponds to Phase 3 (§16). When it is added, it will be an opportunity to verify that "adding new concepts does not break existing probes" (the extensibility non-functional requirement).

## Open questions

None. The timing of adding a second domain will be decided in a separate ADR after M6.
