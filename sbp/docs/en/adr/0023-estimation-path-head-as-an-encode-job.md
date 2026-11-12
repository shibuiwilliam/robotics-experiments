# ADR-0023: The head of the estimation path is an "encode job"; observations are received from a spool, and WM weights are serialized to the store

- Date: 2026-11-08
- Status: Accepted
- Layers touched: worldmodel (weight serialization, encoding), knowledge (use of writes to the latent index), scripts (`encode` / `train_koopman`)

## Context

At the head of the estimation path of design doc §2.5 (observation -> latent -> probe -> event promotion), **nobody runs
observation window -> `WorldModel.encode` -> `LatentIndex.write` in operation** (finding 101). Code that writes to the latent index exists only in `test_bridge_worker_e2e` / `test_g4_worldmodel`, and
in a fresh environment the latents the bridge-worker reads are empty. M8-24 was the task of deciding in an ADR "where to run the encoder".

Facts confirmed:

1. Design doc §12.1 places the encoder at the **edge**. The edge is the layer close to the on-site sensors, and lies outside this repository
   (ADR-0001: use-case validation and real hardware are in a separate repository).
2. `KoopmanModel` is numpy only, and **has no path to serialize trained weights** (`WorldModelRegistry` manages only location and version.
   A known limitation in PROGRESS). Without serialization, encoding cannot be done outside the process that trained.
3. There is no contract for the observation entry point. `EventEnvelope` carries claims, not an observation window (a numeric array).
4. Invariant 9: Do not persist raw video or audio. Only latents and symbols are persisted.

## Options

### Question 1: Where to run the encoder

1. **An "encode job" in this repository (`scripts/encode.py` + `sbp.worldmodel.encoding`).** Make the observation-window entry point a
   Protocol (`ObservationSource`), and have the on-site edge place windows in a **spool** (JSONL). The job reads windows,
   loads the WM weights, runs `encode`, and writes to the latent index. No new process kind (worker) is added.
2. A seventh worker following the sixth. It would be a resident service, but fixing the worker's shape (period, readiness) before the arrival method of observations (spool, stream)
   is decided would be guesswork. A job can be started from cron or an edge hook.
3. Embed it in front of the bridge-worker. The bridge-worker is a "read-only" layer (latent -> probe -> event), and giving it weight loading and
   an observation entry point would mix responsibilities.
4. Leave everything outside this repository (the edge) and decide only the entry-point contract. The contract (the latent index table and WM version registration) is held by this repository, so
   in the end the edge would call `LatentIndex.write`, making the edge depend on this repository's internal API.

### Question 2: The observation entry point

1. **A spool (JSONL).** One line = one window (entity, channel names, time, values). Files are placed by the on-site edge, and the job
   is read-only. Provide a reference implementation `JsonlObservationSource`, and swap in other entry points (stream, DB) via the Protocol.
2. A Kafka observation topic. Requires a contract change to carry arrays in `EventEnvelope`.

### Question 3: Serializing WM weights

1. **numpy `.npz` (`allow_pickle=False`) + metadata.** Arrays suffice for Koopman. pickle is not used (arbitrary code can run on load).
   Validate the metadata (model ID, version, channels, configuration), and forbid `latest` as a version (same convention as `WorldModelRegistry`).
2. torch `state_dict`. For jepa_ts. Out of scope this time (Koopman only).

## Decision

Question 1: an encode job. Question 2: a spool (JSONL). Question 3: `.npz`.

- `sbp.worldmodel.serialization`: `dumps(model) -> bytes` / `loads(data) -> KoopmanModel`. The store is `ArtifactStore`
  (`models/<model-slug>/<version>/koopman.npz`). Untrained models are not saved. Loading validates version, channels, and dimensions, and
  fails on a mismatch.
- `sbp.worldmodel.encoding.EncodeRunner`: window -> latent (all times within the window at which the embedding is filled) -> `LatentIndex.register` / `write`.
  A **per-entity watermark** (`WatermarkStore`, worker name `encoder`) prevents re-encoding windows at or before the processed time
  (writes to the latent index overwrite, so nothing breaks, but it is wasteful). **Advance the watermark after writing** (finding 52).
- The window values themselves are not persisted (latents only; invariant 9). The spool is the on-site store, and the job neither deletes nor rewrites it.
- Training is `scripts/train_koopman.py` (`fit` on the spool's historical windows -> serialize -> `ArtifactStore.put`, no overwriting).
- Health (`latent_shift` / `pred_error` of `health()`) and inference latency are put in the job's return value (M8-15 writes them to metrics).

## Consequences

- **Invariant 5**: The WM version and probe IDs come from configuration (`SBP_BRIDGE_WM_VERSION`). No IRIs are hardcoded.
- **Invariants 6 and 10**: The job does not read ground truth (`sbp.testing`) (`scripts` is subject to import-linter). Simulating windows is on the on-site side (tests).
- **Invariant 9**: Observation values are not persisted.
- **Configuration**: Worker count stays at 6. No alembic change. The spool location is passed via configuration (no default is set).
- **Tests**: Serialization round trip, rejection of tampering, rejection of version mismatch; watermark idempotency; writing to a real Postgres -> readable by the bridge-worker.

## Open questions

- **Connecting real sensors for observation.** The part where the on-site edge (outside this repository) places windows in the spool.
- **Serialization for jepa_ts.** The format for torch weights. Decide once operation runs on Koopman.
- **Spool retention and cleanup.** Deleting or archiving processed windows is edge-side operation.
- **Running multiple sites and multiple WM versions in parallel.** The assumption is to start a job per version, but canary switching is not designed.

## Ordering promise (addendum to M8-24-6)

- Within a run, windows are processed in order of start time. Even if windows of the same entity arrive out of order, none is dropped.
- A window that arrives **later in a different run** than the watermark is not written (the price of monotonicity). The edge either includes late windows in the same run's input
  or re-encodes with a new WM version.
