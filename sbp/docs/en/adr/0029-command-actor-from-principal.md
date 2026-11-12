# ADR-0029: The "who" of a command is determined from the authenticated requester (H-25)

- Date: 2026-11-08
- Status: Accepted
- Layers touched: api (app, commands), apps/ui (header)

## Context

`/commands/*` did not resolve the requester and accepted `resolved_by` / `decided_by` / `approved_by` / `answered_by` as self-declared in the body (review of M8-29, H-25).
A human answer enters belief as `observed` with confidence 0.98, and `answered_by` is also the unit of audit isolation, so injection under someone else's name is the most damaging.

## Decision

1. **If the requester cannot be resolved, the command is not accepted** (401). Even in a configuration with no `principal_resolver`, commands are not accepted (deny by default, same as ABAC).
2. **An agent cannot issue a human judgment** (403). Resolution, accept/reject, approval, and answers are human judgments, and agent output is limited to `inferred` (invariant 3).
3. **The subject fields (`IDENTITY_FIELDS`) are filled with the requester.** If absent from the body they are inserted; if present, they must match the requester (a mismatch is 403 and never reaches the queue).
   The current contract, in which the UI also puts its own principal in the body, is preserved (the contract tests are unchanged).
4. The UI's development build sends `VITE_SBP_DEV_PRINCIPAL` in `X-SBP-Principal`. Once the production authentication method (H-02) is decided, only the resolver needs to be swapped, and the command side does not change.

## Consequences

- The place where a command's "who" is held has moved from the body's self-declaration to the **authentication layer (`principal_resolver`)**. Once the resolver is configured to return real authentication,
  no one can answer, approve, or resolve under another person's name, and `decided_by` becomes the authenticated principal. **The current resolver is for development only (the `X-SBP-Principal` header),
  and claiming the header is enough to pass. The guarantees actually added are that "the body and the header match" and that "a configuration with no resolver does not accept commands"; spoofing cannot be prevented until the production authentication method (H-02) is
  decided.** The same holds for the agent 403: the development resolver always makes `is_agent` false, so this is enforced in real operation only
  once a real resolver is in place.
- An unknown command name does not know its subject fields, so it is rejected with 409 (a unit test checks that `IDENTITY_FIELDS` and `_to_row` agree). Invalid JSON is 400.
- When `SBP_ENV` is anything other than `dev`, no resolver is passed (`factory.py`), so all commands become 401. This is intended behavior until production authentication is added.
- The integration test goes through a path that calls the handler (`build_command_handler`) directly, and HTTP authentication is covered by unit tests. No production code calls the handler directly.
- The UI takes the principal from a single place, `devPrincipal()`, and uses the same value for the headers (both GraphQL and commands) and the body's `*_by`.

## Limitations (still open)

- **There is no authorization (who may decide what).** If the requester can be resolved, all 4 commands pass. Per-answerer authority and confidence are an open question of ADR-0018, and remain in H-25.
- The development resolver is not authentication (`SBP_ENV=dev` only). Production authentication is H-02. `VITE_SBP_DEV_PRINCIPAL` is embedded in the build, so do not use it for production builds.
- **The idempotency key `decision_id` does not include the principal.** If a different principal sends the same `decision_id` first, the rightful person's later judgment is discarded as a duplicate and `accepted: true` is returned
  (a pre-existing property; it has become more conspicuous now that the principal is bound). Whether to include the principal in the key or to make a collision a 409 is an open question.
