# ADR-0004: Check Oxigraph connectivity in the application, not with a container healthcheck

- Date: 2026-10-03
- Status: Accepted
- Layers touched: infra, knowledge (where the connectivity check is implemented), tests

## Context

`.claude/rules/infra-testing.md` states: "Attach `platform: ${SBP_PLATFORM:-linux/arm64}`, `healthcheck`, and `mem_limit` to every service." While implementing M0-08, it turned out that **a healthcheck cannot be run inside the container of the official Oxigraph image**.

`oxigraph/oxigraph:0.5.11` is distroless, and the facts confirmed are as follows.

| What was tried | Result |
|---|---|
| `docker exec sbp-oxigraph sh -c ...` | `exec: "sh": executable file not found in $PATH` |
| `docker run --entrypoint /bin/ls` | `stat /bin/ls: no such file or directory` |
| Image composition | The only Entrypoint is `/usr/local/bin/oxigraph`. No shell, wget, curl, or timeout |
| `oxigraph query --help` | `--location <LOCATION>` is required. There is no way to specify an HTTP endpoint |

Therefore the `CMD-SHELL` form cannot be used, and the only binary usable in the `CMD` form (`oxigraph`) cannot check HTTP connectivity either. `oxigraph query --location /data` opens the same store as the running server, so it conflicts on the RocksDB lock (the check would break the main process).

The HTTP endpoint itself is healthy: from the host, `GET /query?query=SELECT (1 AS ?x) WHERE {}` returns HTTP 200. The only problem is that "there is no means of running the check inside the container".

## Options

1. **Attach no healthcheck, and put the connectivity check on the application side (the integration-test fixture and worker startup).**
   - Benefits: the check confirms that "a SPARQL query actually goes through". This is identical to the check that is really needed, and it also overlaps with what `.claude/rules/infra-testing.md` separately requires: "integration tests verify Compose connectivity, and if it is not running, fail with an explicit error rather than skip" and "workers check Settings and Compose connectivity at startup".
   - Drawbacks: the Health column of `docker compose ps` is blank. `depends_on: condition: service_healthy` cannot be used.
2. Attach a token healthcheck (`["CMD", "/usr/local/bin/oxigraph", "--version"]`).
   - Benefits: satisfies the letter of the rule. The Health column is filled in.
   - Drawbacks: **it only confirms that the binary can be executed.** It reports healthy even if the server is down. "The check passed but it does not work" is worse than having no healthcheck (it gives false assurance). Rejected.
3. Replace it with an unofficial image that includes a shell, or a self-built image.
   - Drawbacks: moves away from the official distribution and increases the cost of supplier trustworthiness and keeping up with updates. A large price to pay for a single healthcheck. Rejected.
4. Add a dedicated healthcheck sidecar.
   - Drawbacks: amounts to adding a service (the direction CLAUDE.md forbids). It also breaks the 4-service constraint. Rejected.

## Decision

**We adopt Option 1.** Only Oxigraph is left without a `healthcheck`, and the reason is stated in a comment in `infra/compose.yaml`. The connectivity check is placed in the following two locations.

1. The fixture in `tests/integration/conftest.py` — confirm connectivity with the equivalent of a SPARQL `ASK {}`, and if the service is not running, fail explicitly rather than skip.
2. The startup check of workers and the API — issue one query when the `sbp.knowledge` client is initialized.

The fact that `service_healthy` cannot be used with `depends_on` is absorbed by the application-side retry above (retry at short intervals until a connection is established, and raise `DependencyUnavailableError` once the limit is exceeded).

The remaining three services (redpanda / postgres / objectstore) have healthchecks, so the intent of the rule (after `make up`, it is mechanically clear whether the stack is "in a usable state") is satisfied for three of the four.

## Consequences

- Impact on invariants: none.
- Ontology version: no impact.
- Difference from `.claude/rules/infra-testing.md`: creates one known exception to the statement "healthcheck on every service". This exception and a reference to this ADR will be added to the rule file.
- Impact on testing: the Oxigraph connectivity check must be placed in `tests/integration/conftest.py` (this becomes the de facto healthcheck, so omitting it would remove the check entirely). It is included in the acceptance criteria of M0-13.
- Impact on operations: `docs/runbooks/local-setup.md` will state that "a blank Health column for oxigraph is normal". This prevents an operator who sees the blank from mistaking it for a fault.

## Open questions

If a future Oxigraph image includes a shell or a subcommand for HTTP connectivity, we will return from Option 1 to a normal healthcheck. Re-evaluation will be done at M7 (operations).
