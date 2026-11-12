# ADR-0022: The LLM live path is anthropic only, and calls it over HTTP without adding an SDK

- Date: 2026-11-08
- Status: Accepted (the decision on H-04. The user chose "anthropic only, direct HTTP")
- Layers touched: llm (provider), core (settings), services (learning-worker wiring)

## Context

`sbp.llm` has the Provider abstraction, structured output, prompt versioning, and replay, but **has no live provider implementation**
(nobody builds the `live_factory` to pass to `resolve_provider`). The M8-09 discovery loop produces proposals only when a replay fixture exists, and
production concept proposals, document extraction, and explanation verbalization cannot run. M8-10 was waiting on the H-04 decision (which provider to use).

Decision (2026-11-08): **anthropic only. Call it over HTTP without adding an SDK.** The reasons are that the default of `SBP_LLM_DEFAULT` is anthropic,
that confirming contract, cost, and data location can be narrowed to one vendor, and that dependencies do not increase (`httpx` is already a dependency).

## Options

### Question 1: SDK or HTTP

1. **Direct HTTP (`httpx`).** No added dependency, and it can be tested with `httpx.MockTransport` without a network. Keeping up with the API shape
   (Messages API) is on us, but the surface used is small (one structured-output call).
2. Make the official SDK an optional dependency. It brings types and retries, but adds management of dependencies, the lockfile, and the import-linter contract (the SDK is not imported from anywhere other than `sbp.llm`).
   Not worth it since the surface used is small.

### Question 2: How to obtain structured output

1. **Force it with tool use.** Set the expected JSON Schema as `tools[].input_schema` and force a single tool with `tool_choice`.
   The `input` of the response's `tool_use` block is the structured output. It breaks less than parsing free-text JSON.
2. Have it return JSON in free text and parse it. Adds handling of malformed output.

### Question 3: Retries

**Done only inside this provider** (CLAUDE.md: retries live in only two places, the eventlog consumer layer and `llm.Provider`).
Retry 429 and 5xx (and connection errors and timeouts) with capped exponential backoff. 4xx (other than 429) fails immediately,
because the input is bad and retrying will not fix it.

### Question 4: The key

`ANTHROPIC_API_KEY` is placed only in `.env` (invariant 12). Settings hold it as a `SecretStr`, and it **does not appear in logs, exceptions, fixtures, or provenance**.
If started in `record` / `live` without a key, it fails (it does not silently fall back to replay or another path).

## Decision

Question 1: direct HTTP. Question 2: tool use. Question 3: capped backoff inside the provider. Question 4: `SecretStr`, fail if absent.

- `sbp.llm.anthropic.AnthropicProvider` (`name = "anthropic"`). `embed` **fails, explicitly stating it is unsupported**
  (Anthropic has no embeddings API. `SBP_EMBEDDINGS` points to a different provider).
- `sbp.llm.build_provider(settings)`: `replay` uses fixtures only (no SDK or key needed). `record` / `live` wrap
  `AnthropicProvider` in `ReplayProvider(live=…)`. The behavior of not silently falling to live when a fixture is absent (M6-02) is maintained.
- The model ID is `ANTHROPIC_MODEL` in `.env` (not written in code). The `default` of routing (`routing.yaml`) is `SBP_LLM_DEFAULT`.
  When `SBP_LLM_DEFAULT` is other than anthropic, `record` / `live` **fail** (other vendors' live is unimplemented).

## Consequences

- **Invariant 11**: The uses of the LLM do not change (concept proposals, document extraction, explanation verbalization, NL->query). It is not called from the hot path.
- **Invariant 12**: The key is only in `.env`. It is a `SecretStr`, and that it is not written to logs is pinned by a test.
- **LLM usage policy**: Output is validated with pydantic before registration, provenance carries `provider / model / prompt_version(hash)`, `record` does not
  overwrite existing fixtures, and `replay` is the default.
- **Tests**: `httpx.MockTransport`. Success, retry, immediate failure, malformed output, the key not leaking, and replay not hitting the transport.
  A real LLM is not used in tests (no `llm_live`-marked test is created this time either).

## Open questions

- **Live for openai / gemini.** Unimplemented. If used, a separate ADR decides each one's structured-output method.
- **Live for embeddings.** The `SBP_EMBEDDINGS` provider (openai / gemini / local). The four uses in this repository do not use embeddings.
- **Cost cap.** There is a cap on the number of calls per cycle (M8-09), but no cap on the token budget.
- **Data location.** Which documents and discrepancy explanations are sent to the API (confirming that no personal data is sent) is an operational judgment. The policy of not putting
  entity IRIs in the prompt is kept in M8-09.
