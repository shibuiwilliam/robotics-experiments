# IMPROVEMENT.md — Claude Code as the primary engine & interface

Design + plan for making **Claude Code the primary engine and interface** to experiment with the
combination of Robotics × AI-agent × Ontology on Musubi. Grounded in `PROJECT.md` (WHAT/WHY),
`CLAUDE.md` (HOW + Golden Rules), and the current codebase (P0–P11 platform + v2 scenario layer with
F1/S5/F2/C3 passing offline). Newest state at top.

## 0. Round 2 — honor the per-arm planner contract (Claude is *actually* the engine)

**Gap found (audit).** Prior round made Claude the registry-default provider and added the console,
but `bench/runner/drivers.py` hardcoded `ScriptedPlanner()` for **every** arm. So scenario runs never
invoked the LLM — Claude was the engine in name only, and the arm contract (SCENARIOS.md §5: A0/A1
`scripted`, A2–A4 `gemini`/`claude`) was unmet. The ablation ladder only varied envelopes/claims/
norms, not the *planner backend*.

**Fix (this round).**
- Extract a shared `agents/grounding.py::ground_relocate(goal, ctx, action_types)` — grounds a
  relocate *skeleton* (move-standoff → pick → move-zone → place) to correct params from belief. Both
  planners use it, so plans are identical and deterministic regardless of who authored the skeleton.
- `ScriptedPlanner` emits the canonical skeleton and grounds it.
- `LLMPlanner` (Claude/Gemini via `ChatAdapter`) asks the LLM for the *skeleton* (action-type
  sequence) and grounds params itself — so offline the `FakeGeminiClient` returns a static skeleton
  while coordinates stay runtime-correct. `ChatAdapter` gains a `.calls` counter.
- `drive_relocate` selects the planner **per arm** via `scenario.planner_for(arm)`: scripted for
  A0/A1; `LLMPlanner` over `ChatAdapter(FakeGeminiClient)` (provider from registry = claude) for
  A2–A4. It records `planner`, `provider`, and `llm_calls` in the RunContext; `api_calls` stays 0
  (the Fake is not a network call — offline determinism preserved).
- Console `inspect` surfaces `planner`/`provider`/`llm_calls`.

**Why it matters.** This makes Claude genuinely the reasoning engine for A2–A4 across the ablation
ladder (E0/flagships), honoring the SCENARIOS.md §5 arm contract, while `make check` stays green
offline (replay → 0 API calls; live Claude cassettes still pending keys).

## 0b. Findings from the full scenario run (2026-08-02, commit `5d98c26`) — see `REPORT.md`

Running all 5 scenarios × arms offline surfaced concrete data/observability gaps and one defect.
Logs in `logs/`; analysis in `REPORT.md`. **G2/G3/G5 are fixed (rev.2, `make check` green, 101
tests); rev.3 verified reproducibility and found G9 (seed inertness). G1/G4/G6/G7/G8/G9 remain
open.** Findings:

- **G1 — Claude engine exercised only by e0.** The per-arm planner contract now works, but only the
  `relocate` driver (e0_smoke) routes through the Episode/planner, so only e0's A2–A4 record
  `engine=llm:claude, llm_calls=1`. The flagship drivers (`confidence_audit`, `forensic`, `recall`,
  `redteam`) construct their result procedurally and never invoke a planner — so at A2–A4 they still
  show `engine=scripted/none`. **The "Claude is the primary engine" claim is validated on 1 of 5
  scenarios.** *Fix direction:* give at least one flagship an agentic planning step (or add an E7
  ablation that swaps planner backends on a scenario that uses one), so agent reasoning quality is
  actually measured on a business task.
- **G2 — s5 `success` predicate was silently always False (DEFECT). ✅ FIXED (rev.2).**
  `s5_ghost.yaml` declares `ground_truth: {planted_root_cause: null}`; `drive_forensic` used
  `setdefault`, which does not overwrite the existing `null` key, so
  `root_cause_identified('planted_root_cause')` returned False even though `forensic_accuracy=1.0`.
  *Fix applied:* driver now uses `if ctx.ground_truth.get("planted_root_cause") is None:` conditional
  assignment (grounds the truth even when the key is declared null). Regression test added
  (`test_flagships`: `assert all(r.success …)`). s5 success is now True on all arms.
- **G3 — Scoreboard was cumulative, not run-scoped. ✅ FIXED (rev.2).** `MetricsStore.ingest`
  appended rows with no run key, so per-arm `n`/rates grew across invocations (e0 showed `n=6` after
  two runs). *Fix applied:* added a deterministic monotonic `run_id` batch column (not wallclock —
  keeps NFR-DETERM); `arm_summaries` now scopes to each scenario's latest `run_id`. Test added
  (`test_bench::test_metrics_store_scopes_to_latest_run`).
- **G4 — No stochastic variation captured.** With the LLM faked, all seeds are identical, so the
  planned per-seed distribution / CI statistics (Experiment Plan §8) are absent. Real variance needs
  live cassettes; until then the `ci_low`/`over_repeats` machinery is exercised but never non-trivial.
- **G5 — f2_recall ladder was 2-point (A0, A4). ✅ FIXED (rev.2).** *Fix applied:* declared A0–A4 in
  `f2_recall.yaml`. The run now shows the gradient — A0/A1/A2 all leak 1 unapproved-irreversible
  disposal, and the **norm/reversibility gate at A3** is the rung that closes the gap (unappr→0),
  matching c3's defense ladder. Test extended to assert A0–A2 unsafe / A3–A4 safe.
- **G6 — `success` vs `oracle_passed` conflation in summaries (residual).** The concrete s5 case is
  resolved by G2. What remains: when a scenario has *no* `success` predicate, `_success` defaults to
  True, so the ladder shows a pass that was never actually evaluated. All 5 current scenarios define a
  predicate, so this never triggers today. *Fix direction:* render `success` as `n/a` when the
  predicate is absent, and keep "headline success" visually distinct from "oracle verdict".
- **G7 — Semantic-envelope bus observability only in e0.** Flagship drivers bypass the Episode bus
  (`bus_events=0` for f1/s5/f2/c3), so the envelope trace / accountability chain is only visible for
  relocate. Any observability claim about the bus rests on one scenario.
- **G8 — No token/latency/cost telemetry.** `llm_calls` counts calls but not tokens or wallclock;
  offline this is 0-cost, but the record path needs token accounting before any live Claude-vs-Gemini
  comparison is meaningful. *Fix direction:* have the VCR/ChatAdapter capture usage on live calls and
  surface it in RunContext.extras.
- **G9 — `repeats: 3` is inert; seeds change nothing (found rev.3).** Verified empirically
  (`logs/41_seed_determinism.txt`): world geometry is identical across seeds 0/1/2 (pallet_0 fixed at
  `(-1.2,1.2,0.06)`) and every per-(arm,seed) metric is bit-identical in all 5 scenarios. The base
  MJCF is fixed and no scenario's `invisible_hand` op samples seeded RNG, so the seed never reaches a
  metric. Consequence: the 3 repeats triple runtime + DB rows for **zero** statistical/coverage value;
  `over_repeats`/CI aggregate identical inputs (a superset of G4 — it's not just the faked LLM, the
  *sim* is seed-invariant too). Separately confirmed **run-to-run reproducibility**
  (`logs/40_reproducibility.txt`): two full-suite passes are bit-for-bit identical and this run's
  standard logs match the committed rev.2 byte-for-byte. *Fix direction:* either (a) make the
  Invisible Hand / world placement consume seeded RNG (jitter pallet poses, tag-degradation timing,
  look-alike position) so repeats sample a distribution, or (b) drop `repeats` to 1 and stop implying
  triplicate coverage until live LLM variance (G4) or seeded perturbations exist.

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
