# ADR-0008: The UI uses Vite + React (TypeScript), with no GraphQL client and no UI library

- Date: 2026-10-03
- Status: Accepted
- Layers touched: api (read-only) / infra (the `ui` profile of compose)

## Context

M7-04 builds four views (`docs/TASK.md` §3, design document §12.2).

| View | Role |
|---|---|
| Discrepancy queue | Juxtaposition of evidence (observation / record / plan); resolution input **becomes a learning signal** |
| Belief view | Contributions of the three sources and an as-of slider ("what we believed as of when") |
| Expectation board | State of Expectations (Active / Fulfilled / Violated / Undetermined) |
| Concept curation | Accepting or rejecting `ConceptProposal`s and recording rejection reasons |

This is the decision for open question 4 in `docs/TASK.md` §7, "UI technology selection (M7-04) — an ADR for the framework at the start of work".

There are three constraints.

1. **The UI is responsible only for reading and for "injecting human judgment".** No path to write beliefs directly is created in the UI
   (invariants 1 and 2. Writes go through the approval flow of `/commands/*`).
2. **It must be developable and buildable on a MacBook.** The whole of Compose stays within 6 GB (CLAUDE.md). The UI is an optional profile,
   but during development `npm run dev` is run locally.
3. **It must be verifiable.** CLAUDE.md's definition of done is "types pass, tests exist". If `node_modules` is
   huge, type checking and tests will no longer run in realistic time and **this definition can no longer be met**.

## Options

### 1. Vite + React + TypeScript, with no GraphQL client and no UI library (adopted)

- Benefits: two dependencies (`react`, `react-dom`). GraphQL is just a POST via `fetch`, so
  no client library is needed (4 queries, no mutations). Types are written by hand from the GraphQL schema rather than from
  `schemas/`, but **the count is small, so handwriting is traceable**.
- Drawbacks: caching, optimistic updates, and subscriptions must be written ourselves.
- Cost on a MacBook: `node_modules` about 60 MB; `tsc --noEmit` and `vitest` take a few seconds.

### 2. Vite + React + Apollo Client / urql + MUI, etc.

- Benefits: caching, normalization, and subscriptions are there from the start. The look comes together quickly as well.
- Drawbacks: `node_modules` grows to hundreds of MB, and type checking and tests become slow. **The cache fits poorly with
  bitemporality** — the same `subject` that differs only in `asOf` is a different fact, and
  a normalized cache assumes "the same ID is the same entity" and overwrites. In a bitemporal
  UI that assumption is wrong, and the work of cancelling it out through cache configuration eats the gain.
- Cost on a MacBook: initial install takes minutes; the dev server uses hundreds of MB of memory.

### 3. SvelteKit

- Benefits: small runtime, and less code to write than React.
- Drawbacks: this platform has no Svelte expertise, and `.svelte` does not fit into the Python-side lint / format
  tooling. **It becomes the third language** (Python / TypeScript / Svelte template syntax).
- Cost on a MacBook: about the same as Option 1.

### 4. Build no UI, and have people hit SPARQL / GraphQL directly

- Benefits: not building is the cheapest.
- Drawbacks: the center of the design, that **resolution input becomes a learning signal** (design document §8.3, §12.2), would not be implemented.
  Without a path to return the result of a human resolving a discrepancy to the platform, reconciliation becomes "file it and be done".

## Decision

Option 1. **We treat not adding dependencies as part of the design.**

There are three reasons.

1. A normalized cache brings no gain in a bitemporal UI (the drawback of Option 2). Every time the `asOf` slider is
   moved, **a different set of facts** is returned; it is not an update to the same entity.
   If a cache would be used with invalidation anyway, it is simpler not to include it in the first place.
2. There are 4 GraphQL calls and no mutations (no mutations are built in the API layer).
   A client library is heavy for something that 30 lines of POSTing to `fetch` covers.
3. The fewer the dependencies, the faster `tsc --noEmit` and `vitest` run, and the easier it is to meet the definition of done (types pass, tests exist)
   every time. **If checks are slow, people stop running them.**

State management is not included either. The four views share no state with one another, and each view holding its own query results suffices.
If sharing becomes desirable, we will show the need at that point and add it.

### Features the UI does not have (intentional)

- **Direct editing of beliefs.** Writes go through the approval flow of `/commands/*` (design document §9.2). Putting a
  "fix this belief to the correct value" button in the UI would bypass both append-only (invariant 2) and the approval flow.
- **An input field for the "correct value".** Resolving a discrepancy takes the form of choosing a **kind of resolution**
  (the record is wrong / the observation is wrong / both are right but the definitions differ / systematic bias), not "which is correct". The principle
  of managing beliefs rather than truth must show up in the shape of the UI.
- **Default display of hypothetical scenarios.** `hypothetical` is shown only when explicitly toggled. If shown mixed in,
  it would be read as "what has been decided" (invariant 3).

## Consequences

- **Invariants**: 1 and 2 (the UI does not write beliefs), 3 (`hypothetical` is not shown by default),
  9 (no feature to display video showing people is built; the UI handles only symbols and confidence),
  12 (the API endpoint is read from `SBP_API_URL` in `.env`, and no URL is written in the UI source).
- **Ontology version**: unchanged (the UI is on the read side). No re-grounding needed.
- **Tests**: `vitest` checks pure functions (evidence juxtaposition, computing the contributions of the three sources, as-of time conversion).
  DOM tests are kept to a minimum, and **we prioritize "human judgment returning correctly to the platform" over the correctness of the display**.
- **Operations**: start with `make up --profile ui`. Build artifacts are static files, served from an origin separate from the API.

## Open questions

- Implementation of authentication. ABAC (M7-06) has been built up to the point of receiving a requester, but **how the UI authenticates has not been decided**.
  The current UI sends a fixed requester for development. It will not be made public until the production authentication method is decided.
- Reflecting subscriptions (`/stream/{topic}`) in the UI. Currently it polls, and the load when left open for a long time cannot be predicted.
