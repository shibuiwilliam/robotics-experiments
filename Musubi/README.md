# Musubi 結

**A shared-ontology verification platform for robotics × business AI agents × external systems.**

Musubi lets multiple robots, multiple business AI agents, and multiple external
business systems share one layer of *meaning* so they can cooperate on a single
task. It ships as a research platform that runs almost entirely on a single
MacBook: a miniature MuJoCo warehouse world, a set of Gemini/Claude-powered
agents, and mock business systems (WMS/ERP/CMMS), all tied together by the
Musubi ontology and measured with reproducible, machine-scored benchmarks.

> The name **結 (musubi)** means *to tie together* — it refers both to binding
> the three worlds (physical, cognitive, business) and to the *binding* of
> observations to entities, which is the core idea of the ontology. The
> ontology namespace prefix is `msb:`.

---

## What Musubi actually measures

Musubi is **not** a study of robot dexterity. Loads are idealized (weld /
adhesion) and failures are injected probabilistically. What we measure is a
system's **honesty** — its ability to:

- **detect** the gap between belief and reality (ledger vs. the physical world),
- **mediate** conflicting claims about the same thing, and
- **explain** how it reached a decision, with a full accountability chain.

Everything is verified with **reproducible experiments and machine-scorable
metrics**, not anecdotes.

### The three worlds it ties together

| World | Lives in | Time scale |
|---|---|---|
| **Physical** — MuJoCo sim, skills, disturbances ("the invisible hand") | `sim/` | milliseconds |
| **Cognitive** — planning, mediation, document processing, delegation | `agents/` | seconds |
| **Business** — mock WMS/ERP/CMMS + SOP document corpus | `external/` | hours–days |

They speak through the **Musubi core** (`core/`): a Claim store, registry, bus,
belief mediation, norms, and explanation — all defined by a single source of
meaning in `ontology/`.

---

## Design principles (the things that make it work)

- **One cloud boundary.** LLMs are reached *only* through `clients/`, behind a
  VCR (record/replay) interface. That single choke point is what gives us
  reproducibility, cost control, and painless model migration.
- **Offline by default.** Tests and CI run in VCR **replay** mode with **zero**
  network calls. Cassettes are committed to the repo.
- **The ontology is upstream of everything.** Types, schemas, vocabulary, and
  SHACL shapes are *generated* from `ontology/src/` (LinkML) — never hand-written.
- **Determinism is local.** The only non-deterministic component is the LLM.
  Physics, mediation, and scoring are fully reproducible from a seed.
- **Claims are append-only.** Beliefs are superseded, never deleted — enabling
  bitemporal, forensic queries.
- **Capability-based decoupling.** Agents don't know specific robots; they speak
  in capabilities. Adding a new robot should take **zero** lines of agent code.

The full *what/why* lives in [`PROJECT.md`](./PROJECT.md); day-to-day *how*
(commands, conventions, workflows) lives in [`CLAUDE.md`](./CLAUDE.md); the 16
benchmark scenarios are cataloged in [`SCENARIOS.md`](./SCENARIOS.md).

---

## Quick start

You need **Python 3.11+** and [`uv`](https://github.com/astral-sh/uv). No API key
is required for the offline path.

```bash
# 1) Set up the environment (venv, deps, pre-commit)
make setup

# 2) Generate ontology artifacts from the LinkML sources
make gen

# 3) Verify everything is green — offline, no network
make check          # lint + types + tests (VCR replay)
```

To run a live LLM call you'll need a key:

```bash
cp .env.example .env
# put GOOGLE_API_KEY=... in .env  (never commit it)
uv pip install -e ".[dev,live]"   # cloud SDKs, only needed for record/passthrough
```

> **macOS note:** interactive viewers and demo recording require `mjpython`
> (a MuJoCo constraint). The `make` targets pick the right interpreter for you —
> `make demo` uses `mjpython`; headless runs use plain `python`.

---

## Common commands

`make` targets are the canonical interface; the real commands live in the
[`Makefile`](./Makefile).

| Command | What it does |
|---|---|
| `make setup` | Create the venv, install deps + pre-commit |
| `make gen` | Regenerate ontology artifacts from LinkML (run after editing `ontology/src/`) |
| `make check` | **PR gate / definition of done** — lint + types + tests (offline) |
| `make test` | Run the test suite (VCR replay, offline, CI-equivalent) |
| `make test-live` | Run live-API tests in *record* mode (needs a key + budget) |
| `make scenario S=<name> [MODE=replay\|record] [ARM=A4]` | Run a single scenario |
| `make experiment E=<E0..E7>` | Run one experiment program (arms × seeds) |
| `make console ARGS="status"` | Operator cockpit (e.g. `run scenario e0_smoke --json`) |
| `make dashboard` | Build & open the semantic-observability dashboard |
| `make report E=<E>` | Generate an experiment report |
| `make demo S=<name>` | Interactive viewer + GIF recording (macOS `mjpython`) |
| `make docs-check` | Validate Mermaid blocks in Markdown |

Run `make help` to see everything.

---

## Repository layout

```
ontology/     Single source of meaning — LinkML → JSON Schema, SHACL, vocab, types
core/         Semantic substrate — Claim store, registry, bus, mediation, norms, explanation
sim/          Physical world — MuJoCo worlds (MJCF), skills, the "invisible hand", rendering
perception/   Turns ground truth / Gemini-ER output into Claims (oracle/hybrid/live dial)
agents/       Cognitive world — ADK agents, tools, the capability compiler
external/     Mock business systems (WMS/ERP/CMMS) + SOP document corpus (intentionally imperfect)
clients/      Gemini/Claude adapters with the VCR — the ONE cloud boundary
bench/        Experiment harness — scenario DSL, runner, seeds, VCR
scoreboard/   Metrics, dashboards, reports (read-only; never writes to the production path)
config/       registry.yaml — model IDs, prices, budgets, dial defaults (no hardcoding elsewhere)
console/      Operator cockpit CLI
```

Dependency direction: `ontology → everyone`, the cloud exit is `clients` only,
and `scoreboard` is read-only. No cycles.

---

## Scenarios & experiments

Verification is organized as a set of machine-scored **scenarios** (the "sixteen
views of Musubi", see [`SCENARIOS.md`](./SCENARIOS.md)) and **experiment
programs** `E0`–`E7`, run across ablation arms `A0`→`A4` and multiple seeds.
A few landmarks:

- **E0** — smoke test: the full three-world stack runs end-to-end on arm `A4`.
- **F1** — anchor-bundle identity binding beats single-anchor binding.
- **F2** — under triple pressure, **zero** unapproved irreversible actions.
- **E7** — integration: ablation arms show a monotonic, significant improvement.

Every experiment is reproducible: same seed → identical simulator trajectory
(bit-for-bit), and VCR replay → zero API calls.

---

## Contributing & conventions

- Read the **Golden Rules** at the top of [`CLAUDE.md`](./CLAUDE.md) before making
  changes — especially: LLMs only via `clients/`, model IDs only in
  `config/registry.yaml`, Claims are append-only, keep determinism intact, and
  never build identity bindings for people (privacy).
- Branch names: `feat/…`, `fix/…`, `exp/E…`, `onto/…`, `docs/…`. Don't push to
  `main` directly. Use Conventional Commits.
- **Nothing is "done" until `make check` is green.** Commit generated ontology
  artifacts and VCR cassettes; never commit secrets.

---

## Status & scope

Musubi is a **research prototype**, not a production system. It deliberately
excludes real-robot control, sim-to-real transfer, dexterous manipulation, HA /
horizontal scaling, connections to real business systems, and model
fine-tuning. The deliverable is a **reusable benchmark and measurement
platform** (MusubiBench / MusubiKit), not a one-off demo.

## License

MIT.
