# IMPROVEMENT.md — Claude Code as the primary engine & interface

Design + plan for making **Claude Code the primary engine and interface** to experiment with the
combination of Robotics × AI-agent × Ontology on Musubi. Grounded in `PROJECT.md` (WHAT/WHY),
`CLAUDE.md` (HOW + Golden Rules), and the current codebase (P0–P11 platform + v2 scenario layer with
F1/S5/F2/C3 passing offline). Newest state at top.

## 1. Objective (restated)

Two dimensions, delivered together:

- **Interface — the cockpit.** A single, discoverable, JSON-capable operator console that lets Claude
  Code (or a person driving it) run and *introspect* the whole platform: environment/invariant
  health, the ontology, scenarios, experiments, and any run's beliefs/events/oracle/trace. Today the
  platform is driven by scattered `make` targets and Python one-liners; the cockpit unifies them into
  one legible surface that Claude Code operates fluently.
- **Engine — Claude as a first-class agent backend.** Generalize the single cloud choke point so the
  agent-reasoning LLM is **registry-selected** (`gemini` | `claude`), and add a real Anthropic/Claude
  `ChatBackend` behind the VCR. Claude (`claude-opus-4-8`, adaptive thinking, structured-output
  planning) becomes the primary planner engine; ER (pointing) and embeddings stay Gemini (Anthropic
  offers neither). Offline stays deterministic via `FakeGeminiClient` / cassettes.

## 2. Invariant reconciliation (flagged per build-prompt §0.2)

Adding Claude as an LLM egress touches two rules. The Golden Rules win on *intent* (single choke
point, offline determinism, reproducibility, model-IDs-in-registry); the letter is generalized with
the user's explicit authorization, and the deviation is documented (DECISIONS D-0012, STATUS).

| Rule (CLAUDE.md) | Letter | Reconciliation |
|---|---|---|
| §0-1 "Gemini only, via `clients/`" | Gemini-only egress | → **"all LLM egress via `clients/`, provider from registry."** Claude is added *inside* `clients/`, lazy-imported. One boundary, now multi-provider. The `no-Gemini-import-outside-clients` test is generalized to `no-LLM-SDK-import-outside-clients` (adds `anthropic`). |
| §0-1 / NFR-LOCAL "network = Gemini only" | one egress host | → Gemini **+** Anthropic, both behind the VCR, **offline by default** (Fake/cassettes). Live only in `record`/`passthrough`. |
| §0-2 model IDs in registry | ✓ unchanged | `claude-opus-4-8` and provider selection live in `config/registry.yaml`. |
| §0-5 determinism, §0-8 offline `make check` | ✓ unchanged | Claude never called offline; Fake covers A2–A4; replay → 0 API calls. |

## 3. Priorities

1. **P1 Console (interface)** — highest value, zero invariant risk, immediately useful to Claude Code.
2. **P2 Provider abstraction + Claude backend (engine)** — additive, non-breaking; default provider
   stays offline-safe; Claude is opt-in via registry / `--provider`.
3. **P3 Validate + document** — tests (console, provider selection, AnthropicBackend via Fake),
   DECISIONS/STATUS/CLAUDE/registry/trace updates, `make check` green, commit.

If scope must be cut, cut breadth of console subcommands — never the invariant tests or the offline
guarantee.

## 4. Design

### 4.1 Console (`console/`, new top-level package)

`python -m console <cmd>` / `make console ARGS="..."`. Read-only observer (no writes to production
paths); every command supports `--json` for machine consumption.

| Command | Does |
|---|---|
| `status` / `doctor` | env (python, uv, keys present?), VCR mode, provider, invariant checks (append-only, no-LLM-import-outside-clients), artifact presence, counts. |
| `ontology concepts` / `ontology validate` | list generated concepts; validate a JSON-LD doc / world against SHACL. |
| `scenarios ls` / `scenarios show <id>` | list scenarios + DSL summary (arms, oracle, perturbations). |
| `run scenario <id> [--arm] [--json]` / `run experiment <E>` | run + oracle breakdown per arm; ingest to scoreboard. |
| `inspect <id> [--arm --seed]` | one run's RunContext: mediated beliefs, bus trace, oracle checks, F1 drilldown IRI chain, unapproved-irreversible. |
| `providers` | show configured LLM providers + which is active + key availability. |

Depends on: `bench.runner.run`, `bench.oracle`, `bench.runner.drivers`, `ontology.artifacts`,
`scoreboard.metrics`, `config`. No new heavy deps (stdlib `argparse` + existing).

### 4.2 Provider-agnostic engine (`clients/`)

- `config/registry.yaml` gains an `llm` block: `provider: gemini` (default), and `models.agent`
  becomes per-provider (`gemini`, `claude`) with `claude.id: claude-opus-4-8`, adaptive-thinking
  effort.
- `clients/backends.py`: add `AnthropicBackend` implementing `ChatBackend.generate()` via the
  `anthropic` SDK (lazy import), using `output_config.format` structured outputs against the plan
  JSON Schema. `select_chat_backend(provider)` returns Gemini/Anthropic/Fake per registry + key.
- `clients/chat.py` `ChatAdapter` becomes provider-neutral (reads provider + model from registry;
  still VCR-wrapped, keyed by (provider, model, prompt, schema)).
- `agents/gemini.py`: keep `GeminiPlanner` (works with any `ChatAdapter`), add `LLMPlanner` alias +
  `make_planner(provider=...)`. Offline uses `FakeGeminiClient` regardless of provider.
- The invariant test generalizes to forbid `anthropic` / `google.genai` / `google.adk` imports
  outside `clients/`.

## 5. Definition of Done

- `make check` green offline (lint, mypy, tests) — no keys, no network. Replay → 0 API calls.
- `make console ARGS="status"` and `python -m console run scenario e0_smoke --json` work offline.
- Provider selection: `provider=claude` resolves the AnthropicBackend structurally; offline uses Fake
  and produces a schema-valid plan; a contract test asserts the AnthropicBackend request shape.
- Invariant test forbids any LLM SDK import outside `clients/` (now incl. `anthropic`).
- DECISIONS (D-0012 multi-provider), STATUS (engine+interface section), CLAUDE registry note,
  `registry.yaml` (`llm` block + claude model), trace matrix updated.
- Live Claude cassette recording documented as pending keys (`make test-live` / `MODE=record`,
  `ANTHROPIC_API_KEY`).

## 6. Execution loop (per user's 7 steps)

Understand ✓ → Plan/Document (this file) → Implement (P1, P2) → Review/test/validate (P3, `make
check`) → Fix → Repeat. Each phase ends `make check`-green and is committed Musubi-scoped on
`feat/musubi-build`.
