# ADR-0030: Start M9 "Operational Entrances" (H-24 option A)

- Date: 2026-11-08
- Status: Accepted (human decision: H-24 option A, 2026-11-08)
- Layers touched: scope (addition of a milestone to docs/TASK.md)

## Context

M8 aimed to run the 4 workers on real services, and G8 has been passed. However, some features remain that have libraries and integration tests but **no one calls them in operation**
(H-24, PROGRESS findings 103 / 110): execution of re-grounding (there is no `run(plan)`; it is a required step of version promotion OPS-06), entrances for prediction, What-if, and explanation,
5 unwired metrics, entrances for features that use an LLM, and operational paths for SHACL quarantine, identity binding, and spatial binding.

## Decision

Start M9 "Operational Entrances". The aim of M8 (run the workers) and the definition of G8 are unchanged. Priority is decided by the direction of dependencies.

| Task | Content | Metric / condition closed |
|---|---|---|
| M9-01 | Execution of re-grounding (`reground` job). Dry-run by default; `--apply` closes the `tx_to` of old statements and adds new ones. Saves a diff report as markdown | `regrounding_duration` |
| M9-02 | Periodic prediction job (create `predicted` forecasts from WM rollouts and score them with observations that arrive later) | `forecast_coverage` / `forecast_crps` |
| M9-03 | Query entrance (GraphQL: reads of predictions and explanations. What-if is a command) | Design document §10.2 |
| M9-04 | G9: an integration test that runs M9-01 to M9-03 with only the outside world simulated, and an update of the metric classification (`test_metrics_wiring`) | G9 |

**Not included in M9 (moved to H-27, still needing a human decision)**: operation of identity binding (`identity_bind_rate` / `identity_revoke_rate`; the perception
contract that supplies Track input does not exist in this repository, and binding depends on the output of the edge in the field), entrances for features that use an LLM (concept proposal, document extraction, NL→query, and prose generation for explanations; which to
introduce first, and the policy on cost and provenance, are needed), and the operational path for SHACL quarantine (the writer has not been decided).

## Consequences

- G9: G8 maintained + M9-04 green. The definition of done is the same as M8 (types, tests, `make check` green, ADR / CHANGELOG, independent review).
- Wired metrics go from 20 to 23 (`regrounding_duration`, `forecast_coverage`, `forecast_crps`). The remaining 2 (identity-related) are H-27.

## Limitations

- The re-grounding in M9-01 targets the **recomputation of beliefs** that accompanies a probe version promotion (latent → promotion events → replay of belief updates). For a WM version promotion (recomputation of latents),
  first rebuild the latents with the encode job (ADR-0023) and then run the same job. The re-grounding plan for an ontology MAJOR is separate.
