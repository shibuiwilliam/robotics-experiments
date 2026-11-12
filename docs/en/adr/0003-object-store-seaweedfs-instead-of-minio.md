# ADR-0003: Replace MinIO with SeaweedFS as the object store

- Date: 2026-10-03
- Status: Accepted
- Layers touched: infra (plus, in the future, raw-layer access in knowledge and model-weight storage in worldmodel)

## Context

The infrastructure table in `CLAUDE.md` and design document §6.2 specify MinIO (S3-compatible) for storing the raw layer (raw sensor values) and model weights. While implementing M0-08 (`infra/compose.yaml`), **it turned out that the public MinIO container image cannot be pulled**.

```
$ docker compose up -d
Error response from daemon: pull access denied for minio/minio,
repository does not exist or may require 'docker login'
```

Findings (as of 2026-10-03). The initial investigation was insufficient because it only looked at nonexistent tags and `latest`. The results confirmed by querying the registry APIs directly are as follows.

| Pull route | How checked | Result |
|---|---|---|
| `minio/minio` (Docker Hub) | Query registry v2 `tags/list` with an anonymous token | `UNAUTHORIZED: authentication required` |
| `minio/minio` (Docker Hub) | Hub API `repositories/minio/minio/tags` | `object not found` (does not exist for anonymous users) |
| `quay.io/minio/minio` | Quay API `repository/minio/minio` | `Requires authentication` |
| `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` | Anonymous access to registry v2 `manifests/<tag>` | **HTTP 401** |
| `quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z` | `docker pull` (anonymous) | `unauthorized: access to the requested resource is not authorized` |

Since both registries return 401 even when the tag is given explicitly, we conclude that **MinIO has lost its anonymous pull route**. Note that this development machine still holds a MinIO image pulled locally by another project in the past, but this is no basis for reproducibility (it cannot be pulled in a fresh environment).

A developer who runs `make bootstrap` cannot reproduce the environment without registry authentication, so the condition of gate G0, "reproducible in a fresh environment within 30 minutes from `make bootstrap`", cannot be met.

CLAUDE.md forbids "adding services just because it is convenient", but this is a **replacement of an existing service, not an addition**, and the contract of an S3-compatible API does not change. Because store selection requires an ADR (CLAUDE.md, Git/PR/ADR section), this ADR is filed.

## Options

All were required to support arm64 and run within `mem_limit: 1g`.

1. **SeaweedFS (`chrislusf/seaweedfs`)** — a distributed file system with a built-in S3 gateway.
   - Benefits: can be pulled anonymously (has an arm64 manifest). A single container exposes the S3 API via `weed server -s3`. Supports persistent volumes. Long track record.
   - Drawbacks: it is fundamentally a distributed FS, so its functionality is excessive for single-node use. Its S3 API coverage is narrower than MinIO's (although SBP uses only PutObject / GetObject / ListObjects / bucket creation).
   - Cost on a MacBook: a few hundred MB of memory, a few seconds to start.
2. **Garage (`dxflrs/garage`)** — a lightweight S3-compatible store for self-hosting.
   - Benefits: can be pulled anonymously. Minimal memory consumption. Dedicated to S3 compatibility.
   - Drawbacks: startup requires `garage.toml` and layout initialization via `garage layout assign`, which adds steps to `make seed`. The cluster concept is exposed even on a single node.
3. **adobe/s3mock** — an S3 mock for testing.
   - Benefits: lightest, no configuration required.
   - Drawbacks: **in-memory, does not persist.** The raw layer must be "retained for 30–90 days for re-latentization and audit" (design document §6.2), and re-grounding (M7-01) refers across versions of model weights. A store that does not persist cannot meet this requirement.
4. Keep using MinIO on the premise of Docker Hub authentication — each developer would need `docker login` and an account on the MinIO side, reducing the reproducibility of `make bootstrap`. CI (X-01) would also need more secrets configured. Rejected.

## Decision

**We adopt Option 1 (SeaweedFS).** The image is pinned to `chrislusf/seaweedfs:3.80`.

```yaml
objectstore:
  image: chrislusf/seaweedfs:3.80
  command: ["server", "-dir=/data", "-s3", "-s3.port=9000", "-master.volumeSizeLimitMB=256"]
```

Reasons:

- Options 1 and 2 are the ones that satisfy both persistence (the deciding factor against Option 3) and anonymous pull (the deciding factor against Option 4).
- The difference between 1 and 2 is the complexity of initialization. Garage's layout initialization would add irreversible steps to `make seed` and also add rebuild steps after `make nuke`. SeaweedFS exposes the S3 API with a single command, leaving only bucket creation as the responsibility of `make seed`. This is the same shape as with MinIO, so the **migration cost is minimal**.
- Because the abstraction of an S3-compatible API is preserved, no change is needed on the SBP code side (access via a `boto3`-compatible client). If the MinIO pull route is restored in the future, we can revert by simply swapping the image and command in `compose.yaml`.

The service name in compose is `objectstore` (so as not to pin the implementation by name; keeping the name `minio` would become a lie if the implementation changed).

## Consequences

- Impact on invariants: none. Invariant 9 (do not persist raw video or audio) is unchanged, and only synthetic data and latents go into the raw layer (during development).
- Ontology version: no impact. No re-grounding needed.
- Impact on configuration: `SBP_S3_*` in `.env.example` can be used as-is (because the API is S3-compatible). `SBP_PORT_MINIO` is renamed to `SBP_PORT_S3`.
- Difference from the infrastructure table in `CLAUDE.md`: the "minio" row in the table must be updated to "objectstore (SeaweedFS)". CLAUDE.md is the canonical source of conventions, so it is updated at the same time this ADR is accepted.
- Impact on testing and operations: the connectivity check of the object store in integration tests is done via the S3 API and does not depend on the implementation. Bucket creation in `make seed` is done via the S3 API (`CreateBucket`).
- The number of services stays at 4 (this is a replacement, not an addition). The memory limit also stays at 1 GB.

## Open questions

Whether to revert if the anonymous pull route for MinIO is restored. The only benefit of reverting is agreement with the original text of CLAUDE.md, and as long as the S3-compatible abstraction is preserved there is no practical gain. **Re-evaluation will be done at M7 (operations)**, and until then SeaweedFS is the default.
