# ADR-0024: The probe bundle is built by a "training job" from weak labels, and only bundles that pass the gate are placed in the store

- Date: 2026-11-08
- Status: Accepted
- Layers touched: bridge (training library), scripts (`train_probe`), core (settings)

## Context

The bridge-worker reads probe bundles from the store (ADR-0009). **No operational code places the bundle** (finding 100), so in a fresh environment
construction fails. The parts for training, evaluation, promotion, and serialization (`fit_with_bags` / `promote` / `bundle_probe`) exist, but nobody wires them together.
The sources of weak labels are the business records' claim events (`BusinessEvent/<fluent>`, ADR-0016) and human answers (ADR-0018).
Latents are written to the index by the M8-24 encode job.

## Decision

1. **The training library is `sbp.bridge.training` (pure), and the operational entry point is `scripts/train_probe.py`.** Because of the layer direction
   (integration cannot import bridge), the conversion from claim events to `TimeBag` is also placed in bridge.
2. **Weak labels**: from the claim event's probability p (the value passed through the reliability model). p > 0.5 is a positive bag, p < 0.5 is a negative bag,
   and p = 0.5 carries no information and is not used. The weight is `|2p − 1|` ((0, 1]). The interval is the start of valid time ± max(uncertainty width, floor).
   The floor is the setting `weak_label_min_half_width_s` (no point-in-time bags are created).
3. **Three-way split in time order**: cut the latent time axis from the front into train / calibration / evaluation (default 0.6 / 0.2 / 0.2). **Do not split randomly**
   because neighbors in the time series leak and evaluation becomes optimistic. A bag is assigned to the slot to which the end of its interval belongs.
4. **Evaluation labels are derived from weak labels** (only samples that fall in bags. Inside a positive bag = true, inside a negative bag = false). Ground truth (the oracle) is
   not read (invariant 10). Since every sample inside a positive bag is treated as true, AUROC comes out conservative. This is recorded as a limitation in
   `ProbeMetrics.n_samples` and the log, and the gate is not loosened.
5. **Do not place anything that does not pass the gate.** If `promote()` raises, no bundle is created and the exit code is nonzero, and only `probe_auroc` / `probe_ece`
   are recorded (so the reason for the failure is visible). If it passes, `ArtifactStore.put` (no overwriting).
6. **The alignment residual** is the value of `alignment_residual` against the symbolization (threshold 0.5) of the evaluation slot. It counts flicker.
   If there is no existing active to compare against, the median condition is not applied (the existing specification of `promote`).
7. Inputs: claim events are read through from the start of Kafka **with a new consumer group** (without affecting operational consumers).
   Latents come from `LatentIndex.since`.

## Consequences

- The path to start the bridge-worker becomes an operational procedure. When data is insufficient (each slot has fewer than `probe_min_labelled` samples of both classes),
  no bundle is placed and the reason is given.
- Limitation: the coarseness of weak labels (4). The validity of thresholds on real data awaits real-hardware validation in the H-01 family.

## Open questions

- How to bundle several fluents into one training run (currently one fluent per run).
- Operation of swapping after promotion (marking the existing active as deprecated) follows the version-promotion runbook.

## Addendum (M8-23-5, independent review)

- The calibration and evaluation slots assign bags by the **end of the interval** (the same claim does not enter both). Training uses bags that finish by the start of the calibration slot.
- The comparison target for the promotion's alignment residual is the residual of the active probe of the same WM version listed in `SBP_BRIDGE_PROBE_IDS` (read from the store).
- When data is insufficient, only one polarity is present, a bag hits no samples, or the residual cannot be measured, it is **rejected** (no bundle, with a reason). It does not crash.
- Metrics (`probe_auroc` / `probe_ece`) are recorded before the bundle is placed.
