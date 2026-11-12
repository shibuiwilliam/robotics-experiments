# ADR-0001: Make SBP an independent repository

- Date: 2026-10-03
- Status: Accepted
- Layers touched: infra (repository layout)

## Context

SBP existed as an untracked subdirectory of the parent directory `robotics-experiments/`. `CLAUDE.md` states: "This repository contains the platform core only. Use-case validation (real hardware and sandbox experiments) is done in a separate repository and must not be placed here." The parent repository is, by nature, a collection of use-case experiments. This is an open question from `docs/TASK.md` §7-1.

## Options

1. **Run `git init` in the SBP directory and make it an independent repository** — Benefits: matches the premise of CLAUDE.md (platform core only), and aligns the ontology version and the CI boundary of `make check` with the repository boundary. History is not mixed with experiment code. Drawbacks: from the parent repository's point of view, a nested untracked directory remains. Cost on a MacBook: none.
2. Track it as a subdirectory of the parent repository — Benefits: a single history. Drawbacks: the history of experiment code and the history of the platform get mixed, and the CI boundary for ontology semver becomes ambiguous. The discipline of keeping use-case validation out cannot be maintained mechanically.
3. Use a git submodule — Benefits: achieves both. Drawbacks: at this scale the operational cost outweighs the benefit (forgetting to update the submodule is a typical source of accidents).

## Decision

We adopt Option 1. The repository was made independent with `git init -b main` plus a `.gitignore` (`.env`-family files, Python caches, tool caches, local generated artifacts), and the existing set of documents became the initial commit.

`.gitignore` excludes `.env` and `.env.*` while making an exception with `!.env.example` (invariant 12: secrets live only in `.env`; `.env.example` must always be kept in sync and committed).

We accept that SBP remains as an untracked directory on the parent repository side (adjusting the parent's `.gitignore` is a concern of the parent repository and is outside the scope of this repository's decision).

## Consequences

- Impact on invariants: invariant 12 (secrets) is enforced mechanically via `.gitignore`. No other invariants are affected.
- Ontology version: no impact (`ontology/VERSION` has not been created). No re-grounding needed.
- Impact on testing and operations: the repository targeted by CI (`X-01`) is settled here. The unit of execution of `make check` equals the repository.

## Open questions

None.
