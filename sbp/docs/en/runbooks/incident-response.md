# Incident Response

Scope: broker outage, graph store outage, worker backlog. Corresponds to design document section 12 and `services/runtime.py`.

**Basic policy: do not hide that something has stopped.** SBP is a platform that manages beliefs, and
the "running but not observing" state is the most dangerous (no discrepancies appear, so it looks normal).
If connectivity is lost, workers do not start, and existing workers go down on a stop request.

## Common first response

```sh
docker compose -f infra/compose.yaml ps        # State of the 4 services
docker compose -f infra/compose.yaml logs --tail=50 <service>
```

An empty Health column for `oxigraph` is **normal** (it is distroless, so a healthcheck cannot be written; ADR-0004).
The application checks connectivity at startup.

## 1. Broker (Redpanda) outage

### Symptoms

- Workers die at startup with `DependencyUnavailableError: ... redpanda unreachable`
- Running workers stop consuming, and `event_log_lag` keeps growing

### Action

```sh
docker compose -f infra/compose.yaml restart redpanda
docker compose -f infra/compose.yaml logs --tail=100 redpanda | grep -i error
```

### What to check after recovery

**Events have not been lost** (at-least-once). Workers resume from the last committed offset, and
already-processed events are rejected idempotently via `processed_events(event_id)` (invariant 4).

```sh
# Whether the backlog is clearing
uv run python -c "print('topics:'); " # Check with the list from scripts/topics.py
```

**There is no need to suspect duplicate beliefs from reprocessing.** Assertions are made idempotent by `event_id`, and
the same event never yields two of the same statement.

## 2. Graph store (Oxigraph) outage

### Symptoms

- `DependencyUnavailableError: ... oxigraph unreachable`
- The API's `/health` shows `store_reachable: false`
- GraphQL returns `storeReachable: false`

### Action

```sh
docker compose -f infra/compose.yaml restart oxigraph
# Startup includes RocksDB recovery, so it takes a few seconds
sleep 5
curl -s "http://localhost:7878/query?query=ASK%20%7B%7D"
```

### What to check after recovery

```sh
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp"   # Example for a port conflict
make seed          # Whether the ontology is loaded (whether the version matches)
uv run pytest tests/integration/test_g0_roundtrip.py -m integration
```

**Always check the ontology version.** If the store is still on an old version, `as_of` checks break
(`make ontology-check` detects a mismatch between `owl:versionInfo` and `VERSION`).

### When data corruption is suspected

If a leftover RocksDB lock prevents startup, **always** check the following before deleting the volume.

```sh
docker compose -f infra/compose.yaml logs oxigraph | tail -50
```

Deleting the volume (`make nuke`) **loses all beliefs**. The ontology comes back with `make seed`, but
assertions do not. Obtain approval from the responsible person before deleting.

## 3. Worker backlog

### Symptoms

- `event_log_lag` keeps growing (`sbp_operations_event_log_lag_seconds`)
- Discrepancy filing has stopped
- `worker.stopped` does not appear in the worker's log (that is, it is alive but not making progress)

### Triage

```sh
# Whether the worker is alive
ps aux | grep -i worker
# Where it has stopped (the last structlog event)
# Whether worker.coupling_cycle appears at its regular period
```

| Last log line seen | Interpretation | Action |
|---|---|---|
| Only `worker.dependency_ready` | Not a single event processed after connectivity was confirmed | Check the broker's topics and offsets |
| `worker.coupling_cycle` keeps appearing | Coupling is running but no events are arriving | Suspect an outage upstream (grounding or integration layer) |
| Nothing | The loop is stuck on an external call | Send SIGTERM (graceful shutdown; does not discard in-flight events) |

```sh
# Graceful shutdown (finishes the current loop, commits the offset, then exits)
kill -TERM <pid>
```

**Do not use `kill -9`.** In-flight events may be discarded while only the offset advances
(this breaks at-least-once, and an event is lost).

## 4. Write-back anomalies

### Symptoms

An unintended change was made in a business system, or an expected change was not made.

### Check

```sh
# Whether the writeback mode is still dry (the default is dry)
uv run python -c "from sbp.core.config import Settings; print(Settings().writeback_mode)"
```

**The default is `dry`.** If it was running in `live`, each command has a declared `compensation` (compensating action)
(an action type without a declaration cannot be registered). Compensation is executed as a separate `ActionRequest`
via `ActionRequest.compensate()`; compensation also has preconditions and approval.

## 5. What a report must include

- The symptom and the time it was first noticed (the `event_log_lag` graph is useful)
- The period of the outage (**during this period observations were not "absent" but "not made."**
  Beliefs were simply not updated; that does not mean there were no discrepancies)
- The number of events reprocessed after recovery
- Whether a retrospective investigation of discrepancies that did not surface during the outage is needed (if so,
  re-ground with `RETROSPECTIVE_INVESTIGATION` in `docs/runbooks/regrounding.md`)
