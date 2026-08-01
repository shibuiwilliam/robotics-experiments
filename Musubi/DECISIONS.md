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

## D-0006 — `make gen` degrades gracefully but real by default
**Context.** LinkML generation must produce JSON Schema / SHACL / types and be committed.
**Decision.** `make gen` runs LinkML generators (`gen-json-schema`, `gen-python`, plus a project
SHACL/NL-vocab emitter) writing to `ontology/generated/`. Outputs are committed. If `linkml` is
absent the target fails loudly (never silently skips) — it is a core dep so this is the normal path.
**Rationale.** Satisfies Rule #4 (ontology single source) and reproducibility (commit generated
artifacts) without a hidden stub on the critical path.
