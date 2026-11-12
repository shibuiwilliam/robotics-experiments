# Backup and Restore

Scope: the knowledge graph (Oxigraph), projections and latent index (TimescaleDB), model weights (SeaweedFS),
and the event log (Redpanda).

**What can be lost differs by layer.**

| Layer | Impact of loss | Recovery source |
|---|---|---|
| Knowledge graph (beliefs) | **Cannot be recovered.** Assertions are append-only primary data | Backups only |
| Event log | Can be reprocessed within the retention period | The log itself (projections can be rebuilt) |
| Projections / latent index | Can be rebuilt (from the event log and latents) | Re-grounding / re-projection |
| Model weights | Can be retrained, but it takes time | Object store |

The **priority order is therefore knowledge graph > model weights > event log > projections**.

## Backup

### Knowledge graph

```sh
# Take N-Quads per named graph (graph boundaries are preserved)
curl -s "http://localhost:7878/store" -H "Accept: application/n-quads" > backup/graph-$(date +%Y%m%d).nq
wc -l backup/graph-*.nq    # If the line count dropped from last time, something is wrong (append-only, so it only grows)
```

**Stop if the line count has dropped.** Assertions are append-only (invariant 2), so the count never decreases.
If it has decreased, a deletion has occurred; investigate the cause before taking the backup.

### TimescaleDB (projections, latent index, metrics)

```sh
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp"   # Example for a port conflict
docker compose -f infra/compose.yaml exec -T postgres \
  pg_dump -U sbp -Fc sbp > backup/pg-$(date +%Y%m%d).dump
```

The latent index is large. If capacity is a concern, take the dump **excluding the latent index** (it can be rebuilt by re-grounding).

```sh
docker compose -f infra/compose.yaml exec -T postgres \
  pg_dump -U sbp -Fc --exclude-table-data='latent_index*' sbp > backup/pg-light-$(date +%Y%m%d).dump
```

However, do **not** exclude `latent_index_registry` (if the mapping of which version uses which table is lost,
the write destination can no longer be determined).

### Model weights

```sh
export SBP_S3_ENDPOINT="http://localhost:19000"
# Under s3://models/<model_id>/<version>/
uv run python -c "
import boto3
from sbp.core.config import Settings
s = Settings()
client = boto3.client('s3', endpoint_url=s.s3_endpoint,
                      aws_access_key_id=s.s3_access_key, aws_secret_access_key=s.s3_secret_key)
print([o['Key'] for o in client.list_objects_v2(Bucket='models').get('Contents', [])])
"
```

### Ontology

It lives in Git, so no separate backup is needed. **However, confirm that the version loaded into the store
matches the version in Git.** The version check in `make ontology-check` is **offline**: it only compares the TTL's `owl:versionInfo` with
`ontology/VERSION` (it does not look at the store). Check the store's version via the destination named graph
(`urn:sbp:g:schema:ontology:<version>`). Running `make seed` after a restore loads the version in Git.

## Restore

### Order

1. Start Compose (`make up`)
2. **Load the ontology** (`make seed`), before restoring beliefs. If the version does not match,
   the `ontology_version` check breaks
3. Restore the knowledge graph
4. Restore TimescaleDB
5. Restore the model weights
6. Verify

### Knowledge graph

```sh
curl -X POST "http://localhost:7878/store" \
  -H "Content-Type: application/n-quads" --data-binary @backup/graph-YYYYMMDD.nq
```

### TimescaleDB

```sh
docker compose -f infra/compose.yaml exec -T postgres \
  pg_restore -U sbp -d sbp --clean --if-exists < backup/pg-YYYYMMDD.dump
# If the dump excluded the latent index, rebuild it by re-grounding (docs/runbooks/regrounding.md)
```

### Verification

```sh
make check
export SBP_PG_DSN="postgresql://sbp:sbp@localhost:15432/sbp" SBP_S3_ENDPOINT="http://localhost:19000"
uv run pytest tests/integration -m integration     # All tests must pass (the G0-G7 and M8 integration tests)
```

**Always confirm that an as-of query returns past beliefs.** A restore that does not return them is a failure
(the bitemporal history has been lost).

```python
rows = store.query(subject=entity, predicate=fluent, as_of=some_past_time)
assert rows, "past beliefs could not be restored"
```

## Things to check regularly

- Whether you can **actually restore** from a backup (having taken it is not enough)
- Whether the knowledge graph's line count is monotonically increasing (a decrease is a sign of deletion)
- Whether `latent_index_registry` is included in the backup
