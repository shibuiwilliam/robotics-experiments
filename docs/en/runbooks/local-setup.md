# runbook: Bringing Up the Local Environment

Scope: running SBP's Compose stack on a development machine (macOS / Apple Silicon).

## Steps

```sh
make bootstrap   # uv sync -> generate .env -> pull images
make up          # start the 4 services
make ps          # wait until healthy (30-60 seconds the first time)
make seed        # topics, migrations, buckets
make test-int    # integration tests
```

## Service layout (design document section 12, ADR-0003)

| Service | Image (pinned version) | Default port | Role |
|---|---|---|---|
| redpanda | `redpandadata/redpanda:v24.2.7` | 9092 / 9644 | Event log (Kafka API) |
| postgres | `timescale/timescaledb-ha:pg16.15-ts2.30.2` | 5432 | Time series, latent index, projections |
| oxigraph | `oxigraph/oxigraph:0.5.11` | 7878 | Knowledge graph (SPARQL 1.1 + RDF 1.2 triple terms; ADR-0005) |
| objectstore | `chrislusf/seaweedfs:3.80` | 9000 | Raw layer, model weights (S3-compatible) |

The memory limits total 6 GB (2+2+1+1). Match the Docker Desktop / OrbStack limit to this.

**An empty Health column for oxigraph is normal** (ADR-0004). It is a distroless image and cannot be probed
from inside the container, so no healthcheck is attached. Connectivity is checked via
SPARQL by the integration-test fixture and at worker startup. Do not mistake the empty column for a fault.

## Common failures

### Port already in use

```
Error response from daemon: ... Bind for 0.0.0.0:9000 failed: port is already allocated
```

This happens when another project on the same development machine uses 5432 (Postgres) or 9000 (S3-compatible store).
Move them aside in `.env` (uncomment the "Ports" section in `.env.example` and change the values).

```sh
SBP_PORT_POSTGRES=15432
SBP_PORT_S3=19000
```

After moving them, update the connection strings in the same `.env` to match.

```sh
SBP_PG_DSN=postgresql://sbp:sbp@localhost:15432/sbp
SBP_S3_ENDPOINT=http://localhost:19000
```

You can find which process holds a port with `lsof -nP -iTCP:9000 -sTCP:LISTEN`.

`make up` passes the top-level `.env` to compose via `--env-file`. **Before 2026-10-25 (X-14) it did not,
so port remapping in `.env` had no effect on compose** (compose only reads the `.env` in the compose file's
directory, `infra/`). On a development machine that follows the old procedure and writes the remapping in `.envrc`, you may move it to `.env`.
When calling `docker compose` directly without going through `make`, add `--env-file .env`.

### Images cannot be pulled

If you see `pull access denied` / `unauthorized`, the image may no longer be anonymously pullable
(this actually happened with MinIO; see ADR-0003). Confirm via the registry API and decide on a replacement in an ADR.

### The healthcheck never becomes healthy

Look at the service's log with `make logs`. redpanda takes about 20 seconds to start, and postgres takes about 30 seconds for its first
initialization. If it is still unhealthy after `start_period`, it is often because Docker's allocated memory is
insufficient for the `mem_limit`.

## Cleanup

```sh
make down    # Stop containers (data is kept)
make nuke    # Also delete volumes (data is lost; the only deletion path)
```
