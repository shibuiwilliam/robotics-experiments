# SBP — Semantic Bridge Platform

[日本語](README.md) | English (this file)

> English translation of the Japanese original (snapshot of 2026-11-12). The Japanese file is canonical; numbers may lag behind it.

A "belief-maintenance system" that uses a world model (WM) × an ontology to line up observations of the physical world (sensors, cameras, equipment) against records in business systems (ERP/MES/WMS/CMMS, plans, regulations) on the same vocabulary, the same time axis, and the same confidence scale. It manages beliefs (claims carrying confidence, provenance, and modality) rather than truth, and treats discrepancies between the physical and business sides as first-class objects: detect → raise a ticket → resolve → learn.

This repository covers the platform core only. Use-case validation (real devices, sandbox experiments) is done in a separate repository.

## Quick start

Prerequisites: macOS 14+ / Apple Silicon, Docker Desktop or OrbStack, Homebrew, uv, Python 3.12.

```sh
make bootstrap   # uv sync → generate .env → docker compose pull (install the Homebrew dependencies by hand; there is no Brewfile)
make up          # start Compose (redpanda / postgres / oxigraph / objectstore). Profiles: PROFILES=ui,observability
make seed        # load the ontology, create topics, run migrations
make dev         # API (:8000, hot reload). Workers: uv run python -m scripts.dev --workers belief,reconcile
make check       # lint + types + layer dependencies + ontology-check + secret scan + unit tests (must be green before every commit)
make test-int    # integration tests (Compose required)
```

When working on the UI (`apps/ui`, Node 20+), run `make ui-install` → `make check-all`
(`make check` + UI type checking and tests). The UI is not part of `make check`
so that `make check` stays in a state that **always runs without any additional installation**.

See `CLAUDE.md` for the command list and development conventions, and `docs/development-rules.md` for details.

> Current status: **M0–M9 complete** (gates G0–G9 passed) / **M9 "entrances to operation" 4 / 4** (2026-11-08).
> Gate G8 (ADR-0025): the integration test that drives the 6 workers through operational code, simulating only the outside world (`test_g8_workers.py`), is green.
> Gate G9 (ADR-0030): the integration test that drives re-grounding execution, forecasting and scoring, and the API's forecasts through the operational entrances (`test_g9_entrances.py`), is green.
> Gate G10 (ADR-0035, 0036): the integration test covering the quarantine queue (broken input → API → review) and What-if (WM with interventions → index → not visible to the default requester) (`test_g10_entrances.py`), is green.
> The recording path (business system → ingest worker → event log and knowledge graph) went into operation on 2026-10-25 (ADR-0016).
> What remains is **23 items awaiting a human decision (excluding those already decided)**
> (`docs/HITL.md`), and constraints of the kind that "cannot be verified until applied to real data" (`docs/STATUS.md`,
> and the known constraints in `docs/PROGRESS.md`).

CI is `.github/workflows/ci.yml`. It is split into 4 jobs (`check` / `integration` / `audit` / `ui`); if `check` (no Compose needed)
fails, `integration` (Compose required) does not run — waiting 5 minutes adds no information when the types don't even pass.
**It has never been run because there is no remote** (`docs/HITL.md` H-23).

## Documentation map

| Path | Contents |
|---|---|
| `docs/design/sbp-design.md` | The **canonical** detailed design document (v0.1, 18 chapters + appendices). Read only the relevant chapter before implementing |
| `CLAUDE.md` | Core rules loaded every session (layer dependencies, the 12 invariants, conventions) |
| `docs/development-rules.md` | Consolidated overview of directory layout, file roles, and development conventions |
| `docs/TASK.md` | Development plan and task management (scope, quality gates, M0–M8 task breakdown, operations, risks) |
| `docs/PROMPT.md` | Prompt collection for driving development (session management, task execution, per-milestone, cross-cutting, operations, recovery) |
| `docs/PROGRESS.md` | Record of the development course (facts found during implementation, decisions that did not rise to an ADR, known constraints) |
| `docs/adr/` | Records of design decisions (MADR format, 36 entries. The template is `0000-template.md`) |
| `docs/HITL.md` | **Register of items awaiting a human decision** (in priority order. Once decided, file an ADR and remove the item from this document) |
| `docs/STATUS.md` | Milestone progress (M0–M8) and measured scale. The granularity of milestones is written only here |
| `.claude/rules/*.md` | Per-layer detailed rules (linked to paths; loaded automatically when a matching file is opened) |
| `.claude/settings.json` | Claude Code guardrails |
| `docs/runbooks/` | Operating procedures (re-grounding / version promotion / incident response / backup and restore / local environment) |
| `apps/ui/` | UI (discrepancy queue, belief view, expectation board, concept curation. ADR-0008) |
| `.env.example` | List of all environment variables (do not commit `.env`) |

Order of precedence: if documents contradict each other, follow the design document → CLAUDE.md → other documents.

## Development with Claude Code

Setup check: start Claude Code and run `/context`; `CLAUDE.md` should appear under Memory files. When `.claude/settings.json` is in effect, reading `.env` and editing generated files (`schemas/`, `src/sbp/core/vocab.py`) are automatically denied.
