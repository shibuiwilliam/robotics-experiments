# Re-grounding Procedure

Scope: when raising the version of a probe, WM, or ontology, or when asked for a retrospective investigation of discrepancies.
Corresponds to design document section 6.8 and `src/sbp/knowledge/regrounding.py`.

**This procedure must be readable and executable by a third party (a different session or owner)** (a condition of gate G7).

## 0. Check the prerequisites

```sh
make up                      # Compose (redpanda / postgres / oxigraph / objectstore)
docker compose -f infra/compose.yaml ps   # The 4 services are running
make check                   # lint + typecheck + ontology-check + unit tests
```

If ports conflict with other projects, move them aside in `.env` (`docs/runbooks/local-setup.md`).
The commands below explicitly set `SBP_PG_DSN` / `SBP_S3_ENDPOINT` as an example for a conflict.

## 1. Confirm what changed

There are 4 versions, and they **cannot be raised independently** (design document section 12.4). The scale of re-grounding depends on which one changed.

| What changed | Latent recomputation | Scope of impact |
|---|---|---|
| Ontology version | Not needed | Only probes for the new concepts |
| Probe version | Not needed | Only the concepts that probe judges |
| WM version | **Needed** | All probes (the meaning of the latents changes) |
| Mapping version | Not needed (outside re-grounding) | Redo ingestion of business records |

```sh
cat ontology/VERSION
uv run python -c "from sbp.core import vocab; print(vocab.ONTOLOGY_VERSION)"
```

If these two disagree, run `make gen` and `make seed` before re-grounding
(`make ontology-check` detects the mismatch).

## 2. Impact analysis (version compatibility matrix)

```python
from sbp.knowledge import CompatibilityMatrix

matrix = CompatibilityMatrix()
# Register verified combinations (persist them in operation)
matrix.record(ontology="0.5.0", world_model="koopman/1.0.0", probe="1.0.0", mapping="1.0.0")

# When raising the WM, find the combinations that need re-verification
affected = matrix.affected_by(world_model="koopman/1.0.0")
print(affected)
```

**Do not release unverified combinations to production.** `require_verified()` raises an exception.

## 3. Plan and cost estimate

```python
from datetime import timedelta
from sbp.core import vocab
from sbp.core.time import Interval, now
from sbp.knowledge import RegroundingTrigger, plan_regrounding

plan = plan_regrounding(
    trigger=RegroundingTrigger.PROBE_VERSION,
    reason="Promote the Running probe to v2 (ECE improved from 0.0064 to 0.0041)",
    entities=["https://sbp.example/site/siteA/asset/P-101"],
    fluents=[vocab.ent.Running],
    interval=Interval(now() - timedelta(days=7), now()),
    from_version="probe/1.0.0",
    to_version="probe/2.0.0",
)
print(plan.estimate.explain())
```

**If `estimate.is_large` is true (estimate of 1 hour or more), obtain approval.** Consider splitting the period
into multiple runs: a job stopped midway leaves no half-finished state, but redoing it costs time.

An unbounded interval (`interval.end is None`) is rejected. A job whose scale is unknown cannot be stopped safely.

## 4. Execution

Execute with `make reground` (`scripts/reground.py`). It reads latents from the latent index and replays
**latent -> probe -> event promotion -> belief update** from the beginning with the new-version probe, then compares against current beliefs (ADR-0031).
**The default is dry-run** (nothing is written). Check the diff, then add `--apply`.

```sh
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp"   # Example for a port conflict
export SBP_S3_ENDPOINT="http://localhost:19000"
# 1. dry-run (writes nothing. Save the report and read it)
make reground ARGS="--entity <entity IRI> --fluent <fluent IRI> \
  --start 2026-11-01T00:00:00+00:00 --end 2026-11-08T00:00:00+00:00 \
  --probe-id <new probe IRI> --from-version probe/1.0.0 --to-version probe/2.0.0 \
  --reason 'reason' --report /tmp/regrounding.md"
# 2. After checking the diff, run the same command with --apply added
```

When the dry-run exit code is 1, **the diff exceeds the threshold and human review is required** (step 5). `--apply` always exits 0.
`--apply` rejects a period whose end extends into the future. **Avoid apply while belief-worker is running** (stop it, or set the end before the watermark).

- Old statements only have their `tx_to` closed; they are not deleted (invariant 2). New statements are added with `valid_from` left in the past and `tx_from` set to the execution time.
- Only statements that belief-worker wrote from observations and assertions are targeted. Human corrections, coupling statements, and other provenance are not closed.
- The replay uses batch boundaries close to live (`SBP_REASONING_BATCH_SIZE`), but **an exact match is not guaranteed**. Live batch boundaries vary with startup time and restarts, so change points at boundaries can appear in the diff.
  The diff includes differences from the version change and from the batch boundaries. A human reads it and decides.
- **When raising the WM version**, first create the new version's latents with `make encode`, set `SBP_BRIDGE_WM_VERSION` to the new version, then run.
- Assertions (business records) are read from the business topic of the event log from the beginning. Periods that have been deleted after the retention period are replayed without assertions (this shows up in the diff).
- In place of an execution record, the integration test goes through the same path: `uv run pytest tests/integration/test_reground_e2e.py -m integration`.

## 5. Review the diff report

```python
from sbp.knowledge import summarise_regrounding

report = summarise_regrounding(
    plan, diffs=diffs, reassessed=reassessed,
    started_at=started, finished_at=finished, closed_assertions=closed,
)
print(report.markdown())
```

The report always includes the following.

- The version change (`from -> to`) and the reason
- Estimate versus actual (elapsed time, number of statements reassessed)
- **The list of diffs that exceeded the threshold** (by default, a confidence change of 0.1 or more)
- The number of statements retroactively added and removed
- Newly filed and vanished discrepancies
- How old statements were handled (the number whose `tx_to` was closed; **0 deletions**)

**If `needs_review` is true, a human decides.** Whether a large movement in confidence means
"the model got better" or "the new version is broken" is not something to adopt automatically.

Rules of thumb for judging:

| Sign | Interpretation |
|---|---|
| Confidence moved substantially in one direction and came closer to the correct answer in an interval where ground truth is known | Improvement |
| A large number of new statements appeared | The probe may be firing excessively. Check ECE |
| A large number of statements disappeared | The probe has stopped firing. Check AUROC and the consistency residual |
| Discrepancies suddenly decreased | **A sign of circular contamination between the record path and the estimation path** (design document section 15). Check audit isolation |

## 6. Confirm the beliefs of the time with as-of

The purpose of re-grounding is not to "rewrite the past." It is to confirm that **the beliefs of the time can be reconstructed**.

```python
# Transaction time before re-grounding -> beliefs of the old version
before = store.query(subject=entity, predicate=fluent, as_of=reground_at - timedelta(seconds=1))
# Transaction time after re-grounding -> beliefs of the new version
after = store.query(subject=entity, predicate=fluent, as_of=reground_at + timedelta(seconds=1))
```

Confirm that both return results, and that `valid_from` is the same while `tx_from` differs. If this does not hold,
re-grounding has failed (old statements may have been deleted).

## 7. Record

- Save the diff report's markdown as an execution record under `docs/runbooks/`
- Register the new combination in the compatibility matrix
- Append the elapsed time and the scale of the diff to `docs/PROGRESS.md` (this feeds the next estimate)

## On failure

| Symptom | Action |
|---|---|
| `RegroundingError: bounded interval` | Put an end in the interval |
| `RegroundingError: must state why it runs` | Write a `reason` (it becomes input for judging the report) |
| `RegroundingError: has not been verified` | Run the promotion pipeline first (impact analysis -> canary -> re-grounding -> diff review) |
| Stopped midway | Old statements have not been closed, so **it is fine to re-run** (not idempotent since it is append-only, but duplicate statements differ only in `tx_from` and do no harm). If duplicates concern you, check the diff report |
| `make seed` fails | Check Compose's ports (`docker compose ps`). Make sure you did not forget to move `SBP_PG_DSN` aside |
