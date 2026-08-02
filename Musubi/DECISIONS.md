# DECISIONS.md — Musubi build decision log

Records design/implementation choices made where `PROJECT.md` / `CLAUDE.md` left a detail
unspecified (per build-prompt §0.3). Each entry: context → decision → rationale. Newest first.

> **Standing caveat.** The three canonical design docs (Ontology Design, Experiment Plan,
> Scenario Catalog) are **absent** from the repo. Per the user's directive ("Build core, draft
> specs as I go"), the working platform core is grounded on `PROJECT.md` + `CLAUDE.md`, and any
> scenario/experiment/ontology *canon* inferred here is **provisional** — to be reconciled when
> the authoritative docs arrive. Items depending on the missing canon are flagged `[PROVISIONAL]`
> in `STATUS.md`.

---

## D-0001 — Package layout: flat top-level packages, hatchling build
**Context.** PROJECT.md §13 lists top-level module dirs (`ontology/ core/ sim/ …`) rather than a
`src/musubi/` layout.
**Decision.** Keep the flat layout. Each module dir is an importable package (`core.claimstore`,
`sim.render`, …). Build backend = hatchling; wheel packages enumerated explicitly. Installed
editable via `uv pip install -e .` so imports resolve without sys.path hacks.
**Rationale.** Matches the canonical repo structure exactly; avoids an extra namespace level that
would diverge from PROJECT.md and the dependency diagram (§6.2).

## D-0002 — Cloud SDKs are an optional extra; offline core never imports them
**Context.** `google-genai` / `google-adk` are only reachable inside `clients/` (Golden Rule #1),
and the whole system must build+test offline with no keys (Prime Directive).
**Decision.** `google-genai`, `google-adk` live in an optional `[live]` extra. `clients/` imports
them **lazily**, only when VCR mode is `record`/`passthrough`. Offline paths use `FakeGeminiClient`
and committed cassettes and never trigger the import. `mujoco`, `linkml`, `pyshacl` are core deps
(needed offline for sim/gen/validation).
**Rationale.** Guarantees `make setup && make check` works with a lighter, network-free install;
keeps the single cloud choke point real without making it a build prerequisite.

## D-0003 — Cassettes as JSON files (committed), runtime stores as SQLite (ignored)
**Context.** CLAUDE.md says commit cassettes and also lists SQLite for cassettes/claims/embeddings;
committing a binary SQLite blob is git-hostile.
**Decision.** VCR cassettes are JSON files under `clients/vcr/cassettes/` (committed, diff-able,
one file per `hash(modelId, normalized-request)`). Claim stores and embedding cache use SQLite
under `data/` (git-ignored, per-run/shared runtime artifacts).
**Rationale.** Reproducibility needs cassettes in version control; JSON is reviewable and merges.
Claim/embedding stores are run artifacts, correctly ignored (matches existing `.gitignore`).

## D-0004 — Determinism substrate: SimClock + seeded RNG registry
**Context.** Golden Rule #5 bans bare `time.time()` / `datetime.now()` / unseeded `random` on
production paths (NFR-DETERM).
**Decision.** A `core.clock.SimClock` supplies all time on system paths (validTime/transactionTime),
advanced by the scenario/sim. A `core.rng.RngRegistry` derives named, seed-forked
`numpy.random.Generator`s (`seed → SHA256(seed, name) → Generator`). A lint test forbids the banned
calls outside tests/tooling.
**Rationale.** Makes "same seed → bit-identical qpos, replay → zero API calls" enforceable and the
non-determinism localized to the LLM behind the VCR.

## D-0005 — Semantic envelope = JSON-LD dict with generated @context; Claim is a frozen pydantic model
**Context.** Cross-module messages carry JSON-LD envelopes; `@context` comes from ontology
generated artifacts (PROJECT.md §6.3).
**Decision.** Envelopes are plain dicts `{"@context": <generated>, "@type", "payload", …}`. Claims
are immutable (`pydantic` frozen) with `source/method/confidence/validTime/transactionTime/realm`;
supersede creates a new Claim referencing the prior IRI. Append-only enforced at the store layer
and by a test.
**Rationale.** Keeps the bus honest (Rule #3, Claims append-only) and the boundary self-describing
without hand-writing schema (Rule #4).

## D-0012 — Multi-provider LLM choke point + Claude as the primary agent engine; Musubi Console
**Context.** The user directed that **Claude Code be the primary engine and interface** for
experimenting with Robotics × AI-agent × Ontology. This overrides the letter of Golden Rule #1
("Gemini only") and the "network egress = Gemini only" rule (NFR-LOCAL). See `IMPROVEMENT.md`.
**Decision (authorized deviation, flagged per build-prompt §0.2).**
- **Engine.** The agent-reasoning LLM is registry-selected (`llm.provider`: `gemini` | `claude`,
  default **claude**). A real `AnthropicBackend` (`clients/backends.py`, lazy `anthropic`,
  `claude-opus-4-8`, adaptive thinking + effort, structured-output planning) sits **behind the VCR**,
  inside `clients/`. ER (pointing) and embeddings stay Gemini (Anthropic offers neither). Rule #1 is
  **generalized** to "all LLM egress via `clients/`, provider from registry" — one choke point, now
  multi-provider. The invariant test forbids `anthropic` / `google.genai` / `google.adk` imports
  outside `clients/` (`clients/guard.py`). Offline stays deterministic: Claude is never called in
  replay; `FakeGeminiClient`/cassettes cover A2–A4; replay → 0 API calls; `make check` green with no
  keys. Live Claude needs `ANTHROPIC_API_KEY` + `MUSUBI_VCR_MODE=record` (documented, pending keys).
- **Interface.** A read-only `console/` cockpit (`python -m console` / `make console`) with `--json`:
  `status`/`doctor`, `providers`, `ontology`, `scenarios`, `run`, `inspect` — the surface Claude Code
  drives.
**Rationale.** Preserves the invariants' *intent* (single choke point, offline determinism,
reproducibility, model-IDs-in-registry) while honoring the explicit user directive; additive and
non-breaking (default offline path unchanged).

**Follow-up (round 2) — wire the engine per-arm.** The first round made Claude the *registry
default* but `drive_relocate` still ran `ScriptedPlanner()` for every arm, so no scenario run
actually invoked the LLM — Claude was the engine in name only. Fixed: `_select_planner` honors the
SCENARIOS.md §5 arm contract (`scenario.planner_for(arm)`) — A0/A1 scripted, **A2–A4 the LLM
(Claude) planner**. To keep the ablation isolating the *reasoning backend* (not geometry), the two
planners now share `agents/grounding.py::ground_relocate`: the LLM authors only the action-type
*skeleton*, params are grounded deterministically. Offline the LLM planner runs on
`FakeGeminiClient` behind a **passthrough** VCR — the sanctioned offline-double pattern (same as
every ER/planner offline test); the fake is not a network call, so `api_calls` stays 0 and
`make check` is green with no keys. Driver + console `inspect` surface `planner`/`provider`/
`llm_calls` for observability.

## D-0009 — Oracle expression language via a restricted AST interpreter (no eval)
**Context.** SCENARIOS.md §3 requires a safe expression oracle over a fixed predicate registry
(determinism + safety; no arbitrary eval).
**Decision.** `bench/oracle` implements a restricted `ast`-based interpreter allowing only predicate
calls, boolean/comparison/logic ops, literals, and scenario `vars` names. Predicates live in a
registry with unit tests. `over_repeats(agg, expr)` is handled specially at endpoint time
(aggregates the inner expr across repeat contexts). Drivers populate `RunContext.extras` (the
episode log) from real sim/Claim data; predicates read those + `ground_truth` — never system beliefs.
**Rationale.** Meets the "no free eval" contract, keeps scoring declarative in the DSL, and separates
"run the scenario" (driver) from "score it" (oracle).

## D-0010 — Scenario DSL schema is hand-authored (not ontology-derived)
**Context.** SCENARIOS.md §2 says "validate the DSL against a JSON Schema from ontology/". The DSL is
a bench concern, not an ontology concept.
**Decision.** The DSL JSON Schema is hand-authored at `bench/scenarios/dsl.schema.json`
(`additionalProperties:false` so unknown keys fail) and validated with `jsonschema` on load.
**Rationale.** The DSL vocabulary (arms/perturbations/oracle) isn't in the meaning ontology; deriving
it from `ontology/` would be a category error. Committed + validated satisfies the intent.

## D-0011 — Sweep points are a curve, not a pass/fail gate
**Context.** F1's `sweep: audit_level` + endpoint `scan_cost < full_scan_cost`: at level 0.99 you must
scan everything, so the cost endpoint legitimately fails at that extreme.
**Decision.** The scenario oracle is gated at the **primary** level (default vars); sweep points are
run additionally for the confidence-cost **curve** and marked `is_sweep=1` in metrics. The primary
records determine "the scenario passes"; sweep records are transparent curve data.
**Rationale.** Preserves the honest finding (99% confidence ⇒ full scan) while letting F1 pass at its
audit level across A0–A4.

## D-0007 — External systems are in-process mocks (not networked FastAPI) for the benchmark path
**Context.** PROJECT.md §7.1 lists FastAPI+SQLite mocks; NFR-LOCAL forbids network egress except
Gemini; the benchmark harness runs in-process and must stay deterministic + offline.
**Decision.** External systems (WMS ledger, audit/regulator/CRM portals, disposal manifest) are
in-process Python classes under `external/` that carry intentional ledger/reality divergence hooks.
They expose the same *semantic* surface the scenarios need (query book state, submit report,
drill-down). A FastAPI+SQLite wrapper is an optional future skin; the benchmark never needs a port.
**Rationale.** Keeps runs deterministic, offline, and fast (no sockets); satisfies "intentional
incompleteness" (ledger divergence) without a network dependency the harness would have to mock away.

## D-0006 — `make gen` degrades gracefully but real by default
**Context.** LinkML generation must produce JSON Schema / SHACL / types and be committed.
**Decision.** `make gen` runs LinkML generators (`gen-json-schema`, `gen-python`, plus a project
SHACL/NL-vocab emitter) writing to `ontology/generated/`. Outputs are committed. If `linkml` is
absent the target fails loudly (never silently skips) — it is a core dep so this is the normal path.
**Rationale.** Satisfies Rule #4 (ontology single source) and reproducibility (commit generated
artifacts) without a hidden stub on the critical path.
