# Operating the Head of the Estimation Path (Encode Job)

ADR-0023. Trains Koopman from observation windows (JSONL spool) placed by the on-site edge, and writes latents to `latent_index`.
The observed values themselves are not persisted (invariant 9).

## Prerequisites
- Compose is running (postgres, objectstore) and `make seed` has been run.
- `.env` has `SBP_WM_SPOOL_DIR` (where the spool is placed) and `SBP_BRIDGE_WM_VERSION` (`<model IRI>/<version>`). There are no defaults.

## 1. Training (once per version; never overwrite the same version)
```
make train-wm SEED=1
```
- Weights are placed at `models/<model>/<version>/koopman.npz`. If that version already exists, training fails. To fix something, cut a new version.
- If the spool is empty, missing, or its channels disagree, it stops with `ConfigError`.

## 2. Encoding
```
make encode            # once
uv run python -m scripts.encode --watch 30   # every 30 seconds
```
- Idempotent through a per-entity watermark. The watermark advances after writing, so if the job dies midway the same window is not written again.
- The output `EncodeReport`: `rows_written` / `skipped_processed` / `skipped_short` / `skipped_mismatch` / `rejected` / `health`.
  If `rejected` grows, return the malformed spool lines (`file:line: reason`) to the site.

## On failure
| Symptom | Cause | Action |
|---|---|---|
| `no spool directory` | `SBP_WM_SPOOL_DIR` is not set | Set it in `.env` |
| `SBP_BRIDGE_WM_VERSION` error | Version is not set | Set it and re-run |
| Weights not found | Before training | `make train-wm` |
| `ArtifactImmutableError` | Retraining the same version | Cut a new version |

## 3. Probe training (grounding path; ADR-0024)
Prerequisite: latents are in the index from the encoding above, and the business assertion events (`BusinessEvent/<fluent>`) are
flowing on `<site>.integration.business`.
```
make train-probe FLUENT=<fluent type IRI> PROBE_ID=<probe IRI (including version)> SEED=1
```
- If it passes the promotion gate (AUROC, ECE, consistency residual), the bundle is placed in the store location, and you can write it to `SBP_BRIDGE_PROBE_IDS` and start bridge-worker.
- If it does not pass, **nothing is placed** and it exits with code 1. `probe_auroc` / `probe_ece` are recorded. Look at `reasons` in the log.
- "Not enough samples in each bin" means a shortage of weak labels. Wait until assertion events accumulate, or revisit `SBP_BRIDGE_PROBE_MIN_LABELLED` (an ADR is required to loosen it).
- The same version is never overwritten (`ArtifactImmutableError`). To retrain, use a new-version `PROBE_ID`.
