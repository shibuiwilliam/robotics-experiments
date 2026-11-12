# ADR-0028: Secrets live only in `.env`. All three real keys in the development machine's `.envrc` are rotated (H-01)

- Date: 2026-11-08
- Status: Accepted (rotation is an operation on the provider side, performed by a human; awaiting confirmation of completion)
- Layers touched: none (operations and documentation)

## Context

The development machine's `.envrc` (direnv) contained API keys for three LLM providers in plaintext. They were never committed to git (`.gitignore`d), but
because the file was displayed in the 2026-10-04 session, the values passed through the session's context. Invariant 12 is "secrets only in `.env`".

## Decision

1. **Rotate all three keys** (invalidate the old keys and issue new ones). Even "merely passed through" is treated as a leak. The effort of reissuing is smaller than the cost of tracking usage history.
2. **Keys are kept only in `.env`.** Neither keys nor settings are placed in `.envrc`. Since X-11 made nested settings also read `.env`, moving them causes no problem.
3. The deny list in `.claude/settings.json` includes reads of `.env`, `.env.local`, `.envrc`, and `.envrc.*`, so they cannot be read from a session. In the rotation procedure, do not paste new keys into the conversation.
4. The procedure is the rotation runbook (kept locally, not published). Once executed, remove H-01 from `docs/HITL.md` (done by a human).

## Consequences

- `make scan-secrets` (invariant 12) scans tracked files for keys. `.env.example` holds the key entries empty.
- Limitation: confirmation that the old keys were invalidated can only be made on the provider side. The platform cannot confirm it.
