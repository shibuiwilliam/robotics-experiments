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
| P2 Core | ✅ done | claimstore (SQLite, append-only, bitemporal `as_of`, decay), bus (JSON-LD envelopes + deterministic toxic mode), registry (entity/authority + capability subsumption+QoS), mediator (authority×decay×method-rank, append-only supersede), norms+gate (SHACL/prohibition/reversibility/resource — unapproved-irreversible=0), explain (accountability chain, trace completeness, IRI-resolvability). ids/clock/rng. 23 core tests. |
| P3 Sim | ✅ done | `micro_warehouse.xml` (zones, 5 pallets + hidden unknown, lift-bot, 3 cameras); `World` (deterministic step, sim-clock, idealized contact-free carry, god-view ground truth); skills (MoveTo/Pick/Place/ScanTag + metadata + seeded fault injection); Invisible Hand (move/swap/remove/degrade_tag/spawn_unknown/churn, seeded, records planted truth); offscreen renderer (RGB/depth/segmentation + geom→entity + truth scoring). 9 sim tests incl. bit-identical determinism + segmentation recall=1.0. |
| P4 Perception | ✅ done | Perception dial (oracle offline / hybrid / live); OraclePerception (calibrated seeded noise: position σ, dropout, false-pos, tag-read error → position Claims, identified vs anonymous tracked-object); pixel→world unprojection (≤6 cm vs ground truth); ERPerception (ER points → world Claims via depth, `ERClient` Protocol behind clients/). 8 perception tests. |
| P5 Clients/VCR | ✅ done | VCR (replay/record/passthrough, MissingCassette, budget guard, JSON cassettes keyed by hash(model,request)); ERAdapter/ChatAdapter/EmbeddingAdapter (VCR-wrapped); `GeminiBackend` (lazy google import — never loaded offline); `FakeGeminiClient` (schema-valid deterministic double: ER points, minimal-instance generate, seeded unit embeddings); embedding SQLite cache (no double-billing). ER interface moved to clients (PER→CLI). 9 clients tests. |
| P6 Agents + E0 | ✅ done | Planner interface + ScriptedPlanner (deterministic, standoff approach) + GeminiPlanner (ChatAdapter, offline via FakeGeminiClient canned plan); Musubi tools (entity_resolve/claim_query/norm_check/plan_validate); capability→tool compiler (zero agent code). Bench Episode orchestrator + A0–A4 arms. **E0 smoke passes end-to-end at A4 fully offline** (success, unapproved-irreversible=0, trace=1.0, api_calls=0); A0–A4 ladder runs. 9 tests. |
| P7 Bench+Scoreboard | ✅ done | Scenario DSL (YAML loader, `e0_smoke.yaml`), run driver (arms×seeds + oracle), CLI (`python -m bench.runner scenario|experiment`), PREREG.md. Oracles (relocate_reached/no_unapproved_irreversible/trace_complete/zero_api_calls). DuckDB MetricsStore + arm summaries. Report generator (`E0_report.md`) + HTML observability dashboard (Case trace). `make experiment E=E0` → 15 runs, oracle 15/15, unappr-irrev 0, API 0. 5 tests. |
| P8–P11 | ⬜ pending | external mocks, flagships F1/S5/F2, breadth S1–S9/C1–C4, polish |

## Deferred / provisional (running list)

- `[PROVISIONAL]` All scenario oracles, experiment statistical design, and the exact ontology
  concept set depend on the missing canonical docs. Choices recorded in `DECISIONS.md`.
- Live cassettes: none recorded yet (no keys during build). Offline paths use `FakeGeminiClient`.

## Test / verification log

- (pending) first `make check` run.
