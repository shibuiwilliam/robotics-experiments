# What-if Execution Procedure (M10-03, ADR-0036)

Scope: when asking "if this action is applied, how will the fluent's probability of holding move?"
`make whatif` places the rollout with intervention next to the rollout without intervention and returns the difference.

## 0. Prerequisite: the WM must have been trained with interventions

**A WM that was not trained with interventions is rejected** (exit code 2, the reason is logged). There is no path that returns a no-intervention rollout as a What-if
(the effect of the intervention would always be 0).

1. The on-site edge adds an optional `actions` to the observation-window spool (JSONL): the same number of rows as the timestamps x the intervention dimension (finite).
   `{"entity": …, "channels": […], "timestamps": […], "values": [[…]], "actions": [[…]]}`
2. Retrain with `make train-wm` (`scripts.train_koopman`). Interventions must be **present in all rows or absent from all rows**
   (if only some rows have them, it is rejected with ConfigError).
3. With the new WM version, proceed as usual through `make encode`, probe training (`scripts.train_probe`), and promotion.

The intervention channel is "the column index of `actions`", and **the caller supplies the correspondence to the action type** (the `--action-type` IRI and `--channel`).

## 1. Execution

```sh
make whatif ARGS='--entity <entity IRI> --fluent <fluent IRI> --probe-id <probe IRI> \
  --action-type <action type IRI> --channel 0 --magnitude 1.0 --horizon 12 --seed 7'
```

- Exit codes: 0 success / 3 configuration error (e.g. probe and WM version mismatch) / 1 no latent or a stale one (`SBP_REASONING_FORECAST_MAX_ORIGIN_AGE_S`) / 2 rejected (a WM not trained with interventions, an out-of-range channel, or a fluent with no probe).
- The same question (entity, fluent, WM version, origin, action type, channel, magnitude, horizon) is never written twice.

## 2. Reading the result

- Read the curves via the GraphQL `scenarios` (requires permission for `hypothetical`; it is not visible to the default requester). `baseline` (no intervention), `intervened` (with intervention), `delta` (the difference).
- The result statements are written as `hypothetical` to `urn:sbp:scenario:<id>` and **do not mix into queries against the main beliefs**. There is no entry point for `promote_scenario`, the promotion to main beliefs.
- **Do not use it as a causal claim.** The effect comes from the WM's linear control term. The number of observed interventions (validity) is not counted (`ActionEvent` is not persisted; H-07).
  There is no entry point for a What-if on a plan diff, because the conditioning has not been trained.
