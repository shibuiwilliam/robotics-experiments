# Version Promotion

Scope: when raising any of the ontology version, WM version, probe version, or mapping version.
Corresponds to design document section 12.4 (integration lifecycle), `src/sbp/worldmodel/registry.py`, and
`src/sbp/knowledge/regrounding.py`.

**The 4 versions cannot be raised independently.** A probe is trained against the latents of a specific WM version,
a WM version is tied to ontology concepts, and a mapping is tied to record types. Whichever you raise goes through the same pipeline.

## Pipeline (design document section 12.4)

```
impact analysis -> canary -> re-grounding -> diff review
```

No stage can be skipped. In particular, **do not skip the canary and release to everyone**
(`ModelRegistry.promote` rejects a direct `candidate -> active` transition).

## 1. Impact analysis

```python
from sbp.knowledge import CompatibilityMatrix

matrix = CompatibilityMatrix()
# Existing verified combinations (persist them in operation)
matrix.record(ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0")

# Find the combinations that include the version being raised
print(matrix.affected_by(world_model="koopman/1.0.0"))
```

Re-verify every combination found in the next stage.

## 2. When raising the ontology version

```sh
# 1. Edit the TTL (ontology/core/*.ttl, ontology/domain/*.ttl)
# 2. Write the semver judgment in the CHANGELOG (MAJOR requires an ADR and a re-grounding plan)
# 3. Update both VERSION and owl:versionInfo
#    (ontology-check detects a mismatch)
make gen             # Regenerate vocab.py and schemas/
make ontology-check  # 7 items + version match
make check           # Contract tests check backward compatibility
```

**Put the generated diff in a separate commit** (CLAUDE.md). `vocab.py` and `schemas/` are generated artifacts.

```sh
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp"   # Example for a port conflict
make seed            # Load the new version into the store
uv run pytest tests/integration -m integration   # All tests must pass (the G0-G7 and M8 integration tests)
```

**If you forget `make seed`, the G0/G1/G2 version checks fail.** The store alone is left claiming an old version.

## 3. When raising the WM version or probe version

```python
from sbp.worldmodel import CompatibilityEntry, ModelRegistry, ModelStatus

registry = ModelRegistry()
entry = registry.register(new_model.meta)       # Registered as a candidate

# Evaluate and record compatibility with the probe (**even with the same dimensions, the meaning of the latents is not the same**)
entry.record_compatibility(CompatibilityEntry(
    probe_id="probe:running", auroc=measured_auroc, ece=measured_ece,
    align_residual=measured_residual, evaluated_at="2026-10-03T00:00:00Z",
))

# Canary (some sites, some concepts)
registry.promote(model_id, version, ModelStatus.CANARY,
                 required_probes=["probe:running"],
                 min_auroc=0.85, max_ece=0.05, max_residual=0.2)
```

Promotion is rejected if any probe is unevaluated. The gate is the **AND of AUROC, ECE, and consistency residual**
(never make a version active on static accuracy alone; this is a prohibition in CLAUDE.md).

Metrics to observe during the canary:

| Metric | What to look at |
|---|---|
| `sbp_worldmodel_latent_shift_ratio` | Whether the latent distribution has drifted from training time |
| `sbp_worldmodel_pred_error_ratio` | Whether prediction error has worsened |
| `sbp_grounding_probe_ece_ratio` | **Whether calibration has collapsed** (a partial collapse does not show in AUROC but shows in ECE) |
| `sbp_reconciliation_discrepancies_raised_total` | Whether filings have surged or plunged |

```python
# To everyone
registry.promote(model_id, version, ModelStatus.ACTIVE,
                 required_probes=["probe:running"],
                 min_auroc=0.85, max_ece=0.05, max_residual=0.2)
# Retire the old version (**keep it loadable**. It is needed to explain past estimates)
registry.promote(old_model_id, old_version, ModelStatus.DEPRECATED,
                 min_auroc=0.0, max_ece=1.0, max_residual=1.0)
```

## 4. Re-grounding

Follow `docs/runbooks/regrounding.md`. Promoting a WM version means **recreating the latents themselves**
(`RegroundingTrigger.WORLD_MODEL_VERSION.invalidates_latents` is true), so
it is orders of magnitude heavier than promoting a probe version. Always check the estimate.

If the latent dimension changes, **a new table is required**.
Writes for a dimension not registered in `latent_index_registry` do not succeed
(the application does not create tables at runtime; cut a migration with alembic).

## 5. Diff review

A human checks the re-grounding diff report (`report.markdown()`).
If `needs_review` is true, hold off on adoption and record the grounds for the decision.

## 6. Record

```python
matrix.record(ontology="0.5.0", world_model="koopman/2.0.0", probe="2.0.0", mapping="1.0.0")
```

**If you forget to register it, `require_verified` will not pass at the next promotion.** This is intended friction
(a combination with no record of having been verified is not released to production).

## Rollback

Old versions remain loadable even when `deprecated`.

1. Return the old version from `canary` to `active` (it cannot be returned directly from `deprecated`, so
   **register it as a new version** instead of re-registering; rolling a version back makes it ambiguous which weights produced a belief)
2. Close the `tx_to` of assertions added by the new version (do not delete them)
3. Append "the reason for the rollback" to the diff report

**Do not erase past beliefs.** Keep it possible to reconstruct, via `as_of`, the beliefs from "the period when the new version was in use."
