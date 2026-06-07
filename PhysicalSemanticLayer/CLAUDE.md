# CLAUDE.md — PSL-Bench Development & Operations Guide

> This file is the **canonical development reference that Claude Code reads at the start of each session**. It covers **"How to build."**
> For "Why / What to build" and research design / evaluation metrics, refer to `PROJECT.md`.
> **Priority on conflicts**: `PROJECT.md` takes precedence for concepts and research definitions; this file takes precedence for development operations, commands, and conventions.
> If the instructions in this file conflict with "making things work correctly," do not proceed on assumptions — **follow the Golden Rules in §1 and ask for clarification first**.

---

## 1. Golden Rules (Code of Conduct for This Repository)

1. **Never handle physical quantities as bare `float` values.** Passing physical quantities without units, frames, time domains, and uncertainty is forbidden. Always go through `Phyte` (§7).
2. **Do not write N×N adapters.** All conversions must go through the Canonical IR (PSL canonical language). Robots/agents implement only "native language <-> canonical language" (N+N). If you find a direct A<->B conversion, delete it and route through IR instead.
3. **Do not leak ground truth (MuJoCo state) into translators.** Ground truth is **for evaluation only**. If PSL conversion logic, adapters, or agents reference ground truth, that constitutes measurement contamination and must be fixed immediately. Ground truth may only be read from `eval/`.
4. **Do not discard uncertainty and provenance.** Implementations that return only point estimates from conversions are not allowed. Covariance, source, and confidence must always be propagated.
5. **Accompany every change with tests.** Changes to physical translation must add or update at least one oracle test and related metamorphic property tests (§9).
6. **Do not break determinism.** Do not write code that circumvents the seed, temperature, and version-pinning mechanisms (§10).
7. **Do not block control loops with external API calls.** Do not place Claude Agent SDK calls (second-order latency) inside high-frequency loops (§11).
8. **When in doubt, ask.** If the specification is ambiguous, inconsistent with `PROJECT.md`, or touches safety invariants (§8), ask rather than proceeding on guesswork.

---

## 2. Project Configuration and Assumptions

- **Host**: Single MacBook (Apple Silicon assumed). No GPU training; MuJoCo runs in **CPU-native mode**.
- **Precise meaning of "local"**: MuJoCo, PSL, orchestration, pseudo-cloud, and evaluation harness run locally. **Only Claude Agent SDK inference makes external calls to the Anthropic API** (= agent "thinking" is second-order latency, incurs billing).
- **Language/Runtime**: Python (pinned to the version specified below). The Claude Agent SDK internally uses the Node.js-based Claude Code CLI (bundled in the package). Note that Node.js may be required.

---

## 3. Environment Setup

### 3.1 Required Tools

- Python (**pinned to 3.12**. SDK requires 3.10+, but this project uses 3.12 as the standard)
- `uv` (dependency resolution and virtual environments. Chosen for speed and reproducibility on a single Mac)
- Node.js (Claude Agent SDK bundles a CLI. May need separate installation depending on environment. Version pinned in `CLAUDE.local.md` or `.tool-versions`)
- Xcode Command Line Tools (for native builds)

### 3.2 Setup Procedure

```bash
# 1) Virtual environment and dependencies
uv venv --python 3.12
source .venv/bin/activate
uv sync                      # Reproducible install from pyproject.toml + uv.lock

# 2) API key (never commit. .env must be gitignored)
cp .env.example .env         # Fill in ANTHROPIC_API_KEY=...

# 3) Verification
make doctor                  # Environment diagnostics (Python/MuJoCo/SDK/key presence)
make sim-smoke               # Verify MuJoCo minimal scene launches
make agent-smoke             # Verify SDK returns one query() call (small billing charge)
```

### 3.3 Key Dependencies and Versioning Policy

- **Dependencies must be declared in `pyproject.toml` and pinned via `uv.lock`**. Direct `pip install` is forbidden.
- Verify external API/SDK facts (argument names, model names, behavior) **against official documentation rather than relying on memory**.
  - Claude Agent SDK / API: https://docs.claude.com/en/docs_site_map.md
  - Claude Code: https://docs.anthropic.com/en/docs/claude-code/claude_code_docs_map.md
- Expected stack (lock file is authoritative for versions):
  - `mujoco` (physics & ground truth)
  - `claude-agent-sdk` (agents. `query()` and `ClaudeSDKClient`)
  - `pydantic` (v2. Phyte schemas) / `pint` (dimensions & units) / thin wrapper for geometry (SE(3))
  - `numpy` / `scipy`
  - `hypothesis` (metamorphic / property-based testing)
  - `pytest` (+ `pytest` markers)
  - `ruff` (lint + format) / `mypy` (types)
  - Structured logging & experiment management (lightweight. §12)

---

## 4. Repository Structure (Corresponds to PROJECT.md §5.2 Mechanisms & §10 Phases)

```
psl-bench/
├── PROJECT.md                # Full project definition (canonical source - Why/What)
├── CLAUDE.md                 # This file (How to build)
├── CLAUDE.local.md           # Personal local settings (gitignored, optional)
├── pyproject.toml / uv.lock  # Dependencies (pinning required)
├── Makefile                  # Single entry point for all commands (§6)
├── .env.example
├── src/psl/
│   ├── phyte/                # [Mechanism 1] Phyte: canonical data unit (pydantic+pint+SE3)
│   ├── ir/                   # [Mechanism 2] Canonical IR and conversion graph / fidelity contracts
│   ├── world_model/          # [Mechanism 3] Shared world model (lightweight SG, USD-migratable abstraction)
│   ├── contracts/            # [Mechanism 4] Fidelity contracts (declaration & verification)
│   ├── grounding/            # [Mechanism 5/6] Embedding grounding & affordances
│   ├── lod/                  # [Mechanism 7] Multi-resolution subscriptions
│   ├── negotiation/          # [Mechanism 8] Semantic handshake
│   ├── safety/               # [Mechanism 9] Physical consistency gate
│   ├── anchoring/            # [Mechanism 10] Document & business data physical anchoring
│   └── adapters/             # "Native language <-> IR" adapters per robot/agent (N+N)
│       ├── robots/<robot>/   #   Per robot
│       └── agents/<agent>/   #   Per agent
├── sim/                      # MuJoCo scenes, models, Schema Generator (§independent variables)
│   ├── menagerie/            #   Robot bodies (external models via submodules/fetch scripts)
│   ├── scenes/               #   Task scenes (cross-cutting task #42 etc.)
│   └── schema_gen/           #   Heterogeneity dials (units/frames/naming/control modes/noise)
├── agents/                   # Claude Agent SDK: supervisor/worker & MCP tool definitions
│   ├── tools/                #   In-process MCP tools such as query_world_model
│   └── topology/             #   Supervisor + worker configuration
├── pseudo_cloud/             # Pseudo-cloud (SQLite/JSON: inventory, SOPs, work orders)
├── eval/                     # Evaluation harness (ground truth is ONLY referenced from here)
│   ├── oracle/               #   Ground-truth-based (fidelity/calibration/contract accuracy)
│   ├── metamorphic/          #   Property-based (equivariance/invariance/commutativity)
│   ├── metrics/              #   Metric formula implementations (SE3 distance, NLL/ECE, etc.)
│   └── runner/               #   Experiment runner & dose-response sweeps
├── experiments/              # Run configurations (config-as-code) and results (per-run directories)
├── tests/                    # Unit & integration tests (including hypothesis)
└── docs/                     # Detailed design (Phyte schema spec, contract format, MR catalog, etc.)
```

**Critical boundary**: Maintain a one-way dependency direction between `eval/` (may access ground truth) and `src/psl/` / `agents/` (must NOT access ground truth). Importing `eval` from `src/psl` is a design violation.

---

## 5. Architecture Invariants (Enforced in Code)

These translate PROJECT.md §5.3 into implementation rules. Verified mechanically during review and CI.

- **IR-only routing**: Code under `adapters/` communicates only with IR. Cross-adapter imports are forbidden (enforced by lint rules).
- **Phyte required**: All physical data exchange between mechanisms uses the `Phyte` type exclusively. The `safety` gate must always be passed before writing to `world_model`.
- **A2A must be spatiotemporally resolvable**: All assertions in inter-agent messages must be resolvable to `(frame, SE3, time)`. Messages carrying unresolvable assertions will fail validation.
- **Uncertainty & provenance propagation**: Conversion function signatures must not return point estimates alone (must return types that include covariance and provenance).
- **Ground truth isolation**: As per §1-3.

---

## 6. Commands (All via Makefile)

When adding new scripts, always register them in the Makefile. Do not scatter ad-hoc procedures across documentation.

```bash
make doctor          # Environment diagnostics
make setup           # Initialization (uv sync, etc.)
make fmt             # ruff format
make lint            # ruff check (including custom rules for architecture invariants)
make type            # mypy (strict)
make test            # pytest (all markers)
make test-fast       # Excludes external API and heavy sim (separated by markers)
make test-oracle     # Ground-truth-based evaluation tests
make test-mr         # Metamorphic (hypothesis)
make sim-smoke       # MuJoCo minimal launch
make agent-smoke     # SDK single-shot (incurs small billing charge)
make eval CFG=experiments/<name>.yaml   # Evaluation run (generates per-run directory)
make sweep CFG=...   # Heterogeneity sweep (dose-response curves)
make check           # fmt+lint+type+test-fast (mandatory pre-commit gate)
```

> **Billing notice**: `agent-*` / `eval` (with agents) call the Anthropic API and incur charges. Make targets that use the API must clearly indicate this via prefixes or descriptions.

---

## 7. Coding Conventions

### 7.1 General

- **Types required**: `mypy` strict. `Any` is allowed only with a justifying comment.
- **lint/format**: `ruff` (formatting also unified under ruff). `make check` must pass before committing.
- **Keep it small and pure**: Physical conversions should be pure functions whenever possible. Side effects (world_model writes, API calls, I/O) must be isolated in boundary modules.
- **docstrings**: Public functions must document "input/output units, frames, assumptions, and information loss."

### 7.2 Handling Physical Quantities (Highest Priority)

- **Units via `pint`, coordinates with explicit frames, poses as SE(3)**. Do not inline rotations or homogeneous transforms in functions (use the geometry module in `src/psl`).
- Frame transforms must explicitly specify "from -> to" via types/argument names (e.g., `pose_in_world`). Implicit reference frames are forbidden.
- Timestamps must carry not just values but also **clock domain + temporal uncertainty** (required for Phase 4 clock skew verification).

### 7.3 Phyte Schema (pydantic v2 Guidelines)

`Phyte` is a self-describing object that **must** contain the following fields: semantic ID (ontology link), reference frame + SE(3) pose, timestamp + clock domain + temporal uncertainty, units & dimensions, covariance, provenance + confidence.
- Validators inspect "unit/dimension consistency" and "frame known-ness" at construction time, rejecting invalid values early.
- The detailed schema is maintained in `docs/phyte.md`; this file specifies policy only.

---

## 8. Safety Invariants (Never Violate)

- **Do not bypass the physical consistency gate (`src/psl/safety/`).** State updates to world_model must always pass through the gate. Transformations that would violate conservation laws, kinematic limits, teleportation prohibition, or causal ordering are rejected.
- The gate's **false rejection rate is also a measurement target** (PROJECT.md §8). Changes that relax the gate must report the impact on metrics.
- Safety logic is implemented in a **symbolically rigorous** layer. Outputs from learned components must not be used for safety decisions without verification (neuro-symbolic binding).

---

## 9. Testing Strategy (Dual-Track)

Translates PROJECT.md §7.1 into test code. **New features must include tests from both tracks.**

### 9.1 Oracle Tests (Ground-Truth-Based: `eval/oracle`, `tests/`)

- Uses MuJoCo state as ground truth to verify round-trip reconstruction error, calibration (NLL/ECE), and contract accuracy.
- Ground truth is obtained only via `eval/` (§1-3).

### 9.2 Metamorphic / Property Tests (`eval/metamorphic`, `hypothesis`)

No ground-truth labels required. Minimum relations to cover (catalog maintained in `docs/metamorphic.md`):
- Frame equivariance: `T(g*x) == g*T(x)`
- Unit invariance: semantic invariance under unit rescaling
- Temporal equivariance: consistency under time shifts
- Object substitution invariance
- Composability (commutative diagram): divergence between `A->IR->B` and `A->IR->C->IR->B` within threshold

### 9.3 Conventions

- Classify with `pytest` markers: `unit` / `oracle` / `metamorphic` / `slow` / `api` (billed).
- `api` and `slow` are excluded from default CI and run only on explicit request (`make test-fast` excludes them; `make test` runs all).
- Tests using randomness must have fixed seeds. `hypothesis` must be reproducible via `derandomize` or fixed seeds.

---

## 10. Determinism & Reproducibility

- **Seeds**: All random sources (numpy, sim, schema_gen, hypothesis) are injected from a central `seed` setting. Implicit use of global random state is forbidden.
- **Agents**: Temperature, model name, and SDK options are pinned in config. Non-determinism is handled through **multi-seed runs + distribution reporting** (do not draw conclusions from single-run results).
- **Config-as-code**: Experiments are fully described in `experiments/*.yaml`. Do not override behavior via CLI arguments (this breaks reproducibility).
- **Run manifest**: Each run saves used config, dependency versions, git commit, seeds, and metrics in `experiments/runs/<timestamp>-<name>/`. Code and results must always be linked.
- **Environment pinning**: Record `uv.lock`, Python/Node versions, and MuJoCo model revisions.

---

## 11. Claude Agent SDK Operational Rules

Official docs: https://docs.claude.com/en/docs_site_map.md (verify argument names and models **against documentation** before use)

- **API selection**: Use `query()` for one-shot queries. Use `ClaudeSDKClient` for workers requiring dialogue, custom tools, or hooks.
- **Custom tools = in-process MCP**: PSL access tools (`query_world_model` / `command_robot_semantic` / `subscribe_affordances` / `resolve_document_to_physical`) are implemented as SDK custom tools (in-process MCP servers) without spawning separate processes. These constitute the actual A2R standard interface.
- **Topology**: supervisor (planning) + worker (execution). Workers interact with PSL only via MCP tools. Workers must not directly access ground truth or world_model internals.
- **Model assignment (asymmetric)**: Splitting models between heavy reasoning and light subcontracting is permitted (e.g., upper-tier model for planning, lightweight model for routine subtasks). Assignments must be specified in config.
- **Latency/cost discipline**:
  - Do not call the SDK inside high-frequency control loops (§1-7). Agents intervene only at medium-to-low frequency semantic levels.
  - Log `usage` / `total_cost_usd` for every API call (§12). Produce cost estimates before sweeps.
  - Isolate billed tests with the `api` marker; do not run them in default CI.
- **Permissions**: Use least privilege for `permission_mode`, etc. Explicitly specify tool allow-lists; do not open unexpected tools.
- **Non-determinism**: Follow §10 — pin temperature, use multi-seed runs.

---

## 12. Logging and Experiment Management

- **Structured logs (JSON)** are the standard, with each entry including `run_id` / `trace_id` / module / simulation time / wall-clock time.
- Agent calls record message type, token count, cost, and elapsed time (for debugging and cost correlation).
- Metrics are centralized in the `eval/metrics` implementation; individual mechanisms must not re-implement them independently (prevents definition drift).
- Figures (dose-response curves, calibration plots, commutativity diagrams) are saved as artifacts in the run directory and are not committed (reference by path only).

---

## 13. Performance Guidelines (Single Mac Assumed)

- MuJoCo runs on CPU. Avoid heavy parallelism; prioritize correctness on a single machine first.
- **Separate three time-constant tiers**: high-frequency (in-sim physics) / medium-frequency (PSL canonicalization, world_model updates) / low-frequency (agent intervention). Cross-tier synchronization uses LOD subscriptions rather than direct coupling.
- Prefer vectorization (numpy). Avoid physics computation hot paths in Python loops.

---

## 14. Git, Commit, and PR Conventions

- **Branches**: `feat/...` `fix/...` `exp/...` (experiments) `docs/...`.
- **Commits**: Conventional Commits (`feat:` `fix:` `test:` `refactor:` `docs:` `chore:` `exp:`). One intent per commit.
- **Pre-commit gate**: `make check` (fmt+lint+type+test-fast) is mandatory.
- **PR requirements**: Purpose, correspondence to PROJECT.md RQ/H, added/updated tests (oracle + metamorphic), impact on safety invariants, impact on API costs.
- **Do not commit artifacts or secrets**: `.env`, API keys, large run artifacts, external model binaries (manage via fetch scripts/submodules).

---

## 15. Definition of Done (Completion Criteria for Each Change)

- [ ] `make check` passes (fmt / lint / type / test-fast).
- [ ] If changing physical translation: oracle tests + related metamorphic properties added/updated (`make test-oracle` `make test-mr`).
- [ ] Architecture invariants (§5) are satisfied: Phyte-mediated, IR-mediated, ground-truth-isolated, uncertainty-propagated.
- [ ] Safety gate (§8) is not bypassed / if relaxed, impact on false rejection rate is reported.
- [ ] If agent-related: temperature, model, and permissions are pinned in config; costs are logged.
- [ ] Run manifest is generated and results are linked to the commit.
- [ ] Related documentation (`docs/`, and this file if necessary) is updated.

---

## 16. Recipe: Adding a New Robot (N+N Workflow)

> The goal is to integrate with all counterparts by adding just one "native language <-> IR" adapter. **Do not touch any other robot's adapter.**

1. Add the robot body to `sim/menagerie/` (via fetch script/submodule; do not directly commit binaries).
2. Configure heterogeneity (units, frames, naming, control modes) in `sim/schema_gen/` if needed.
3. Implement **native language -> IR** and **IR -> native language** in `src/psl/adapters/robots/<robot>/`. Return `Phyte` with covariance and provenance attached.
4. Verify that the consistency gate in `src/psl/safety/` prerequisites are met (kinematic limits, etc.).
5. Register capability descriptors in `negotiation/` (for semantic handshake).
6. Tests: add round-trip oracle (error) + metamorphic (frame equivariance, unit invariance, commutativity) tests.
7. Confirm `make test-oracle test-mr` passes. **Verify via lint that no cross-adapter imports were created.**

## 17. Recipe: Adding a New Metamorphic Relation

1. Define the relation in `docs/metamorphic.md` (preconditions, transformations, expected invariance/equivariance, tolerance thresholds).
2. Implement in `eval/metamorphic/` using `hypothesis`. Make thresholds configurable.
3. If existing translations break, determine whether it is a bug or by-design, and record the finding linked to `PROJECT.md` RQ/H.

---

## 18. Common Pitfalls (Apple Silicon / This Configuration)

- **MuJoCo interactive viewer**: On macOS, the GUI viewer may not work when launched with the standard `python`. Use `mjpython` to launch the viewer (not needed for headless evaluation). Run CI/sweeps in headless mode.
- **Unit mistakes**: Computing directly with `float` without going through `pint` leads to unit accidents. Always go through Phyte (§1-1).
- **Frame mix-ups**: z-up/y-up, rotation conventions. Include the frame in argument names and use the geometry module.
- **Time domains**: Do not depend on wall-clock time even on a single machine. Explicitly specify the clock domain for each stream (this matters for Phase 4 skew injection).
- **API keys / billing**: `.env` is required, `api` tests are isolated, produce cost estimates before sweeps.
- **Ground truth leakage**: Do not read ground truth from `src/psl` or `agents`, even for debugging purposes. If you need to read it, move the code to `eval/`.
- **Async**: The SDK is async-first (anyio-based). Do not call blocking I/O directly in async contexts.

---

## 19. Related Documents

- `PROJECT.md` — Full project definition (Why/What, RQ/H, evaluation metrics, phases).
- `docs/phyte.md` — Phyte schema details.
- `docs/metamorphic.md` — Catalog of metamorphic relations and thresholds.
- `docs/contracts.md` — Fidelity contract format and verification.
- `docs/agents.md` — MCP tool specifications and multi-agent topology.
