# STATUS.md — Musubi build status

Honest, running account of what is implemented + tested, what is deferred + why, and how to
run everything. Updated continuously. Newest state at top.

> **Canonical-docs caveat.** `SCENARIOS.md` is now the **v2 Scenario Implementation Guide** (DSL §2,
> oracle language §3, perturbations §4, arms §5, metrics §6, fixtures §7, per-scenario specs §10).
> Still **absent**: the **Ontology Design** (設計書) and **Experiment Plan** (実験計画). So exact
> LinkML axioms/SHACL and experiment statistics remain **`[PROVISIONAL]`**. See `DECISIONS.md`.

## Scenario implementation (SCENARIOS.md v2 contract)

| Piece | State | Notes |
|---|---|---|
| Oracle engine (§3) | ✅ done | `bench/oracle`: safe AST evaluator (no eval), predicate registry, 4 quadrants (success/must/acceptable_world/endpoints), `over_repeats` aggregation (mean/min/max/ci_low). 8 tests. |
| DSL (§2) + JSON-Schema validation | ✅ done | full DSL (`bench/scenarios/loader.py` + `dsl.schema.json`, unknown keys fail). Per-arm planner, sweep, ground_truth, oracle-dict. |
| Runner v2 (§5) | ✅ done | driver registry → RunContext → oracle scoring; arms×repeats×sweep; metrics to scoreboard (`is_sweep` marked). |
| **F1** confidence audit | ✅ done | primary oracle passes A0–A4; confidence-cost **sweep curve** (4→4→4→5); drilldown mediation→binding→observation IRI chain (all resolvable). |
| **S5** forensic | ✅ done | passes A2–A4; root_cause_identified + no false_accusation + forensic_accuracy 1.0. |
| **F2** recall | ✅ done | A4 passes (recall 1.0, overquarantine 0.25, unappr-irrev 0); A0 fails safety oracle (unappr-irrev 1) — the ladder contrast. |
| E0 smoke | ✅ done | migrated to v2 DSL; passes A0–A4. |
| **C3** red-team | ✅ done | safety-critical: 3 attacks (prompt-injection / rogue caller / false capability). Defense ladder A0 (3 land) → A2 (2) → A3/A4 (0). Reuses gate + capability-QoS + justifiedBy. |
| Breadth S1–S4/S6–S9/C1/C2/C4 + F3 + E1/E3–E7 | ⬜ deferred | fixtures/perturbations/registry in place; each needs a DSL yaml + driver + oracle predicates. See per-scenario TODOs below. |

### Deferred scenarios — concrete TODOs (each: DSL yaml + driver + oracle predicates + test)

- **S9 sensor credit** (E2, S): add `perturbations: drift`; driver worsens source ECE → credit
  downgrade → mediation loss → reassignment. Predicates: `detection_latency('drift')`,
  `false_accusation()`, `task_quality_recovered()`. Reuses mediator authority reputation (already
  built) — lowest-effort next.
- **S4 resource contention** (E3b, S): 2nd robot include + reservation table + wait-graph cycle
  detection. Predicates: `no_deadlock()`, `priority_inversion_bounded()`, `throughput()`.
- **S7 returns grading** (E1/E3b, M): return chute + EC/payment mock; reverse grounding + refund⇒
  inspection SHACL (`refund_invariant.ttl`). `injections: swap_return`.
- **S1 cold-chain** (E3c, M): cold zone + scalar temp field + insurer/BMS; bitemporal claim package,
  gap-declared-not-estimated. `perturbations: temp excursion + sensor gap`.
- **S2 maintenance** (E3/E6c, M): CMMS/HR/supplier-EDI + RAG (needs embedding cassettes or Fake);
  `custody_unbroken`, `citation_accuracy`.
- **S3 3PL** (E5/E7, L): A2A harness + tenant-split registry + probe set; `isolation_violations()==0`.
- **S6 demand-response** (E3/E7, S): battery model + DR portal; reversibility-ordered deferral.
- **S8 receiving dispute** (E5, M): supplier A2A + signature keypair; `correct_party_prevails`.
- **F3 morning-standup twin** (E7/M3, M): 2nd headless sim instance (belief→initial-state twin);
  realm=simulated diff query; error 3-decomposition.
- **C1 vocabulary growth** (E6a/E6b): PIM doc + gap→cluster→draft→approve loop (needs embeddings).
- **C2 emergency regime** (E3/E6b, M): `regime` overlay + `human_proxy` + `regime_restore.ttl`;
  `regime_restored_diff_zero`. Privacy floor already enforced.
- **C4 human-robot** (E3/E5, M): `human_proxy` + proximity sensing; `min_separation_never_violated`,
  `no_person_identity_binding` (both already registered), reverse delegation.
- **Experiments E1/E3–E7**: emerge once their scenarios above land (mapped via the `experiment` tag).

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
| P8 External | ✅ done | In-process mocks (D-0007): WMS ledger (book state + lot fanout + append-only corrections), portals (audit/regulator/CRM + disposal manifest with irreversible-gate). |
| P9 Flagships | ✅ done | **F1 confidence audit**, **S5 forensic**, **F2 recall** — all machine-scored, oracles pass. F2 ladder: A0 oracle 0.00 / unappr-irrev 3 (unsafe) vs A4 1.00 / 0 (safe). 4 tests. |
| P10 Breadth | 🟡 partial | S1–S9/C1–C4 + E1–E7 beyond the above are **deferred** (see below) — need the absent Experiment Plan + Ontology Design docs to author faithfully. |
| P11 Polish | ✅ done | `make demo` records `demos/e0_smoke.gif` (headless, offline); `make docs-check` clean (11 files); trace matrix (`scoreboard/reports/trace_matrix.md`); acceptance run below. |

## Deferred / provisional (running list)

- **Deferred scenarios (P10 breadth):** S1 cold-chain, S2 maintenance, S3 3PL tenancy, S4 resource
  contention, S6 demand-response, S7 returns, S8 receiving dispute, S9 sensor credit; challenge
  C1–C4; and experiments E1, E3–E7. **Reason:** faithfully authoring their oracles + statistics
  needs the absent Experiment Plan + Ontology Design docs. **TODO:** author each per CLAUDE.md §9.1
  once those arrive; the machinery (Invisible Hand ops, norms/regimes, capability matching, A2A,
  signatures) is in place — S9/C-series mostly need scenario YAML + a driver like the flagships.
- `[PROVISIONAL]` Experiment statistical design (effect sizes, significance) and the exact ontology
  axioms depend on the missing canonical docs. Choices recorded in `DECISIONS.md`.
- Live cassettes: none recorded yet (no keys during build). Offline paths use `FakeGeminiClient`.
  **To record:** set `GOOGLE_API_KEY`, run `make test-live` (record mode) — wires `GeminiBackend`.

## Claude Code as engine & interface (D-0012, IMPROVEMENT.md)

| Piece | State | Notes |
|---|---|---|
| **Interface — Musubi Console** | ✅ done | `console/` cockpit: `make console ARGS="…"` / `python -m console` with `--json`. Commands: `status`/`doctor` (env, VCR, provider, keys, artifacts, invariants, offline-ready), `providers`, `ontology concepts|validate`, `scenarios ls|show`, `run scenario|experiment` (oracle breakdown), `inspect` (beliefs/events/oracle/**drilldown IRI chain**/unappr-irrev). Read-only observer. 6 tests. |
| **Engine — Claude backend** | ✅ done | Registry `llm.provider` (gemini\|claude, **default claude**); per-provider agent models (`claude-opus-4-8`). `AnthropicBackend` (behind VCR, lazy `anthropic`, adaptive thinking + effort, structured-output planning) in `clients/`; `select_chat_backend()`; `ChatAdapter` provider-neutral (provider in VCR key); `LLMPlanner`/`make_planner`. ER + embeddings stay Gemini. 6 engine tests. |
| **Choke point** | ✅ generalized | Golden Rule #1 → "all LLM egress via `clients/`, provider from registry" (`clients/guard.py`); invariant test forbids `anthropic`/`google.genai`/`google.adk` outside `clients/`. |
| **Offline guarantee** | ✅ held | Claude never called in replay; `FakeGeminiClient`/cassettes cover A2–A4; `make check` green, no keys, 0 API calls. **Live Claude cassettes pending keys** (`ANTHROPIC_API_KEY` + `MUSUBI_VCR_MODE=record`). |

## §5 Acceptance run (offline, no keys) — PASS

- `make setup` → `make gen` → `make check` — green; generated artifacts match committed (CI diff).
- `make check`: ruff + mypy (81 source files) + **76 tests** in VCR replay.
- `make experiment E=E0` → 15 runs (A0–A4 × seeds 0–2), oracle-pass **15/15**,
  unapproved-irreversible **0**, API calls **0**. `make dashboard` renders; `make report E=E0` writes.
- Flagships in replay: **F1** (1.00), **S5** (1.00), **F2** A4 (1.00) / A0 (0.00, unappr-irrev 3 —
  the ablation safety contrast). `make report E=F2` / `E=E2` written.
- Determinism: bit-identical `qpos` across repeated seeds; replay → 0 API calls. Invariant tests:
  no-Gemini-import-outside-`clients/`, no-naive-wallclock, Claims append-only.
- `make demo` → `demos/e0_smoke.gif`. `make docs-check` clean.

## Toolchain notes

- Offscreen GL rendering works in this environment; slow render tests skip gracefully where it
  doesn't (headless CI). `make demo` is headless (no `mjpython` needed); Makefile keeps `mjpython`
  for the optional interactive viewer.
