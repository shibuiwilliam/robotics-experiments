# HITL.md — Register of items awaiting a human decision

> English translation of the Japanese original (snapshot of 2026-11-12). The Japanese file is canonical; numbers may lag behind it.

- Date: 2026-10-04 (first version)
- Last updated: 2026-11-08 (M10 carried out (3) and (4) of H-27. Decided H-02 / H-03. ADR-0033. Added H-26. Decided H-04 in ADR-0022 and H-22 with option A. H-25 already added. H-01 / H-17 remain unaddressed)
- Previous: 2026-10-17 (inventory. Added H-22 / H-23)
- Scope: items in SBP development and operation that **cannot proceed / cannot be settled unless a human decides**
- Role: **a queue of judgements**. Task state is in `docs/TASK.md`, milestone state in `docs/STATUS.md`,
  facts found in `docs/PROGRESS.md`, records of decisions in `docs/adr/`. This document holds only **what is not yet decided**.
  Once decided, file an ADR, move the item in this document to "decided", and remove it at the next inventory.

## Criteria for inclusion in this document

| Include | Do not include |
|---|---|
| Choices about business specifications and operating policy (the platform has no basis) | Implementation details (settled by reading the code) |
| Contracts with the outside (authentication method, provider selection, legal compliance) | Technical judgements that can be verified by tests |
| Irreversible operations or those affecting the outside (key rotation, handling of real data) | Matters already decided in an ADR |
| Vocabulary decisions involving an ontology MAJOR / MINOR | Refactoring |

**Do not fill gaps by guessing** (CLAUDE.md). If the platform side decides on its own, it later becomes "a setting no one can explain the reason for"
(`docs/PROGRESS.md` finding 88).

## Legend of states

- `待ち` (waiting) — awaiting a decision
- `暫定` (provisional) — proceeding with a default, but a decision may change it (the code has a default and it runs)
- `期限到来` (overdue) — the point at which it was decided to "re-evaluate" has passed
- `決定済み` (decided) — ADR filed. Remove from this document at the next inventory

---

## 1. Priority high — cannot proceed unless decided, or dangerous to leave

### H-01 A real key is placed in plaintext in the development machine's `.envrc` `決定済み・実行待ち` (decided, awaiting execution)

**Decided 2026-11-08: option A (rotate all 3; keys only in `.env`). ADR-0028; the procedure is the rotation runbook (kept locally, not published). Rotation is an operation on the provider side, performed by a human. Remove this item once completed.**

**Fact.** The API keys of 3 LLM providers are in plaintext in `.envrc` (direnv). It is excluded by line 6 of `.gitignore`
and does not appear in `git ls-files` either (**it has never been committed**). However,
**because the file was `cat`ed in the 2026-10-04 session, the values passed through the session's context.**

**Why a human decision.** Revoking and reissuing keys is an account operation on the provider side that the platform cannot perform.
Also, whether "having merely passed through" counts as a leak is a question of operating policy.

**Options.**

| Option | Content | Pros / cons |
|---|---|---|
| A (recommended) | Rotate all 3 | Safest. The only cost is the effort of reissuing |
| B | Rotate only those with a record of use | Less effort / requires tracking which were used |
| C | Do nothing | **Not recommended.** It must be stated explicitly that the risk was accepted |

**Related decision.** Whether to move the place for keys from `.envrc` (direnv, expanded into the shell) to `.env`
(read by `Settings` with the `SBP_` prefix). Invariant 12 states "secrets only in `.env`", and
**placing keys in `.envrc` deviates from the convention**. Since `.envrc` has been added to the deny list in `.claude/settings.json`,
it can no longer be read from a session.

**What to do after the decision.** File an ADR (policy on where keys live). Confirm that `.env.example` has entries for the keys.
Add a rotation procedure to `docs/runbooks/`.

**As of 2026-10-17.** Not addressed. The modification time of `.envrc` is still 2026-10-03, and 13 days have passed
since it was recorded as "overdue". Today's session did not read the contents of `.envrc` because of the deny setting (only its existence and
modification time were confirmed via a directory listing). It must be decided before H-23 (push to a remote).

**As of 2026-10-25.** Not addressed (the modification time of `.envrc` is still 2026-10-03; 21 days since "overdue"). The
contents were not read today either. **One fact affecting the related decision was found**: nested settings (`SBP_BRIDGE_*` /
`SBP_REASONING_*` / `SBP_WM_*` / `SBP_ACTIONS_*`) do not read `.env` and take effect only from environment variables
(`docs/PROGRESS.md` finding 108, `docs/TASK.md` X-11). `.envrc` presumably worked as the place for settings on the development machine
because direnv exports them as environment variables, so **if keys and settings are moved to `.env`, X-11 must be fixed first**.

---

### H-02 Authentication method for the UI `決定済み` (2026-11-08. (c) stay localhost-only and do not put into operation. ADR-0033)

**Decided 2026-11-08**: There is no plan to expose the UI externally. Before exposing it, the authentication method (OIDC + PKCE as the first candidate) will be decided in an ADR. Until then, everything other than `SBP_ENV=dev` stays fully denied. The spoofing in H-25 (development header) is tolerated only under this premise. Remove this item at the next inventory.

What follows is the text from before the decision.

**What is blocked.** `apps/ui` sends a fixed requester (the `X-SBP-Principal` header) for development.
ABAC (M7-06) is implemented up to the point of accepting a requester, and the default is deny. **The UI cannot be
published until the production authentication method is decided.**

**Why a human decision.** It depends on the organization's identity infrastructure (OIDC provider, existing internal authentication, per-site operations),
for which the platform has no basis.

**Options.** (a) OIDC / OAuth2 Authorization Code + PKCE (no new service; the IdP is external),
(b) authenticate at a reverse proxy and have the platform trust the header (one more proxy in Compose → ADR required),
(c) do not decide during development, keep the UI localhost-only and do not put it into operation.

**Related.** Open in ADR-0008. `sbp.api.factory.dev_principal_resolver` passes no resolver when `SBP_ENV` is anything other than `dev`
(= deny everything), so it is built so that **nothing leaks even if accidentally published**.

---

### H-22 Whether the test may place worker inputs directly in the integration test for gate G8 `決定済み` (2026-11-08. Option A)

**Fact.** The condition for G8 is "run the 4 workers (bridge / belief / reconcile / scheduler) on real services, and
have each `step()` return non-zero". The 2026-10-17 inventory found that **there is no code to write the head of each data path in
production** (`docs/PROGRESS.md` findings 101–103) — writing to the latent index, loading the probe bundle,
syncing the asset ledger, connector → mapping → `integration.business`, plan ingestion, expectation verification. In every case the test
places them. **If the test may place the inputs, G8 can be written with M8-11 alone, with the current implementation.**

**Why a human decision.** "What counts as M8 being complete" is a matter of plan scope, and `docs/TASK.md` §2 requires an ADR
for scope changes. Whether to follow the letter of the gate or its purpose (running in operation) cannot be decided for the platform's
convenience.

**Options.**

| Option | Content | Pros / cons |
|---|---|---|
| A (recommended) | **The test simulates only the outside world, and the platform's inner stages are driven through operational code.** Outside world = the window of physical observation (`SynthWorld`), business systems (SQLite mock), human operations (API commands). Latent encoding, loading the probe bundle, ingestion, and asset ledger sync are done by production code | G8 means "runs in operation". Consistent with invariant 10 too (`services` / `scripts` cannot import `sbp.testing`, so simulation of the outside world stays in the test process) / M8-23, M8-24, M8-25 become prerequisites of M8-11, and M8 gets longer. If M8-24 is decided in an ADR to be "the edge (outside this repository)", only the contract at the entrance of the latent is needed |
| B | Per the current letter, the test may place inputs directly | M8-11 can start immediately / **repeats G5's "the one who ran it through was the test code" (finding 89)**. New tasks remain in M8 but are not prerequisites of G8 |
| C | Pass G8 with B, and split M8-23 to M8-29 out as M9 | The milestone boundary comes earlier / moving tasks is a scope change so an ADR is needed. A separate gate must be defined for M9 |

**What to do after the decision.** File an ADR (definition of the gate). Update the definition of G8 and the M8-11 row in `docs/TASK.md`.
For C, create the M9 section.

**As of 2026-10-25.** One more input to the judgement. Of the premises of option A, **the recording path (M8-25) went into operation** —
the ingest-worker reads the business system (in development a file-based SQLite mock) and writes the ledger, business records, and plans with production
code. The integration test `test_ingest_worker_e2e.py` was written in the form of option A (only the outside world is placed by the test). What
remains of option A's premises is the head of the inference path (the ADR for M8-24, and loading the probe bundle in M8-23).

**Decision of 2026-11-08**: Option A (simulate only the outside world, drive the platform's inside through operational code). M8-24 (head of the inference path) and M8-23 (loading the
probe bundle) were made prerequisites of M8-11. The definition of G8 is written in the ADR for M8-11.

---

### H-03 Fate of `/stream/{topic}` `決定済み` (2026-11-08. (a) removed. ADR-0033)

**Decided 2026-11-08**: No external client is planned to subscribe, so the endpoint and `stream_reader` were removed. When needed, decide the method and authorization and re-file. Remove this item at the next inventory.

What follows is the text from before the decision.

**Fact.** With M8-18 / M8-19, all 4 UI views now read GraphQL resources, and
**there is not a single stream subscriber left** (fixed by the contract test `test_the_ui_reads_no_stream_at_all`).
The endpoint on the API side remains, but `build_app` passes `stream_reader=None`, so it returns 503.

**Why a human decision.** Whether to "keep it but not use it" or "delete it" depends on plans to use the platform from outside
(whether clients other than the UI are expected to subscribe).

**Options.** (a) delete (an unused endpoint gets read as "it should work"), (b) keep it and decide the subscription method
(SSE / WebSocket), (c) keep it and state "not wired" in the response body (current).

**Related.** Open in ADR-0008 (reflecting subscriptions in the UI).

---

### H-04 Provider used for the LLM live path `決定済み` (2026-11-08. ADR-0022: anthropic only, direct HTTP)

**What is blocked.** M8-10 (the LLM live path) is a task of "adding one SDK as an optional dependency", but
**which one to add has not been decided**. `routing.yaml` holds per-task routing, and by design model IDs are written only in
`.env`, so all that the code decides is "which SDK goes into the optional dependencies".

**Why a human decision.** Contracts, cost, and where data lives (which provider documents are sent to) are operating policy.

**Options.** (a) `anthropic` only (the default of `SBP_LLM_DEFAULT`), (b) all 3, (c) do not build live and stay with
replay (concept proposals and document extraction are written by humans instead).

**Note.** The behavior of not silently falling back to live when a fixture is missing (M6-02) is **maintained**. It is linked with H-01
(using live requires a key).

**Decision of 2026-11-08**: (a) anthropic only. No SDK is added; the Messages API is called with `httpx` (ADR-0022). Live for openai / gemini is
not implemented. H-01 (key rotation) remains unaddressed — using live requires a key.

---

### H-05 Handling of legal erasure requests `待ち`

**2026-11-08**: A proposed policy was written in ADR-0034 (status: proposed). Hold subjects as pseudonymous IRIs, and satisfy erasure by deleting a row of the mapping table (statements are not deleted). **Awaiting legal review** — whether pseudonymization can be regarded as satisfying an erasure request, handling of re-identification, retention period. The ADR contains the result of checking in code where human subject IRIs are placed (event log, graph provenance, `learning_decisions`).

**What is blocked.** Reconciling invariant 2 (append-only) with "erasure requests for personal data". The rule of `sbp.knowledge`
says "a separate ADR". **It must be decided before operation begins.**

**Why a human decision.** It is a legal judgement and cannot be settled by platform design alone.

**Points at issue.** (a) Can the design hold no personal data at all (invariant 9 means raw video is not held, and
personal identification of a `Person` is prohibited at the type level in probes. What remains are the subject IRIs of `decided_by` / `resolved_by`),
(b) if erasure is needed, "delete the statements" or "replace the subject with a pseudonym", (c) the extent to which as-of reconstruction breaks.

**Current state on the platform side.** IRIs pointing to people enter the work item tables (`learning_decisions.decided_by`,
`discrepancies`' `assignee_role`) and the provenance of assertions. **Places that hold roles
do not point to individuals** (the design of ADR-0008).

---

## 2. Priority medium — progress is possible without deciding, but deciding firms up the design

### H-06 Whether to persist the rate limit of actions `暫定`

For now `RateLimitState` is inside the worker process and **disappears on restart**. The idempotency key
(derived from `decision_id`, finding 93) prevents "applying the same decision twice", but "two different decisions in a short time" gets through
after a restart. Design document §9.3 states "limit repeated actions on the same target in a short time".

**Options.** (a) count `created_at` of `expectations` (no new table), (b) a dedicated table,
(c) status quo (judge that harm is small if restarts are rare). **Related**: open item 1 of ADR-0014.

### H-07 Whether to persist `ActionEvent` `暫定`

The record of execution is in neither the event log nor a table; `Expectation.action_event` only holds an ID.
To persist it, one must answer "**is an execution a claim?**" — if the fact of having executed is read as an observable fact,
there is a seat for `observed`, but an `EventEnvelope` carries claims, and does a record of execution count as one?
**Related**: open item 2 of ADR-0014.

### H-08 Entrance for compensation `待ち`

Compensating actions are mandatory in the contract (an undefined ActionType is rejected at registration), but **there is no entrance to execute them**.
Running them automatically is dangerous (the compensation itself changes the business system), so it has not been built.
**Options**: (a) go through the decision queue as with execution (a human approves), (b) raise a proposal from an expectation violation and a human approves,
(c) do not build for now. **Related**: open item 3 of ADR-0014.

**Note of 2026-11-08**: The UI's action approval (M8-22, ADR-0021) does not create an entrance to execute compensation. It only
shows the compensation target in the contract summary. Whether to create the entrance awaits the decision on this item.

### H-09 Whether to track "was it filed" for adopted concept proposals `暫定`

The platform does not know whether a proposal that became `ACCEPTED` actually entered `ontology/` (filing a MINOR is manual work by humans;
decision of M6-04). Tracking would require a state of "adopted but not implemented".
**Related**: open item 1 of ADR-0015.

### H-10 Whether to suppress re-proposals of rejected proposals `暫定` (provisional decision in M8-09. ADR-0019)

The reason is kept in `curation_note`, but there is no mechanism to stop the next discovery cycle from producing the same proposal from the same cluster.
Introducing M8-09 (periodic execution of the discovery loop) makes the repetition actually happen.
**Related**: open item 2 of ADR-0015.

**Provisional decision of 2026-11-07** (ADR-0019): suppress by the signal fingerprint (trigger, set of entities, type) before calling the LLM.
`proposed` / `accepted` / `revised` always, `rejected` for a cooldown of 30 days (`SBP_REASONING_DISCOVERY_REJECTION_COOLDOWN_DAYS`).
If the same signal appears after the cooldown, re-propose. **To change this, change the cooldown days or the definition of the fingerprint.**

### H-11 Default of the entity types to ground `暫定`

The default of `SBP_BRIDGE_GROUND_TYPES` is `vocab.core.Asset`. Whether to include carriers, jigs, and workers
is a domain judgement. **Related**: open item in ADR-0009.

### H-12 Whether to narrow by site with a predicate (ontology MINOR) `暫定`

For now sites are narrowed by an IRI convention (`.../site/<site>/...`). **The convention is weak grounds**; it would be more correct to place a `sbp:atSite`
predicate and narrow with `?entity sbp:atSite <site>`. However, since this is an ontology MINOR change,
the change procedure (CLAUDE.md) must be run first. The count is logged so that a 0 count can be noticed.
**Related**: open item in ADR-0009.

### H-13 Judging that an observation request has been "satisfied" `待ち`

The processing that, when an `observed` event comes back, matches which request it satisfied is not implemented
(the `mark_satisfied` API exists but nothing calls it). The straightforward approach is to judge by entity × fluent × interval overlap, but "**how much
must be measured to be satisfied**" differs by observation means.
**Related**: open item in ADR-0011.

**Note of 2026-11-07**: Only for human answers (M8-29, ADR-0018), the answer names the request's key `(discrepancy_id, fluent)`,
so it was decided to satisfy by "key match". Satisfaction when a sensor observation comes back remains this open question.

### H-14 Whether to allow multiple observation means per fluent `暫定`

For now, 1 fluent has 1 means (`observation/*.yaml`). Choosing among several (trying the cheapest first, etc.) would mean adding a
means dimension to the scheduler's selection, and needs a separate design. **Related**: open item in ADR-0011.

### H-15 Forwarder from site to center `待ち`

Discrepancy events go to the site topic by default (ADR-0010). Seeing them across sites at the center requires a forwarder, but it is
not implemented. It depends on **when multi-site operation starts**.

### H-16 Cleanup of terminal-state work items `暫定`

The practice of dropping rows that became `Learned` / `Suppressed` / `rejected` (partitioning, archiving) is undecided.
The row count is proportional to the number of unresolved items, so it is not a problem for now. **Related**: open item in ADR-0013.

### H-23 Where to run the remote repository and CI `待ち`

**Fact.** `git remote` is empty, and `.github/workflows/ci.yml` (4 jobs: check / integration / audit / ui) has
**never been run** (confirmed 2026-10-17). X-01 is `[x]`, but only to the extent of "the CI definition was written"; it has not been observed to go green on a GitHub
runner (amd64, `SBP_PLATFORM=linux/amd64`). Also, the 20 commits of M8 are
on `feat/bridge-probe-serialization` and are not in main (`docs/TASK.md` X-10).

**Why a human decision.** Creating a remote and pushing involves publication to the outside (private or public, which organization).
It is also linked to the handling of keys (H-01).

**Options.** (a) create a private repository, push, and actually run CI (recommended. Settle H-01 before pushing),
(b) do not create a remote, and provide a way to reproduce CI locally, (c) for now operate only on local `make check` /
`make test-int`, and state X-01 as "not executed".

**What to do after the decision.** File an ADR (where CI runs. It may be combined with the fact that the "CI platform" row in §4 remains without an ADR). Finish
X-10 (merging the branch) before pushing.

**As of 2026-10-25.** No change (no remote, 20 commits ahead of main). In addition, the 2026-10-17 and 10-25
inventories are uncommitted (finding 107).

### H-26 Beliefs saturate on history replay / late-arriving events are skipped `決定済み`

(b) The cause was confirmed to be the accumulation of repeated claims from the same information source, and it was fixed by replacement (ADR-0027). (a) Skipping is 0 on the real chain.
Reordering within a window will be decided once `OUT_OF_ORDER` is observed in operation. Remove this item at the next inventory.

### H-25 Authentication of the command API, and identity of the answerer `一部決定済み` (partially decided)

**2026-11-08**: The place for "who" was moved from self-declaration in the body to the authentication layer (`principal_resolver`) (ADR-0029. A mismatch between body and requester gives 403; if it cannot be resolved, 401). **However, the resolver is only a development header, and spoofing cannot be prevented until the production authentication method is decided.** The text below is the fact from before the move; **remaining**: authorization of commands, the fact that `decision_id` does not include the subject (who may judge what) and per-answerer confidence (ADR-0018), the production authentication method (H-02).

`/commands/*` (`resolve_discrepancy` / `curate_concept` / `execute_action` / `answer_observation` / `review_quarantine`) pass through neither
`principal_resolver` nor ABAC, and `resolved_by` / `decided_by` / `approved_by` / `answered_by` are
**freely claimed by the caller**. The independent review of M8-29 pointed this out (2026-11-07). A human answer
enters the belief as an `observed` claim with confidence 0.98, and `answered_by` is also the unit of audit quarantine, so
the impact is larger than for other commands (one can inject "it is running" under another person's name). Until H-02 (UI authentication method)
is decided, the premise is not to publish beyond development and verification environments.

**Addendum of 2026-11-08** (review of M8-22): The UI's action approval button can now reach `execute_action`, so the impact of
`approved_by` (the provenance of approval) being self-declared has grown. Reading `decisions` also only checks for the presence of a requester
(separation of sites is maintained). Decide authentication and authorization of commands and decisions together.

**Addendum of 2026-11-08 (confirmed in code)**: `decision_id` is an idempotency key assigned by the client, and the table's primary key is `decision_id` alone (`ON CONFLICT (decision_id) DO NOTHING`). As a result (1) if another subject sends the same `decision_id` first, the genuine decision arriving later is silently dropped, (2) the handler returns `accepted: true` without waiting for the enqueue result, so the drop is not conveyed to the caller. This is tolerated while localhost-only (ADR-0033), but before publication either namespace the key by subject, or wait for the enqueue result and return 409.

**To decide.** The method of tying commands to an authenticated subject (the straightforward way is to derive `answered_by` etc. from the request's subject
and not accept self-declaration in the body), and per-answerer authority and confidence (open in ADR-0018). **Related**: H-02, ADR-0012, ADR-0014, ADR-0018.

### H-24 In which milestone to handle the operational gaps outside the purpose of M8 `決定済み`

Option A was adopted (2026-11-08, ADR-0030). Create M9 "entrances to operation" and add the entrances for executing re-grounding, forecasting, and queries. The rest is H-27. Remove this item at the next inventory.

### H-27 Operational gaps not included in M9 and M10 `待ち`

**Fact.** Of what ADR-0030 did not include in M9, (3) the operational route of SHACL quarantine and (4) the What-if entrance were **added in M10** (ADR-0035, 0036. 2026-11-08). The remainder:
(1) **operation of identity binding** (`identity_bind_rate` / `identity_revoke_rate`) — the perception contract that feeds Track does not exist in this repository, and depends on the output of the field edge,
(2) **entrances for LLM-based features** (concept proposals, document extraction, NL→query, explanation text generation) — operational code does not import `sbp.llm`.

**Limits of M10 (inputs for the next decision).**
- Quarantine stacks "rows that failed mapping and ledger rows whose type is not in the ontology". **The SHACL shapes (`AssetShape` and others) have not been applied to operational input** — `AssetShape` makes `locatedIn` mandatory, but
  the ledger has no location column, so applying it would make every row a violation (open in ADR-0035: give the ledger a location column or loosen the shape. An ontology decision, to be settled together with the operational route for spatial binding).
- What-if answers **only for a WM trained with interventions** (the operational WM rejects until the field edge places `actions` in the spool). For plan-diff What-if, conditioning has not been trained, so
  and counterfactuals have no entrance because `ActionEvent` is not persisted (H-07). There is also no entrance for `promote_scenario` (adoption needs a separate command design).

**Why a human decision.** (1) is a contract on the perception side (a separate repository), (2) is a policy on cost and provenance and a matter of priority.

**Options.** For each, "split out to M11 / do not handle for now". For (1), not handling it until the perception contract is decided is reasonable.

---

## 3. Priority low — decide when the deadline or condition arrives

### H-17 Re-evaluation of SeaweedFS / Oxigraph `期限到来` (overdue)

- **ADR-0003**: Whether to go back to MinIO if its anonymous download route recovers. It was decided that "re-evaluation is done in M7", and
  **M7 has passed**. The only benefit of going back is agreement with the original text of CLAUDE.md, and as long as the S3-compatible abstraction is maintained
  there is no practical benefit (= status quo seems reasonable, but **the judgement has not been stated**).
- **ADR-0004**: Return to a normal healthcheck once the Oxigraph image includes a subcommand for liveness checking.
  Likewise it was decided to re-evaluate in M7.

**What to do.** Either may stay "status quo", but **leave a record that it was re-evaluated** (append to the ADR, or
mark as decided in this document). If a deadline is set and then left alone, the next reader will mistakenly think "still under consideration".

**As of 2026-10-17.** No record yet. Compose still uses `chrislusf/seaweedfs:3.80` and `oxigraph/oxigraph:0.5.11`
(no healthcheck has been added to Oxigraph).

**As of 2026-10-25.** No change (no record, same images). It was confirmed on 2026-10-25 that the 187 integration tests are green with these two versions.

### H-18 Arrangement for re-measurement on real data `待ち`

The 5 items that synthetic data cannot confirm (probe performance, non-triviality of concept discovery, strength of latent reconstruction attacks,
precision of reconciliation, time needed for re-grounding) **cannot be resolved in this repository** (ADR-0001 split use-case validation off
into a separate repository).

**To decide.** When, by whom, and with what data to set up the separate repository. This repository's role is up to "handing over in a measurable form", and
**that is done** (enumeration of metrics, the `metrics` table, dashboard generation, a skeleton of attack tests with positive controls).

### H-19 Adding a second domain `待ち`

ADR-0002 chose manufacturing and equipment maintenance as the initial domain and says "the timing of adding a second domain will be judged in a separate ADR after M6".
**M6 has passed**, but the balance between the motive for adding (confirming the generality of the ontology) and the cost (building a full set of mappings,
reliability models, and probes) is a human judgement.

### H-20 Attribution for nonlinear probes `待ち`

`WorldModel.attribute()` assumes a linear functional. When nonlinear probes become necessary (the rule is to use them
"after logging that the latent is not linearly separable"), decide whether to extend it to accept gradient-based attribution or to
make it a separate method. **Related**: open item in ADR-0007. **It has not become necessary yet.**

### H-21 Shorthand notation of RDF 1.2 `待ち`

Whether to use `<< s p o ~ ?r >>` (sugar that binds the reifier at the same time). Support in pyoxigraph has not been checked.
It is not urgent since writing explicit `rdf:reifies` suffices. **Related**: open item in ADR-0005.

---

## 4. Already decided (remove from this document at the next inventory)

Items that remained in `docs/TASK.md` §7 but had in fact been decided.

| Item | Decision | Record |
|---|---|---|
| Independence of the repository | Make it an independent repository | ADR-0001 |
| Initial domain | Manufacturing and equipment maintenance | ADR-0002 |
| CI platform | GitHub Actions (4 jobs: `check` / `integration` / `audit` / `ui`. `audit` was added in X-05) | X-01 complete. `.github/workflows/ci.yml`. **There is no ADR**, so decide whether to file one. **There is no remote and it has never been run**, so the place of execution is H-23 |
| Technology selection for the UI | Vite + React, no GraphQL client or UI library | ADR-0008 |
| Destination of discrepancy events | Default is the site topic, switch with `SBP_REASONING_DISCREPANCY_SCOPE=central` | ADR-0010 |
| Where observation requests live | The work queue (cannot be put in the event log) | ADR-0011 |
| Who applies learning signals | The 5th worker `learning-worker`. Human approval is a prerequisite | ADR-0012 |
| Where to read discrepancies, expectations, concept proposals | Work item tables + GraphQL resources | ADR-0013 / M8-18 / ADR-0015 |
| Entrance for action execution | The decision queue (`kind='action'`). Approval is a decision, and execution is its application | ADR-0014 |

---

## 5. Operating procedure

**Cycle of inventories**: when a gate passes, and when touching `docs/TASK.md` §7.

**Flow for deciding one item** (corresponds to P-25 in `docs/PROMPT.md`):

1. Read the relevant item in this document and confirm that the options and recommendation are sufficient (if not, build inputs for the judgement with a small spike).
2. A human decides.
3. **File an ADR** (in the format of P-21). Reflect the affected tasks in `docs/TASK.md`.
4. Move the item in this document to §4, and remove it at the next inventory.

**When adding an item to this document**: if during implementation it turns out that "the business specification is unknown" or "a contract with the outside is needed",
**add it to this document without filling the gap by guessing**. Put `TODO(question)` in the code, and also write it in the ADR's "open items"
(writing the same thing in 3 places is redundant, but the priority is that **a person arriving from any entrance can notice it**).

**Relation to the ADRs' "open items"**: ADRs are records of decisions, so open items are **left as they are** (they should
read as history). This document is a rearrangement of them across ADRs into "whose judgement are we waiting for now". Therefore,
even if an ADR's open item has been resolved, the ADR is not rewritten, and the current state is shown by moving the item to §4 of this document.
