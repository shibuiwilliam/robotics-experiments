# PREREG.md — Musubi Pre-Registration

Hypotheses, primary endpoints, exclusion + stopping rules — committed **before** running (research
self-binding, PROJECT.md §15.2). No post-hoc endpoints.

> Status: E0 registered and passing. E1–E7 endpoints are `[PROVISIONAL]` pending the Experiment
> Plan canonical doc (absent); they are grounded on PROJECT.md §12.2 and the Scenario Catalog E-map.

## Primary endpoints (E7 integration, PROJECT.md §12.2)

Evaluated across the A0→A4 ladder; numbers finalized after E0 measurement:

1. **Order-fulfilment rate** — A4 significantly higher than A0.
2. **Unapproved irreversible actions = 0** — must hold for A3/A4 (hard invariant).
3. **Human interventions** — count.
4. **Tokens / decision** — A4 lower than A0 (context economy).

Secondary: MTTC, explanation-chain completeness, IRI-hallucination rate, total cost.

## E0 (smoke) — registered

- **H0:** the full stack runs end-to-end at A4, offline, deterministically.
- **Endpoints (all must hold):** oracle-pass = 1.0 at A4; unapproved-irreversible = 0;
  trace-completeness ≥ 0.95; API calls (replay) = 0; bit-identical qpos across repeated seeds.
- **Arms:** A0–A4. **Seeds:** 0,1,2. **Dial:** oracle. **Planner:** ScriptedPlanner.
- **Result:** PASS (see `scoreboard/reports/E0_report.md`).

## Exclusion / stopping rules

- Exclude a run if the sim diverges from its seed (determinism check fails) — indicates a
  non-determinism leak (bug), not data.
- Stop live recording immediately on daily-budget breach (config/registry.yaml).
- A scenario whose oracle cannot be machine-decided is a demo, not a benchmark — excluded from
  endpoints (Scenario Catalog §0 principle 3).
