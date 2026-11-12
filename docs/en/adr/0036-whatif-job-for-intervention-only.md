# ADR-0036: What-if is introduced, via a job and a read, only for "WMs trained with interventions"

- Date: 2026-11-08
- Status: Accepted (M10-03 / M10-04 of ADR-0035)
- Layers touched: worldmodel (spool intervention columns, `action_dim`), reasoning (job), knowledge (scenario table), api (`scenarios`)

## Context

`run_scenario` / `encode_intervention` / `promote_scenario` exist, but no one calls them in operation. Before building the entrance, we checked in the code **whether What-if is meaningful with the operational WM**:

1. **Intervention (`INTERVENTION`)**: Koopman rejects `step(action=...)` unless it was trained with interventions (`_control is None`). This repository's training job (`train_koopman`) fits
   only on the spool's observations and passes no intervention columns. **A WM built in operation currently always rejects interventions.**
2. **Plan diff (`PLAN_DIFF`)**: conditioning merely adds a deterministic feature vector to the tail of the latent, and how it takes effect is **not learned** (docstring of `worldmodel/context.py`; training is stage 2 of design document §4.5).
   A plan-diff What-if is a number that "moves by the amount of the added feature", not a prediction of physical effect. Emitting it would make the intervention effect a non-zero lie (the flip side of the form warned against at the top of `whatif.py`).
3. **Counterfactual (`COUNTERFACTUAL`)**: the validity score requires the number of observations of the intervention. `ActionEvent` is not persisted (H-07), so the count cannot be computed and is always 0 (extrapolation).

## Decision

1. **Only `INTERVENTION` is introduced.** No entrance is built for `PLAN_DIFF` and `COUNTERFACTUAL` (reasons in 2 and 3 above; re-open when conditioning is learned and when `ActionEvent` is persisted).
2. **Run only with a WM trained with interventions.** Before running, check `KoopmanModel.action_dim` (the number of control-term columns; `None` if absent), and if `None`, **refuse with the reason stated** (exit code 2). We do not build a path that
   refuses nothing and returns a rollout without intervention.
3. **Enable training with interventions from the spool.** The spool's observation window is allowed an optional `actions` (rows of the same length as the timestamps × intervention dimension), and `train_koopman` passes it. The intervention columns are placed by the edge in the field (outside this repository).
   `channel` is "the column index of that matrix", and the mapping to the action type is supplied by the caller (the `--action-type` IRI and `--channel`).
4. **Shape of the result.** From the latest latent at the origin, with the same `seed`, roll out two runs, **without intervention (baseline)** and **with intervention**, and return the difference in fluent probability after the probe, per lead time.
   The `hypothetical` assertions (with intervention) are written to `urn:sbp:scenario:<id>` and are not put into the main belief (invariant 3). No `promote_scenario` entrance is built (adoption is a human decision and needs a separate command design).
5. **Scenario index table `scenarios`** (alembic 0017). The identifier is a uuid5 determined from `(site, entity, fluent, WM version, origin, action type, channel, magnitude, horizon)` (re-running does not accumulate duplicates).
   Reads are via GraphQL `scenarios`. `hypothetical` is invisible by default under ABAC (only to requesters whose permissions include the modality `hypothetical`).

## Consequences

- What-if can be called in operation. However, while there is no training data with interventions, it refuses — we make it visible, through the refusal reason and exit code, that **refusal is the operational default**.
- Limitations: only one-hot-like encoding (a single magnitude channel). The validity of the intervention effect (number of observations) is not produced. No UI is built.

## Open questions

- The condition for introducing plan-diff What-if (learning the conditioning; design document §4.5 stage 2), and the condition for counterfactuals (persisting `ActionEvent`; H-07).
