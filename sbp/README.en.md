# SBP — Semantic Bridge Platform

[日本語](README.md) | English

> English version of README.md (the Japanese file is canonical; numbers may lag behind it).

**A belief-maintenance system that puts physical-world observations and business-system records on the same footing.**

In factories and warehouses, the "reality" seen by sensors and cameras and the "books" kept in ERP / MES / WMS / CMMS often drift apart.
SBP finds that drift, raises it as a ticket, lets a person resolve it, and learns from the result.

- **It holds beliefs, not truth.** Every claim carries a confidence, provenance, and a modality (observed / inferred / predicted / planned / normative / hypothetical).
- **It compares the physical and the business on one scale.** A world model (WM) and an ontology align vocabulary, time axis, and confidence.
- **Discrepancies are first-class objects.** The platform owns the flow detect → raise → resolve → learn.

> This repository contains **the platform core only**. Use-case validation on real machines or in sandboxes happens in a separate repository (ADR-0001).

## Contents

[How it works](#how-it-works) · [Getting started](#getting-started) · [Common commands](#common-commands) · [Layout](#layout) · [Status](#status) · [Documentation](#documentation) · [Contributing](#contributing)

## How it works

```
 Physical      ──► encode ──► latent index ──► probes ──► event promotion ──┐
 observations      (WM)       (pgvector)      (calibrated)                  │
 (sensors…)                                                                 ▼
                                                                 ┌── belief update ──┐ ──► knowledge graph
 Business      ──► ingest ──► mapping ──► business events ──────►│  merges 3 sources │      (Oxigraph)
 systems           (DSL)                                         └─────────┬─────────┘
 Plans, norms ─────────────────────────────────────────────────────────────┘
                                                                           │
                              discrepancy detection (reconciliation) ◄─────┘
                                          │
                    resolved by people (API / UI) ◄┘──► forecasts / What-if / actions
```

- Three data paths: **record** (business system → events), **estimation** (observation → latent → probe → event), **belief update** (merge the three sources).
- Four Docker Compose services underneath: Redpanda (event log), Postgres + Timescale + pgvector, Oxigraph (knowledge graph), SeaweedFS (object store).
- Layer dependencies run one way only (checked mechanically with import-linter):

  ```
  core ← eventlog ← knowledge ← {integration, worldmodel} ← bridge ← reasoning ← actions ← api
  ```

For details, see `docs/design/sbp-design.md` (canonical); read only the chapters you need.

## Getting started

**Prerequisites**: macOS 14+ / Apple Silicon (Intel: `SBP_PLATFORM=linux/amd64`), Docker Desktop or OrbStack, Homebrew, [uv](https://docs.astral.sh/uv/), Python 3.12. Node 20+ only when working on the UI.

```sh
make bootstrap   # sync dependencies → generate .env from .env.example → pull images
make up          # start Compose (redpanda / postgres / oxigraph / objectstore)
make seed        # load the ontology, create topics, run migrations, create buckets
make dev         # start the API (http://localhost:8000, hot reload)
```

Check that it works:

```sh
make ps                          # are the 4 services healthy?
curl -s localhost:8000/health    # API response
make check                       # lint + types + layering + ontology + secret scan + unit tests
```

Workers are not started by default. Pick the ones you need:

```sh
uv run python -m scripts.dev --workers belief,reconcile
```

Stop with `make down` (data is kept). To delete the data too, use `make nuke` (the only deletion path).

> **Secrets**: put API keys and the like only in `.env` (gitignored). Never in code, tests, logs, or fixtures. The LLM runs in `replay` mode (fixtures only) by default; `live` runs only when you ask for it explicitly.

## Common commands

| I want to… | Command |
|---|---|
| Run the full pre-commit check (must be green) | `make check` |
| Include the UI | `make ui-install` → `make check-all` |
| Unit tests (no Compose or network) | `make test` |
| Integration tests (Compose required) | `make test-int` |
| Regenerate `vocab.py` / `schemas/` from the ontology | `make gen` → `make ontology-check` |
| Encode observations into latents and index them | `make encode` |
| Train the WM (Koopman) | `make train-wm SEED=…` |
| Train a probe (stored only if it passes the gate) | `make train-probe FLUENT=… PROBE_ID=… SEED=…` |
| Recompute past beliefs under a new version (dry-run by default) | `make reground ARGS='--entity … --apply'` |
| Produce and score forecasts | `make forecast ARGS='--entity … --fluent … --probe-id … --seed N'` |
| Compare rollouts with and without an intervention (What-if) | `make whatif ARGS='--entity … --action-type … --seed N'` |
| Check dependencies for vulnerabilities | `make audit` / `make ui-audit` |
| List all commands | `make help` |

Operational procedures are in `docs/runbooks/` (local setup, re-grounding, version promotion, What-if, incident response, backup).

## Layout

```
ontology/    ontology (core / domain / shapes=SHACL / process=clingo / norms)
schemas/     generated: JSON Schema derived from EventType (do not edit by hand)
src/sbp/     core library (core, eventlog, knowledge, integration, worldmodel, bridge,
             reasoning, actions, llm, api, testing)
services/    6 workers (bridge / belief / reconcile / scheduler / learning / ingest)
scripts/     jobs (encode, train-*, reground, forecast, whatif)
apps/ui/     UI (discrepancy queue, belief view, expectation board, concept curation). localhost only
infra/       compose.yaml, DB migrations (alembic)
mappings/ reliability/ …   mapping, reliability and other configuration
docs/        design, ADRs, runbooks, progress, items awaiting human decision
tests/       unit / integration / contract / property
```

## Status

**M0–M10 complete** (gates G0–G10 passed, 2026-11-08).

| Item | State |
|---|---|
| Automated tests | `make check` 1952, integration 236, UI 126 — all green |
| Operational entrances | record, estimation, belief update, reconciliation, re-grounding, forecasting, quarantine queue, and What-if all run through the API / jobs |
| Not validated | **Not validated on real data** (synthetic data and a mock business system only) |
| Remaining | 23 items awaiting human decision (`docs/HITL.md`); identity binding, LLM-backed entrances, etc. |

CI is defined in `.github/workflows/ci.yml` (`check` → `integration`, `audit`, `ui`), but **it has never run**:
this project lives under `sbp/` in a repository that holds other projects, and GitHub Actions reads only the repository-root `.github/` (`docs/HITL.md` H-23).

For the history and the numbers, see [`REPORT.en.md`](REPORT.en.md) (development and verification summary) and `docs/en/STATUS.md`.

## Documentation

| Read first | Contents |
|---|---|
| [`REPORT.en.md`](REPORT.en.md) | Summary of development and verification so far |
| `docs/en/design/sbp-design.md` | The **canonical** detailed design (18 chapters + appendices). Read only the chapters you need |
| `docs/en/STATUS.md` | Milestone progress and measured size |
| `docs/en/HITL.md` | Register of **items awaiting a human decision** |
| `docs/en/adr/` | Design decision records (MADR format, 37 files including the template) |
| `docs/en/runbooks/` | Operational procedures |

English translations live in `README.en.md`, `REPORT.en.md`, and `docs/en/`. **The Japanese originals are canonical**; numbers may lag behind them.

<details>
<summary>Other development documents (Japanese only)</summary>

| Path | Contents |
|---|---|
| `CLAUDE.md` | Core rules loaded every session (layering, the 12 invariants, conventions) |
| `docs/development-rules.md` | Directory structure, file roles, consolidated development conventions |
| `docs/TASK.md` | Development plan and task management (quality gates, task breakdown) |
| `docs/PROMPT.md` | Prompt collection for driving development |
| `docs/PROGRESS.md` | Development log (facts discovered, known constraints) |
| `.claude/rules/*.md` | Per-layer detailed rules (loaded automatically when you open matching files) |
| `.env.example` | List of all environment variables |

Precedence when documents conflict: design document → `CLAUDE.md` → everything else.

</details>

## Contributing

- One task per branch. Commits follow Conventional Commits (`feat(bridge): …`). Get `make check` green before committing.
- A decision that departs from the design is written up first as an ADR in `docs/adr/`. Ontology changes come before code (procedure in `CLAUDE.md`).
- Invariants to respect (excerpt): assertions are append-only, never UPDATE or DELETE; `modality` has exactly six values, and output of LLMs, rules, and probes is always `inferred`; never call an LLM from the belief-update path; reference IRIs only through `sbp.core.vocab`. All 12 are in `CLAUDE.md`.
- Never hand-edit generated files (`schemas/`, `src/sbp/core/vocab.py`, `ontology/imports/`); produce them with `make gen`.
- Developing with Claude Code: start it and run `/context`; confirm `CLAUDE.md` appears under Memory files. With `.claude/settings.json` active, reading `.env` and editing generated files are denied automatically.
