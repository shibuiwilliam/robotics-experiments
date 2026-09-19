# Robotics Experiments

[日本語](./README.ja.md)

A collection of reproducible research prototypes exploring how robots, AI agents, world models, ontologies, memory, and business systems can work together.

The projects share a practical philosophy: use simulation as measurable ground truth, keep experiments small enough to run locally, and make claims through repeatable tests rather than one-off demos. Most development and test workflows run offline; integrations with cloud models are optional and isolated behind explicit commands.

> This is a research workspace, not a production robotics stack. The experiments primarily target MuJoCo on an Apple Silicon Mac and use simulated robots and synthetic business systems.

## Projects

Each directory is a standalone project with its own environment, documentation, tests, and experiment outputs.

| Project | Research question | Good place to start |
|---|---|---|
| [Multi-World Search](./MultiWorldSearch/) | Can physical observations, business records, documents, and skills become one searchable memory for an embodied-agent swarm? | [README](./MultiWorldSearch/README.md) · [Scenarios](./MultiWorldSearch/SCENARIOS.md) |
| [Musubi](./Musubi/) | Can robots, business agents, and external systems cooperate through one shared ontology while preserving provenance and accountability? | [README](./Musubi/README.md) · [Project definition](./Musubi/PROJECT.md) |
| [Physical Semantic Layer](./PhysicalSemanticLayer/) | How faithfully can heterogeneous robots, agents, and cloud systems translate physical meaning through a common semantic layer? | [README](./PhysicalSemanticLayer/README.md) · [Scenario overview](./PhysicalSemanticLayer/docs/scenarios/overview.md) |
| [RO-SimLab](./RO-SimLab/) | Can a robotics ontology close the loop from physical perception to business reconciliation, task execution, verification, and write-back? | [README](./RO-SimLab/README.md) · [Research notes](./RO-SimLab/RESEARCH.md) |
| [Tuned Past Action](./TunedPastAction/) | Can a VLA reuse past actions safely under distribution shift by geometrically adapting them to the current scene? | [Project definition](./TunedPastAction/PROJECT.md) · [Results](./TunedPastAction/results/REPORT.md) |
| [GTWM](./gtwm/) | Can a learned world model and a business ontology form a grounded, predictive data space for warehouse operations? | [PoC plan](./gtwm/docs/poc_plan.md) · [Status](./gtwm/docs/status.md) |

### Which project should I explore?

- Start with **Multi-World Search** for retrieval, shared memory, and multi-agent coordination.
- Choose **Musubi** for ontology-driven cooperation, claim mediation, and auditable decisions.
- Choose **Physical Semantic Layer** for units, coordinate frames, uncertainty, translation fidelity, and safety gates.
- Choose **RO-SimLab** for a complete semantic loop between a simulated warehouse and business records.
- Choose **Tuned Past Action** for memory-augmented VLA policies and interpretable trajectory adaptation.
- Choose **GTWM** for learned world models, symbolic grounding, consistency gaps, and what-if prediction.

## Getting started

### Prerequisites

The exact requirements vary by project, but you will generally need:

- macOS on Apple Silicon for the fully tested local workflow
- Python 3.11 or 3.12, as specified in each `pyproject.toml`
- [`uv`](https://docs.astral.sh/uv/) for Python environments and dependency locking
- MuJoCo for simulation (installed as a Python dependency where needed)
- `make`, or [`just`](https://just.systems/) for RO-SimLab
- Docker only for experiments that use Elasticsearch, Oxigraph, or distributed services

Clone the repository, choose one project, and set it up from inside that directory. Do not create one shared virtual environment at the repository root: dependency and Python-version requirements intentionally differ.

```bash
git clone <repository-url>
cd robotics-experiments

# Example: run Musubi's offline quality gate
cd Musubi
make setup
make gen
make check
```

Useful first commands for every project:

| Project | Setup | Offline check or smoke test |
|---|---|---|
| Multi-World Search | `uv sync --extra dev` | `uv run pytest` |
| Musubi | `make setup && make gen` | `make check` |
| Physical Semantic Layer | `make setup` | `make doctor && make check` |
| RO-SimLab | `just sync` | `just check` |
| Tuned Past Action | `uv sync --extra dev` | `uv run pytest -q` |
| GTWM | `make setup` | `make doctor && make test` |

Most Make-based projects provide `make help`; RO-SimLab provides `just --list`. Otherwise, use the project README or operating guide to discover the supported workflows. Read those instructions before starting live-model, full experiment, or interactive viewer commands—some require API keys, Docker services, additional model downloads, or `mjpython`.

## Shared research themes

Although every project tests a different hypothesis, several ideas recur across the repository:

- **Simulation as an oracle:** MuJoCo supplies controlled physics and ground truth for quantitative evaluation.
- **Semantics between worlds:** ontologies and typed intermediate representations connect robot state with documents, plans, and business records.
- **Provenance and uncertainty:** observations and decisions should explain where they came from and how trustworthy they are.
- **Offline-first reproducibility:** seeded runs, local mocks, recorded model responses, and machine-scored criteria keep experiments repeatable.
- **Explicit boundaries:** real robot control, production scaling, and claims not supported by the experiment are kept out of scope.

## Working in this repository

Before changing a project, read its local documentation. Most projects use:

- `PROJECT.md` or a PoC plan for the research question, scope, and success criteria;
- `README.md` for setup and everyday entry points;
- `CLAUDE.md` for detailed development rules and architecture constraints;
- `SCENARIOS.md`, `docs/`, or `experiments/` for evaluation protocols;
- `runs/`, `results/`, or `scoreboard/` for generated evidence and reports.

Please keep changes scoped to one project unless the work is intentionally cross-project, preserve deterministic seeds and ground-truth isolation, and never commit secrets from `.env` files. Each project’s own quality gate is the source of truth for validation.

## License

This repository is available under the [MIT License](./LICENSE).
