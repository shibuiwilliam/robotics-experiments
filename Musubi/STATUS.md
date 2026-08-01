# STATUS.md — Musubi build status

Honest, running account of what is implemented + tested, what is deferred + why, and how to
run everything. Updated continuously. Newest state at top.

> **Canonical-docs caveat.** Of the three canonical design docs, the **Scenario Catalog**
> (`SCENARIOS.md`, 結の十六景) IS present and authoritative — it supplies the 16 scenarios with
> machine-scorable oracles, the rig-delta tree, and (§4.1) the 14 core ontology mechanisms.
> Still **absent**: the **Ontology Design** (設計書) and **Experiment Plan** (実験計画). So the
> exact LinkML concept axioms/SHACL and the experiment statistics remain **best-effort
> `[PROVISIONAL]`**, grounded on `PROJECT.md §8` + `SCENARIOS.md §4.1`. See `DECISIONS.md`.

## How to run (offline, no keys)

```bash
make setup     # uv venv + install .[dev] + pre-commit
make gen       # regenerate ontology artifacts (P1+)
make check     # lint + types + test (VCR replay) — the PR gate / DoD
```

Live cassette recording (later, needs GOOGLE_API_KEY + budget): `make test-live` (record mode).

## Phase status

| Phase | State | Notes |
|---|---|---|
| P0 Scaffold | ✅ done | tree, uv/ruff/mypy/pytest/pre-commit, Makefile (all §3 targets), registry.yaml, CI, determinism substrate (SimClock, RngRegistry), invariant tests. `make check`/`make gen`/`make docs-check` green offline. |
| P1 Ontology | ✅ done | 30 core concepts (LinkML, `ontology/src/musubi.yaml`) → generated JSON Schema, Pydantic types, SHACL (canonicalized, deterministic), JSON-LD @context, NL vocab. Hand-authored `world_ok.ttl` (privacy floor + claim sanity). `ontology/artifacts.py` access/validation layer. 7 ontology tests (schema conformance + SHACL privacy-floor rejection). `make gen` deterministic across runs. |
| P2 Core | ⬜ pending | claimstore, bus, registry, mediator, norms+gates, explain |
| P3 Sim | ⬜ pending | micro_warehouse.xml, skills+faults, invisible hand, renderer |
| P4 Perception | ⬜ pending | oracle/hybrid/live dial, ER point→world |
| P5 Clients/VCR | ⬜ pending | VCR record/replay/passthrough, ER/ADK/embedding adapters, FakeGeminiClient |
| P6 Agents + E0 | ⬜ pending | ScriptedPlanner + GeminiPlanner, Musubi tools, capability compiler, E0 smoke @A4 |
| P7 Bench+Scoreboard | ⬜ pending | scenario DSL, runner, scoreboard, dashboard, report |
| P8–P11 | ⬜ pending | external mocks, flagships F1/S5/F2, breadth S1–S9/C1–C4, polish |

## Deferred / provisional (running list)

- `[PROVISIONAL]` All scenario oracles, experiment statistical design, and the exact ontology
  concept set depend on the missing canonical docs. Choices recorded in `DECISIONS.md`.
- Live cassettes: none recorded yet (no keys during build). Offline paths use `FakeGeminiClient`.

## Test / verification log

- (pending) first `make check` run.
