# ADR-0032: Forecasts are produced by a periodic job and, once the horizon has passed, scored against belief (M9-02)

- Date: 2026-11-08
- Status: Accepted
- Layers touched: reasoning (`forecasting`), knowledge (`forecasts`, `LatentIndex.latest`), scripts (`forecast`), alembic 0014

## Context

`sbp.reasoning.prediction` has the forecast types, scoring (CRPS, coverage), and the trustworthy lead time, but no one **produced and wrote** forecasts, and no one
**scored them and turned them into metrics** (the reason `forecast_coverage` / `forecast_crps` were unwired; H-24).

## Decision

1. **How forecasts are produced**: starting from the entity's latest latent, run `forecast_samples` WM rollouts (default 30) for `forecast_horizon_steps` ticks (default 30),
   and pass the sampled latents through the probe at each lead time (every `forecast_stride` ticks, plus the end of the horizon). **The probability is the calibrated probe output** (invariant 7);
   the sample distribution is the predictive distribution, the interval is quantiles, and the mean is the sample mean. The uncertainty of the origin latent is isotropic (`forecast_state_std`), because the index holds only the mean.
2. **Storage is twofold**: `predicted` assertions (the mean, per lead time, with `horizon_origin` and `lead_time`; they do not mix into main-belief queries), and
   the whole (intervals, samples) for scoring as JSON in the `forecasts` table (alembic 0014). The same `(site, entity, fluent, WM version, origin)` is one record.
   **The identifier (uuid5) and the random seed are determined from the origin.** Assertions are written first (only the missing points) and then the table, so wherever it fails, re-running completes it.
   If the latent is older than `forecast_max_origin_age_s` (default 1 hour), no forecast is produced (a belief later than the origin already exists).
3. **Scoring follows the conventions for binary events.** The observation is 0 / 1 and the forecast is a probability p (the sample mean). `forecast_crps` is the CRPS against Bernoulli(p) = `(p − y)²` (a proper score that coincides with Brier).
   `forecast_coverage` is the coverage of the **prediction set**: for nominal `level`, the set is {1} if p ≥ level, {0} if 1 − p ≥ level, and {0, 1} if neither is reached,
   and coverage is the fraction of observations that fall in the set (at or above nominal if calibrated). Passing the sample distribution (the spread of probe probabilities) to CRPS or interval coverage rewards the epistemic spread as "sharpness",
   and intervals fail to reach 0 / 1, so coverage sticks at 0 (a finding from independent review; samples are kept only to display the epistemic spread of the probability).
   The observation is the **latest posterior of the belief-worker's belief** (1 if 0.5 or above; the probe/rule `inferred` and coupling statements are not consulted). Only forecasts for which beliefs exist at all lead times are scored,
   and incomplete ones wait until `forecast_complete_wait_s` (default 1 day) (after that, they are scored with only the points available). For each entity, fluent, WM version, and lead time, metrics are written **from the forecasts scored in that cycle**
   (not cumulative; a different time adds a row). The scored mark is set only once.
4. **Job**: `make forecast` (`scripts/forecast.py`, periodic with `--watch N`). The WM is `SBP_BRIDGE_WM_VERSION` and the probe is `--probe-id` (includes the version; fails if the WM version differs).

## Consequences

- The number of wired metrics becomes 23 (the remaining 2 are identity-related, H-27).
- Forecasts remain in the record as `predicted`, and the forecast at the time can be retrieved with `as_of` (accuracy tracking).

## Limitations

- **The "observation" for scoring is belief, not ground truth.** There is no ground truth in operation (invariant 10). If the belief is wrong, the scoring is wrong too. This is consistency with the platform's belief, not accuracy against
  physics. Accuracy against ground truth is measured only in tests that use SynthWorld.
- The origin uncertainty is isotropic, and the WM ensemble (`LatentState.ensemble`) is not used. Interval coverage may deviate from nominal (the scoring measures it).
- The lead-time step is the step width of the WM's hierarchy (`mid`), and it is not guaranteed to coincide with the time spacing of the latent index (the encode window step) (align at training time).
- Entities and fluents are specified explicitly by arguments (the job does not sweep all entities in the index). Automatic entity detection is unimplemented.
- Two concurrent `forecast` runs do not duplicate the forecast for the same origin, thanks to a unique constraint. The scored mark is set only once by the `scored_at IS NULL` condition, but if both write metrics first, the same cycle's metrics can be written twice.
- Beliefs are retrieved at the current `tx`, so past forecasts may be scored against a belief corrected later (we judged that reflecting corrections is more correct as an observation).
