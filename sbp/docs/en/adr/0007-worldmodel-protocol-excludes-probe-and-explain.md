# ADR-0007: Remove `probe` / `explain` from the `WorldModel` Protocol, and return attribution in a form that does not know symbols

- Date: 2026-10-03
- Status: Accepted
- Layers touched: worldmodel (Protocol), bridge (the side that applies probes)

## Context

Design document §4.3 defines the common interface of `WorldModel` as follows.

```text
interface WorldModel {
  encode(obsWindow, context?)  -> LatentState
  step(z, action?, context?)   -> LatentState
  rollout(...)                 -> Trajectory[]
  probe(z, probeIds[])         -> { probeId: Distribution }
  explain(z, probeId)          -> Attribution
  embed(z, level)              -> Vector
  health()                     -> { latentShift, predError, alignResidual }
}
```

Of these, `probe` and `explain` **take a probe ID as an argument**. When trying to implement this as-is in M4-01, it turned out to conflict with three rules.

1. **CLAUDE.md layer dependencies**: `core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge`. `worldmodel` cannot import `bridge`. The probe types (`ProbeSpec`, `LinearProbe`, `Calibrator`) all live in `bridge`.
2. **CLAUDE.md layer responsibilities**: "`worldmodel` does not decide symbols (probes belong to `bridge`)".
3. **`.claude/rules/bridge-worldmodel.md`**: "Output must always pass through `Calibrator.apply`, and be returned with epistemic and aleatoric uncertainty separated". The calibrator is attached to the probe, and the promotion gate (AUROC ∧ ECE ∧ consistency residual) is also on the probe side.

To put `probe()` in worldmodel, one of the following is required: (a) move the probe types down to `core`, (b) have `worldmodel` import `bridge`, or (c) inject probes as callables.

## Options

1. **Remove `probe` / `explain` from the Protocol, and return attribution as "the contribution of latent dimensions to a linear functional".** `bridge` applies probes by taking a `LatentState`.
   - Benefits: layer dependencies are kept as they are. Model families can be implemented without knowing that probes exist, and adding, promoting, or retiring a probe has no effect on the WM. Attribution (which latent dimensions mattered) is information only the WM can compute, so if it is received in a form that does not know symbols (a weight vector), it stays within the WM's responsibility.
   - Drawbacks: departs from the description in design document §4.3. The caller's invocation becomes two steps (`wm.encode` → `probe.predict`).
2. Move the probe types down to `core`.
   - Benefits: `probe()` can be placed as the design document describes.
   - Drawbacks: `core` is "types and minimal utilities", and the implementation of grounding, including calibrators, promotion gates, and MIL loss, would come to live there. `core` would depend on numpy and training code, undermining the meaning of the layer. If the promotion gate were in `core`, there would be a path to make a probe active without going through `bridge`.
3. Inject probes into the WM as callables (`register_probe(probe_id, callable)`).
   - Benefits: keeps the design document's call shape (`wm.probe(z, ids)`). No type dependency arises either.
   - Drawbacks: the WM would hold state about "which probes are registered". For the same latent, the model would return different results depending on registration state, and reproducibility of "model version + latent → output" would break (the as-of explanation in design document §8.8 would not hold). A missed registration becomes a silent omission.

## Decision

**We adopt Option 1.**

The methods of the `sbp.worldmodel.base.WorldModel` Protocol are `encode / step / rollout / embed / health / attribute`. `probe` and `explain` are not provided.

- Applying probes is done by `bridge`. `LinearProbe.predict(latents)` is aligned to take `LatentState.z` (or an ensemble).
- Attribution is `attribute(state, weights) -> Attribution`. `weights` are the coefficients of a linear functional (a linear probe's weights go in as they are), and the WM does not know which symbol they represent. The returned `Attribution` carries contributions to latent dimensions, modalities, and the observation window.
- The intent of design document §4.3 ("one can obtain the output from latent to concept, and its attribution") is satisfied by the caller's two-step structure. The `LatentSide` that the explanation service (`sbp.reasoning.explain`) receives carries the probe ID and output, the WM version, and the attribution, so **the amount of information in the explanation as seen by the user does not change**.

## Consequences

- Model families (`koopman`, `jepa_ts`) can be implemented and tested without knowing anything about probes. G4's "two model families are interchangeable under the same Protocol" can be demonstrated independently of probes.
- Whether "probes are embedded in the WM" can be checked mechanically by the import-boundary lint (import-linter). It fails if `worldmodel` → `bridge` imports increase.
- The interface description in design document §4.3 is to be read as updated by this ADR. A reference to the ADR is added at the relevant place in the design document itself.
- The `Attribution` type lives in `worldmodel`. `bridge` uses it, attaching a `probe_id`.

## Open questions

- **Attribution for nonlinear probes**: `attribute()` presumes a linear functional. When a nonlinear probe becomes necessary (`.claude/rules/bridge-worldmodel.md` states they may be used only "after logging that the latents are not linearly separable"), we will decide whether to extend it to accept gradient-based attribution or make it a separate method.
