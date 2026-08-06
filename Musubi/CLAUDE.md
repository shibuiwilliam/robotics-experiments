# CLAUDE.md — Musubi Development & Operations Manual

This file is the **practical manual (HOW) for Claude Code (and developers) to work in this repository**. The definition of the project's purpose, requirements, and architecture (WHAT/WHY) lives in `PROJECT.md`; the canonical source of meaning lives in the Design Document, the Experiment Plan, and the Scenario Catalog. **This document sticks strictly to operations, conventions, and procedures. When you want to know a definition, read PROJECT.md.**

> This file is loaded every session. Keep it high-signal and low-redundancy. Do not re-explain concepts (link to PROJECT.md). Do not let this document bloat.

---

## 0. Golden Rules (obey first)

Violations are bounced immediately in review. These are a summary of the invariants in §7 and take top priority.

1. **Do not call an LLM outside `clients/` (single cloud boundary).** Access to ER, agent reasoning, and embeddings must always go through the VCR (`clients/`). Do not import `google.genai` / `google.adk` / `anthropic` directly from other modules (`clients/guard.py` enforces this). **D-0012**: the agent-reasoning LLM is selected in the registry (`llm.provider`: gemini | claude, default claude). Claude is adopted as the primary engine (a relaxation of Gemini-only, approved by the user), but the exit remains a single point in `clients/`. ER and embeddings stay on Gemini. Offline is deterministic via Fake/cassettes (zero API calls on replay).
2. **Do not write model IDs, unit prices, budgets, or thresholds in code.** Everything goes in `config/registry.yaml`. If you see a hardcoded model name, fix it.
3. **Claims are append-only.** Updates are done via `supersede`; nothing is deleted. Do not mix the ground-truth snapshot into system paths (it is for scoring only).
4. **The ontology is the single source.** Do not hand-write types, schemas, or vocabulary. Edit `ontology/src/` (LinkML) and regenerate with `make gen`.
5. **Do not break determinism.** Do not use bare `time.time()` / `datetime.now()` / unseeded `random` on production paths. Take time from the sim clock / scenario, and randomness from a seeded RNG (→ NFR-DETERM).
6. **Do not identify people.** Do not create an `IdentityBinding` for a person. Only anonymous location and pose (→ NFR-P).
7. **Do not commit secrets.** API keys go in `.env`. Do not write key material into `config/registry.yaml`.
8. **Pass `make check` before declaring done.** It is not "done" unless lint, types, and tests (VCR replay) are green.
9. **Respect the dependency direction.** `ontology → everywhere`, the cloud exit is `clients` only, `scoreboard` is read-only. Do not create cycles (→ PROJECT.md §6.2).
10. **Update the trace when you change something.** If you add a feature, update the trace matrix (PROJECT.md §15.3) and the tests. If WHAT/WHY changes, update PROJECT.md; if HOW changes, update this document.

---

## 1. Tech Stack and Toolchain

| Purpose | Tool | Notes |
|---|---|---|
| Language | Python 3.11+ | Type hints required |
| Env & deps | `uv` | `uv venv` / `uv pip` / `uv.lock`. pip works too, but uv is the standard |
| Lint & format | `ruff` | `ruff check` + `ruff format` |
| Type checking | `mypy` | Leaning strict (see config) |
| Testing | `pytest` | Default is VCR **replay** mode (no network needed) |
| Hooks | `pre-commit` | ruff, secret detection, mermaid validation at commit time |
| Task running | `make` | **The canonical interface is the make targets** (§3) |
| Physics | `mujoco` | Offscreen via `mujoco.Renderer` |
| Agents | `google-adk` | `LlmAgent` + custom FunctionTool |
| LLM/embedding | `google-genai` | but calls only inside `clients/` |
| Ontology | `linkml` (generation) · `pyshacl` (validation) | Generated artifacts are Git-managed |
| Storage | SQLite (Claim/cassette/embedding) · DuckDB (metrics) | Fully local |
| Mocks | `FastAPI` + SQLite | External business systems |

---

## 2. Environment Setup

```bash
# 1) Install dependencies and tools
make setup            # create uv venv → install deps → set up pre-commit

# 2) API key (not needed for VCR replay only)
cp .env.example .env
# write GOOGLE_API_KEY=... into .env (never commit)

# 3) Build the ontology artifacts
make gen              # ontology/src → ontology/generated

# 4) Sanity check (no network needed)
make check            # lint + types + test(replay)
```

### macOS essentials (`mjpython` vs `python`)

- **Interactive viewer / demo recording** requires `mjpython` (a constraint of `viewer.launch_passive()`).
- **Headless experiment runs** (`mj_step` loop, offscreen `Renderer`) are fine with plain `python`.
- The make targets pick the right one automatically: `make demo` uses `mjpython`; `make experiment` / `make scenario` use `python`. You only need to think about it when launching by hand.

---

## 3. Common Commands (canonical interface)

Treat the `make` targets as canonical. See the Makefile for the actual commands that run behind them.

| Command | What it does |
|---|---|
| `make setup` | Set up the environment (venv, deps, pre-commit) |
| `make gen` | Regenerate the ontology artifacts from LinkML (always after editing) |
| `make fmt` | Format with `ruff format` |
| `make lint` | `ruff check` (+ format-diff check) |
| `make types` | `mypy` type checking |
| `make test` | `pytest` (**VCR replay**, no network, same as CI) |
| `make test-live` | Hit the live API (**record** mode, needs key and budget, §8) |
| `make check` | `lint + types + test` — **the pre-PR gate / completion condition** |
| `make scenario S=<name> [MODE=replay|record] [ARM=A4]` | Run a single scenario |
| `make experiment E=<E0..E7>` | Run one experiment program (arm × seed expansion) |
| `make dashboard` | Generate and display the semantic-observability dashboard |
| `make demo S=<name>` | Run a demo with the `mjpython` viewer + GIF recording |
| `make docs-check` | Syntax-check the Mermaid inside Markdown |
| `make report E=<E>` | Generate an experiment report (`scoreboard/reports/`) |

---

## 4. Getting Around the Repository

The overall structure is in PROJECT.md §13. Here are just the developer's touchpoints:

- **New meaning (types/relations)** → edit `ontology/src/` → `make gen`. Do not grow hand-written type definitions.
- **Physics behavior / perturbation** → `sim/` (`worlds/` MJCF, `skills/`, `invisible_hand/`, `render/`).
- **Belief, mediation, norms, explanation** → `core/`.
- **Robot perception** → `perception/` (dial, pixel→world). The ER call itself is in `clients/`.
- **Agent reasoning and tools** → `agents/` (`tools/`, `capability_compiler/`).
- **Business systems, portals, documents** → `external/`.
- **Cloud calls** → `clients/` (VCR). Nowhere else.
- **How to run experiments / scenarios** → `bench/` (`scenarios/` DSL, `runner/`, `PREREG.md`).
- **Metrics, visualization, reports** → `scoreboard/`.
- **Configurable values** → `config/registry.yaml`.

---

## 5. Coding Conventions

- **Naming follows the ubiquitous language** (PROJECT.md §8). `Claim`, `IdentityBinding`, `ReversibilityClass`, `Realm`, etc. are used verbatim as code identifiers too. Do not create synonyms (`Assertion` ≠ `Claim`; do not paraphrase on your own).
- **Type hints are mandatory.** Public functions and data structures are fully annotated. Keep `mypy` green.
- **Do not create bare numbers or bare references** (design principles P1/P2). Quantities carry units (QUDT), coordinates carry a frame, cross-instance references are IRIs. Move raw numeric literals into `config` or a constants module.
- **Boundary messages are semantic envelopes** (JSON-LD, `@context` is a generated artifact). The `sim ⇄ core` boundary speaks in these.
- **Time is bitemporal** (validTime/transactionTime) — keep it in mind. Do not conflate "when it was true" with "when it was recorded."
- **Do not swallow errors.** Expected deviations go on the bus as an `Exception` (a domain concept, Design Document §3.8). Leave runtime exceptions in the log with their origin.
- **Logs are structured.** Include the Case/Episode IRI in the trace key (observability).
- **Localize side effects**: `scoreboard/` is observation-only (never writes to a production path). Prefer pure functions.
- **Docstrings say "what and why".** Let the code itself speak the "how" of the implementation. Design decisions go in the trace matrix or the decision log.

---

## 6. Testing Strategy

| Layer | Content | Run with |
|---|---|---|
| Unit | Intra-module logic (mediation rules, decay, subsumption, pixel→world, etc.) | `make test` |
| Schema validation | Conformance of sample data against generated JSON Schema / SHACL | `make test` |
| Determinism | Run twice with the same seed → assert bit-identical `qpos` trajectory / **zero API calls** on replay | `make test` |
| Scenario oracle | Pass/fail via the DSL oracle (sim ground truth, SHACL, planted answers) | `make scenario` |
| Contract | ER coordinate convention & output schema, capability matching, ADK tool I/O | `make test` |
| Documentation | Mermaid syntax in Markdown | `make docs-check` |

Discipline:

- **CI and `make test` are always VCR replay** (no network). Cassettes are committed to the repository.
- A test that requires a new LLM input should first record a cassette once via `make test-live` (record), commit the artifact, and then be pinned to replay.
- **Do not make assertive assertions on LLM output.** Verify structure (schema conformance, invariants, IRI resolvability). Handle generation variance statistically (Experiment Plan §8).
- Tests touching irreversibility / safety (zero unapproved-irreversible) are treated as **must-pass assertions**; no flakiness is tolerated.

---

## 7. Architecture Invariants (detail)

Background and application of the Golden Rules (§0).

- **The cloud boundary is a single point**: the three Gemini services exist only behind the VCR interface in `clients/`. This is what makes reproducibility, cost, and model migration (ER 1.6→2) work. Break it and everything breaks.
- **The ontology is upstream**: `ontology/` depends on nothing. Types, schemas, vocabulary, and SHACL are generated from here, and all modules reference the same version. Generated artifacts are committed too (for reproducibility).
- **Immutability of Claims**: append-only, replace via supersede, no deletion. This is what makes bitemporal queries (forensics, S5/E2) possible.
- **Localization of determinism**: nondeterminism is LLM-only. Everything else (physics, mediation, scoring) is deterministic and fully reproducible from a seed. Hence manage the origin of time and randomness strictly.
- **Authority is per-aspect**: do not create a global master. The authority for position and the authority for inventory quantity are different (PROJECT.md §8, Authority).
- **Loose coupling via capabilities**: the agent does not know a specific robot; it speaks in Capabilities. Adding a new machine does not change agent code (the subject of E4's verification).

---

## 8. External API Practice (inside `clients/`)

### VCR modes

- `replay` (default): cassettes only. An unrecorded call **throws `MissingCassette` to surface it** (CI catches accidents).
- `record`: live-call only the unrecorded ones and save them. Replay the existing ones.
- `passthrough`: always live (debug only).
- Key = `hash(model ID, normalized request)`. Images are deduplicated by content hash. **Do not mix time, randomness, or per-run-varying values into the prompt** (it defeats the cache).

### Gemini Robotics (ER)

- Model IDs are in `config/registry.yaml` (e.g. `gemini-robotics-er-1.6-preview`; ER 2 is a drop-in replacement).
- Coordinates are **normalized `[y,x]` (0–1000)**, boxes are `[ymin,xmin,ymax,xmax]`. Worldization lifts by sim depth (Experiment Plan §4.2, `perception/`).
- Receive output as **structured output (with a generated JSON Schema) and validate immediately**. A validation failure becomes a "perception exception" Claim (do not trust the LLM).
- Detection uses a low thinking budget; relational reasoning uses a high one. The budget is registry-managed too.
- Preview spec. Keep a standing **contract test** for the coordinate convention and schema to detect breaking changes.

### ADK (agents)

- `LlmAgent` + **custom FunctionTool only**. Do not mix with built-in tools (search, code execution) (to avoid the constraint). Split into a separate agent if needed.
- Tool I/O schemas come from the ontology artifacts. Do not hand-write them.
- 429 handling is `HttpRetryOptions` + a rate limiter + a daily budget guard. The bench uses **stateless calls** (does not use the Interactions API = does not break VCR replay). Only interactive demos may be an exception.
- Delegation records a Musubi `Delegation` Claim at the same time as the ADK-internal call (keep them consistent with the accountability chain).

### Embedding

- `gemini-embedding-2` (multimodal). Default dimension 768 (MRL, truncation with auto-normalization). **Do not mix with `gemini-embedding-001`** (space-incompatible).
- All embeddings are locally cached as `hash(model, dim, content) → vector`. **Never pay twice.**
- Use the Batch API for bulk embeddings. Task instructions use the prompt-prefix method.

---

## 9. Development Playbook (frequent procedures)

### 9.1 Add a scenario

1. Write `bench/scenarios/<name>.yaml` per the DSL (initial world, ledger divergence, invisible hand, faults, `norms_active`, oracle, arms, repeats, seed).
2. If needed, add acceptance SHACL to `ontology/shapes/` and a mock to `external/`.
3. Run `make scenario S=<name> MODE=record ARM=A4` once to generate cassettes → commit.
4. Confirm the oracle passes by machine judgment → `make scenario S=<name>` (replay).
5. `make check`.
6. Update the Scenario Catalog §4 matrix and the trace matrix.

### 9.2 Add a robot / capability (goal: zero lines of code)

1. Add an MJCF (body/actuator/camera) to `sim/worlds/` (include recommended).
2. Add the capability description (Capability YAML) to `core/registry` (or config).
3. **Do not change any code in `agents/`** — the capability compiler generates the tools.
4. Contract test: confirm that the requirement for that actionType is subsumed by the capability.
5. Confirm `git diff` did not touch agent logic (the invariant of E4).

### 9.3 Add / change an ontology concept

1. Edit `ontology/src/` (LinkML).
2. **Dual representation**: always pair a SHACL constraint with a natural-language definition (definition text, usage example, counterexample).
3. `make gen` → regenerate the artifacts and commit.
4. Bump SemVer (addition = minor, breaking = major). Pass the compatibility check.
5. Impact analysis: check which modules/agents use the concept.

### 9.4 Add an agent tool

1. Define a FunctionTool in `agents/tools/`. I/O schemas come from the artifacts.
2. Register it on the `LlmAgent` (do not mix with built-in tools).
3. Add a replay-mode test (expected input → structural assertion).

### 9.5 Add an external-system mock

1. Implement it under `external/<system>/` with FastAPI + SQLite. Give it **intentional incompleteness** (a place to stage a ledger divergence).
2. Connect to the bus with semantic envelopes.
3. Make the initial state declarable from the scenario DSL.

### 9.6 Run an ablation experiment

- `make experiment E=E7` expands A0–A4 × seeds and aggregates into `scoreboard/`. Chart it with `make report E=E7`.
- Write the primary endpoints into `bench/PREREG.md` and commit **before** running (no post-hoc).

---

## 10. Git / Branch / Commit / PR

- Branches: `feat/<topic>` `fix/<topic>` `exp/<Ex>` `onto/<change>` `docs/<topic>`. Do not push directly to `main`.
- Commits use Conventional Commits (`feat:` `fix:` `test:` `onto:` `exp:` `docs:` `chore:`). Small, one meaning per commit.
- **Commit generated artifacts (`ontology/generated/`) and cassettes** (reproducibility). Do not commit secrets or large binaries.
- `make check` green before a PR. Write the corresponding FR/NFR, experiment E, and scenario in the PR description (traceability).
- Write a thicker explanation for changes touching safety, irreversibility, or privacy.

---

## 11. Claude Code Working Rules (for you)

How to behave when working in this repository.

- **Plan large changes first.** If it spans three or more modules, or includes a breaking ontology change, present the approach in 1–2 paragraphs before starting.
- **Re-check the Golden Rules (§0) every time.** Especially "Gemini in clients only," "model IDs in the registry," "Claims append-only," "determinism," and "no person identification."
- **Do not write model names / API specs from guesswork.** Look at the registry. If unclear, propose `make test-live` for live confirmation and do not hit production on your own.
- **Do not say "done" until the Definition of Done (§13) is satisfied.** Especially `make check` green, tests added, docs consistent.
- **Cost awareness.** The default is replay. Keep live calls (record) to the minimum, and confirm the budget (registry) and PREREG first.
- **Respect the boundary with PROJECT.md.** When you want to write WHAT/WHY, put it in PROJECT.md. This document holds only HOW.
- **Match the existing vocabulary and patterns.** Before adding a new abstraction, check the ubiquitous language and existing modules.
- **Do not fill gaps with speculation.** If there is no basis in the design documents, confirm with options and a recommendation.
- **Validate Mermaid in documents with `make docs-check`** before shipping.

---

## 12. Cost / Safety / Privacy Guardrails

- **Budget**: every Gemini call is accounted against the registry's unit price and daily cap. Stop when over the cap. Do not re-charge cassettes or the embedding cache.
- **Secrets**: `.env` only. Do not disable pre-commit's secret detection.
- **Privacy**: do not create or store person-identifying information (anonymous location/pose only).
- **Safety**: do not use untrusted external input (notifications, the memo field of a document) as the sole basis for an irreversible action. The irreversible must always pass the gate (SHACL + norms + reversibility + approval). The test's "zero unapproved-irreversible" is a must-pass.
- **The only network destination is Gemini.** Do not add other external communication (external systems are all local mocks).

---

## 13. Definition of Done

The checklist before calling work "done."

- [ ] `make check` is green (lint, types, test replay).
- [ ] Added/changed logic has a test (determinism, oracle, or contract, whichever applies).
- [ ] If you changed the ontology, `make gen` was run, artifacts committed, SemVer bumped, dual representation present.
- [ ] Gemini calls are inside `clients/`, model IDs are in the registry, cassettes committed.
- [ ] Claims are append-only / determinism is intact / no person identification.
- [ ] Related documents (the trace matrix in PROJECT.md, Scenario Catalog §4, PREREG) updated.
- [ ] The PR description states the corresponding FR/NFR, experiment E, and scenario.

---

## 14. Troubleshooting Quick Reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `MissingCassette` on a replay test | A new LLM input is unrecorded | Record once via `make test-live` (record) → commit → pin to replay |
| Interactive viewer won't start (macOS) | Launched `launch_passive` with `python` | Launch with `mjpython` (use `make demo`) |
| Determinism test fails | Bare time / unseeded randomness crept in | Use the sim clock for time, a seeded RNG for randomness (§0-5) |
| SHACL validation fails | Stale artifacts / data violates the convention | Retry after `make gen`. Suspect bare numbers/references |
| Cost spikes | Running in record/passthrough instead of replay | Return to the default replay. Check the registry budget guard |
| Type check won't pass | Generated types not reflected | `make gen` → review imports |

---

*This is Development & Operations v0.1. Tool choices (uv/ruff/mypy/make, etc.) are at this document's discretion, so update them to match reality. However, the Golden Rules in §0 and the invariants in §7 are rooted in the project definition (PROJECT.md), so keep them consistent with PROJECT.md when changing them.*
