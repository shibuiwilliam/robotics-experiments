# CLAUDE.md — Multi-World Search (MWS) Development & Operations Guide

> **Role of this document**
> This is the development & operations guide that defines **"how we build"** in the MWS repository. Claude Code reads this before starting a task.
> **"What, why, and what counts as success" (the project definition) lives in `PROJECT.md`.** When unsure about a design decision, always check `PROJECT.md` first.
> If this guide and the code conflict, confirm the intent of this guide, and update it if needed before changing the implementation.

---

## 0. Golden Rules (top priority, no exceptions)

1. **Before starting, read `PROJECT.md` and the responsibilities of the target module.** Do not start writing on a guess.
2. **Cloud calls happen in exactly two places.** Only `mws/embedding/` (embedding) and `mws/agents/` (LLM) may call Gemini. Do not write network calls to Gemini from any other module.
3. **Embedding is a single `gemini-embedding-2`.** Documents and queries are embedded with the same model (the student model is abolished). Cloud round-trip latency/cost is suppressed via the content-hash cache, single-shot multi-Content batching, and the Batch API (offline indexing). Mock mode is a deterministic stand-in.
4. **Thread a seed through all randomness.** Do not write any new stochastic process that does not take a seed.
5. **Never commit secrets.** API keys live in environment variables only. Do not write keys or tokens in code, config, logs, or tests.
6. **Do not break the dependency direction.** ([2.2](#22-dependency-direction-rules)) Lower layers do not import upper layers.
7. **Tests do not touch the cloud by default.** Live Gemini calls are only allowed in opt-in, marker-tagged tests.
8. **Do not overwrite experiment results.** Artifacts in `runs/` are append-only. Always leave the manifest needed for reproduction.
9. **Do not add dependencies on a whim.** If one is needed, state the reason and confirm it does not violate `pyproject.toml` or this guide's premises.
10. **Respect the Non-Goals.** (`PROJECT.md` §2.3) Do not stray into real hardware, production scale, real ERP integration, or training new models.

---

## 1. Development Environment Setup

### 1.1 Prerequisites

- OS: macOS (Apple Silicon assumed)
- Python: 3.11+
- Package / environment management: **uv** (fast, with lockfile management)

### 1.2 Initial Build

```bash
# Sync the venv and dependencies (pyproject.toml / uv.lock are authoritative)
uv sync

# Including dev extras
uv sync --extra dev

# Smoke test
uv run python -c "import mujoco, lancedb, duckdb; print('ok')"
```

### 1.3 Secrets (environment variables)

Put them in `.env` (gitignored) and reference them from code only through the settings layer.

```bash
# Gemini (LLM via ADK / Embedding 2)
GOOGLE_API_KEY=...           # for local development. Never commit the real value
# MWS operating mode
MWS_CLOUD_MODE=mock          # mock | live (default is mock; live only when explicit)
MWS_RUN_DIR=./runs           # output directory for experiment artifacts
```

- `MWS_CLOUD_MODE=mock` is the default. **Live cloud usage only happens when you explicitly set `live`.**
- **Spend gate (G1)**: A `scenario run` / `scenario-all` that resolves to live exits non-zero with a cost estimate unless `MWS_CONFIRM_LIVE_SPEND=1` (replay passes through because it incurs zero real spend). Before a run, it prints a banner with the resolved `cloud_mode`/embedding/vector backend/seed (G2/G5).
- The whole pipeline must run under `mock` even with no key set ([5.4](#54-offline--mock-mode)).

### 1.4 MuJoCo / Apple Silicon Notes

- `mujoco` (CPU) runs fine on arm64. Allocate parallel environments on CPU by default.
- **MJX (JAX/XLA edition) GPU parallelism is limited on Apple Silicon.** By default do not depend on MJX; run multiple instances in parallel / time-sliced on CPU MuJoCo. If you use MJX, separately verify seed, determinism, and performance before adopting it.
- Offscreen rendering (RGB-D generation) is environment-dependent and easy to get stuck on. When changing the rendering path, verify headless reproduction.

---

## 2. Repository Structure and Dependency Direction

### 2.1 Directories and Responsibilities

Corresponds to the components in `PROJECT.md` §7. Each directory keeps a single responsibility.

```
mws/
├── core/          # Experience-atom schema, envelope, provenance, access policy (lowest layer; depends on nothing else)
├── worldmodel/    # 4D scene graph (entity nodes, relations, time)
├── storage/       # Polyglot persistence adapters (vector/graph/timeseries/spatial/blob)
├── embedding/     # Gemini Embedding 2 (single model), content-hash cache, async indexer
├── retrieval/     # Multi-index search, query planner, fusion reranker, consumer-aware projection
├── consolidation/ # episodic→semantic distillation, dedup, TTL pruning
├── federation/    # edge↔cloud, QoR routing, predictive prefetch
├── reactive/      # standing query (pub-sub), curiosity loop
├── agents/        # Gemini ADK agents, A2A, MWS search tools (MCP-compatible) exposure
├── vla/           # retrieval-augmented VLA, recall/injection of skill-demo atoms
├── sim/           # MuJoCo worlds, sensors, multiple instances, state forking
├── business/      # synthetic business data (ERP/WMS/CMMS/MES/SOP/SDS) generation, stubs
├── scenarios/     # verification-scenario implementations
├── eval/          # metrics, auto relevance-label generation, latency decomposition, reports
├── configs/       # world / experiment / model configuration (config, not code)
└── cli.py         # unified entry point
tests/             # unit / integration tests (mirror of mws/)
runs/              # experiment artifacts (gitignored; append-only)
data/              # locally-generated artifacts such as synthetic business data (gitignored)
```

### 2.2 Dependency-Direction Rules

Dependencies may only flow bottom-up. Do not import upper layers.

```
core  ←  worldmodel  ←  storage  ←  embedding  ←  retrieval
                                                      ↑
            consolidation ─────────────────────────────┤
            federation / reactive ──────────────────────┤
   (consumers: agents / vla / scenarios)───────────────┘
   sim / business → supply atoms to core (input sources from the external world)
   eval may read all modules (output-only; nothing depends on it)
```

- `core` depends on no other MWS module.
- Do not create circular imports. Put shared types in `core`.
- Confine external I/O (DB, files, network) to each adapter layer, separate from pure logic.

### 2.3 The Cloud Boundary (the most important guardrail)

- Calls to Gemini are implemented **only** in `embedding/` (teacher) and `agents/` (LLM).
- If any other module's design starts to need the cloud, that is a sign of a design mistake. Return to the intent of `PROJECT.md`.

---

## 3. How to Run

Run via subcommands of the unified CLI (`mws/cli.py`). Specify configuration with `configs/`; override with flags.

```bash
# Start a simulation world and generate observation atoms
uv run python -m mws.cli sim run --config configs/worlds/warehouse.yaml --seed 0

# Indexing (offline, teacher embedding, Batch)
uv run python -m mws.cli index build --config configs/index/default.yaml

# Run a verification scenario (e.g., Scenario 1, maintenance handoff)
uv run python -m mws.cli scenario run --name maintenance_handoff --seed 0

# Evaluation (compute metrics, generate report)
uv run python -m mws.cli eval run --run-id <RUN_ID>

# Start an ADK agent locally
uv run python -m mws.cli agent serve --config configs/agents/ops_agent.yaml
```

- Do not write hard-coded absolute paths, keys, or model names outside CLI arguments or config.
- Design long-running processes (indexing, experiments) to be interruptible and resumable.

---

## 4. Configuration Management

- Configuration is authoritative as YAML under `configs/`; the code reads it via typed settings (pydantic-settings, etc.).
- **Do not mix config and code.** Magic numbers, thresholds, model names, and dimensions go into config.
- Layer the config: `worlds/`, `index/`, `embedding/`, `agents/`, `experiments/`, `scenarios/`.
- Every run receives `seed` via config and records it in the manifest ([Chapter 9](#9-reproducibility-and-experiment-discipline)).

---

## 5. Cloud-Usage Policy (Gemini LLM / Embedding 2)

> The exact arguments and model IDs of the external APIs (ADK, Embedding 2) may change. **Check the current official docs** at implementation time, and keep this guide's premises (mock by default, cache required, no cloud on the hot path).

### 5.1 Common

- Always call through an adapter (do not scatter the SDK directly). Centralize retry, timeout, and rate control in the adapter.
- Measure and log every cloud call by **count, tokens, cost, and latency** ([Chapter 10](#10-logging-and-observability)).
- The cloud is nondeterministic. **For comparisons that need reproducibility, cache/record the responses** and fix them together with the seed.

### 5.2 LLM (Gemini via ADK)

- The agent runtime (ADK) is local; only inference is cloud. Develop with local sessions/memory.
- Expose MWS search as ADK **tools (MCP-compatible)**; the agent recalls via the tool.
- Use A2A for multi-agent coordination.

### 5.3 Embedding 2 (offline indexing only)

- Do indexing **async and low-cost via the Batch API** (not on the hot path).
- Use MRL and **specify dimensions in config** (default starting point 768; 1536/3072 as needed). Record the dimension as an attribute of the index.
- If you adopt two-stage retrieval (coarse-filter at small dims → re-rank at full dims), handle both dimensions within the same model space.

### 5.4 Offline / Mock Mode

- With `MWS_CLOUD_MODE=mock`, the whole pipeline must run with the cloud replaced by a deterministic stub.
- Unit/integration tests, CI, and fast iteration run on mock. `live` is only for explicit experiments.

### 5.5 Cost Discipline

- Estimate token volume for indexing/experiments and prefer Batch. For large re-indexing, record the scale and cost in the log/manifest beforehand.

---

## 6. Embedding Operation Rules

- **Embedding is unified on a single `gemini-embedding-2`.** Documents/queries are embedded with asymmetric task-instruction prefixes (the student model and the two-tier teacher/student scheme are abolished). Round-trip reduction is done with the cache, batching, and the Batch API. Mock mode is a deterministic stand-in.
- **Never mix embedding spaces.** Do not put vectors of a different model/dimension into the same index or compare them by distance. Tag every vector with `embedding_space` (model + dims + version).
- **Model update = re-index.** Changing the embedding model changes the coordinate space and forces a re-index. Bump the version and distinguish it from the old space (make it an explicit experimental target related to the H's).
- **Content-hash cache is mandatory.** Never embed the same content twice. The cache key is content + model + dims.
- Multimodal embedding of visual atoms depends on Gemini (cloud latency remains). This is a known constraint; do not hide it — measure it.

---

## 7. Coding Standards

- **Formatting / linting**: `ruff format` and `ruff check`. Pass them before committing.
- **Types**: Annotate public functions and data structures and pass the type checker (mypy/pyright).
- **Data structures**: Make schemas such as experience atoms explicit with pydantic/dataclass. Avoid passing raw dicts around.
- **Naming**: Modules/functions are `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE`. Align MWS terms (atom, scene_graph, projection, etc.) with the terminology in `PROJECT.md`.
- **Isolate side effects**: Push I/O, network, and randomness to the edges; keep pure logic testable.
- **Logging**: Use the structured logger, not `print`.
- **Error handling**: Do not swallow errors. Handle cloud failures, missing indices, and empty hits explicitly.
- **Documentation**: Docstrings on public APIs. Leave the reason for non-obvious design decisions in a comment.
- **No magic numbers**: Thresholds, dimensions, and TTLs go into config.

---

## 8. Testing Policy

- Framework: `pytest`. `tests/` mirrors `mws/`.
- **No cloud contact by default.** Live Gemini is opt-in via a marker (e.g., `@pytest.mark.live`). Do not run it in CI or the default run.
- **Determinism**: Fix the seed and stay deterministic with the mock cloud. Leave no flaky tests.
- **Golden retrieval tests**: Generate relevance labels from sim ground truth and regression-test that Recall@k, etc., meet the bar.
- **Layering**: Pure logic is unit-tested; adapter integration is integration-tested (against local stores).
- Be fast. Shrink heavy sim/indexing with fixtures.

```bash
uv run pytest                 # default (mock, deterministic; live and elasticsearch excluded)
uv run pytest -m live         # live cloud (only when explicit)
uv run pytest -m elasticsearch  # ES-backend integration (`make es-up` + `uv sync --extra es`; excluded by default)
```

---

## 9. Reproducibility and Experiment Discipline

- **A seed on every run.** Initialize all random sources (Python/NumPy/simulator) in one place.
- **Run manifest required.** Each run records in `runs/<RUN_ID>/manifest.json`: the config used, git SHA, **git dirty/untracked state and the list of dirty paths**, dependency versions, model IDs, embedding space, **vector backend** (for ES, the url + index naming convention), seed, and cloud mode. The manifest alone must let you reconstruct "which code, model, config, and backend it ran on." Fields are append-only (never delete or rename existing keys).
- **Pre-register metrics.** Declare the metrics used for comparison before the run to avoid post-hoc cherry-picking (`PROJECT.md` §10).
- **Results are append-only.** Do not overwrite/delete `runs/`.
- **Statistics**: Run multiple seeds × multiple worlds and report with confidence intervals.
- Collect artifacts (metrics, figures, logs) under `runs/<RUN_ID>/`.

---

## 10. Logging and Observability

- Structured logs (JSON lines) by default, with `RUN_ID` attached to every log.
- **Latency decomposition** must be measurable: distinguish local ANN (`local_ann`) / ES search round-trip (`es_search`) / Gemini embedding call (`gemini_embed`) / Gemini inference (p50/p95). The vector-search bucket is decided by the backend (the store's `latency_bucket` attribute; ES is an HTTP round-trip, so do not lump it into `local_ann` — G6).
- **Cloud-call counters**: Aggregate count, tokens, and estimated cost, and record them in `metrics["cloud"]` and the manifest on every run (zero under mock).
- Have retrieval/federation carry a mechanism that measures virtual edge↔cloud bandwidth (the bytes that would have been sent).

---

## 11. Resource Constraints (single MacBook)

- Be mindful of the memory budget. Stream large arrays / point clouds; do not OOM by loading everything at once.
- Cap the number of parallel MuJoCo instances in config to match CPU/memory. Scale experiments gradually.
- Do indexing on a background async indexer so it does not block the interactive / control loop.
- Run heavy work (indexing, consolidation) in batch / off-peak.

---

## 12. Git / Commit Conventions

- Branches: `feature/<topic>` / `fix/<topic>` / `exp/<topic>`.
- Keep commits small and at a logical unit. Imperative, concise messages (e.g., `add cross-modal reranker`).
- **Do not commit**: secrets, `runs/`, `data/`, model weights, large binaries, generated caches (follow `.gitignore`).
- Keep each PR/change single-purpose. For changes that touch design, confirm consistency with the intent on the `PROJECT.md` side.

---

## 13. The Claude Code Workflow

1. **Read**: Check `PROJECT.md`, the target module, and related existing code/config.
2. **Plan**: Clarify the change scope, affected modules, dependency direction, and impact on the cloud boundary. If ambiguous, **ask before implementing**.
3. **Build small**: Minimal diff. One purpose per task.
4. **Protect**: Dependency direction (§2.2), cloud boundary (§2.3), seed, mock default, embedding-space consistency.
5. **Verify**: Pass `ruff` → type check → `pytest` (mock). If you change retrieval, update/confirm the golden tests.
6. **Record**: If an experiment is involved, leave a manifest. Reflect design decisions in comments / this guide.
7. **Do not grow**: State a reason before adding a dependency, a new cloud touchpoint, or a new directory. Do not stray into the Non-Goals.

---

## 14. Common Pitfalls

- **Mixing up embedding spaces**: Comparing vectors of different model/dims in the same index → always check `embedding_space`.
- **Cloud embedding on the hot path**: Calling Gemini at query time and adding latency → suppress round-trips with the content-hash cache + single-shot multi-Content batching (never re-embed the same content).
- **Forgetting the seed**: Results don't reproduce → initialize randomness in one place; put the seed in the manifest.
- **Mixing cloud nondeterminism into comparisons**: A/B comparing without fixing/recording responses → cache/record and fix them.
- **MuJoCo rendering's environment dependence**: Offscreen RGB-D crashes → verify headless reproduction; do not casually use MJX/Metal.
- **Gemini rate limits**: Jams from many synchronous calls → centralize Batch, async, and retry in the adapter.
- **Collapse of the dependency direction**: Importing upper layers from `core` and creating cycles → centralize shared types in `core`.

---

## 15. Definition of Done

- [ ] The change is single-purpose and breaks neither the dependency direction nor the cloud boundary
- [ ] `ruff` / type check / `pytest` (mock, deterministic) pass
- [ ] Retrieval-related changes are verified by golden tests
- [ ] Randomness is seed-managed and experiments leave a manifest
- [ ] No secrets / large artifacts committed
- [ ] Design decisions and trade-offs are explained (code / commit / this guide)
- [ ] Consistent with the intent of `PROJECT.md` (update this guide if needed)
