# ADR-0033: Remove `/stream/{topic}` (H-03). The UI stays localhost-only and is not put into operation (H-02)

- Date: 2026-11-08
- Status: Accepted
- Layers touched: api

## Context

- H-03: With M8-18 / M8-19, all 4 UI views now read GraphQL resources, and `/stream/{topic}` has no subscribers. `build_app` passes `stream_reader=None`, so it always returned 503.
- H-02: the UI authentication method (OIDC, proxy). The human's answer (2026-11-08): **there is no plan to expose the UI externally. There is also no plan for external clients to subscribe to `/stream`.**

## Decision

1. **Remove `/stream/{topic}`** (the `stream_reader` argument of `create_app`, the endpoint, and related tests). An unused endpoint that returns 503 gets read as "it should work".
   When it becomes necessary, decide the subscription method (SSE / WebSocket) and authorization, and rebuild it (the event log remains, so nothing is lost). The "stream subscription" row of the table in design document §10.1 is treated as unimplemented / withdrawn as of this ADR.
2. **The UI authentication method is not decided (H-02 (c)).** The UI and API are operated localhost-only. Precondition: before exposing externally, decide the authentication method (OIDC + PKCE as the first candidate) in an ADR.
   When `SBP_ENV` is anything other than `dev`, no resolver is passed and everything is denied, so we maintain the property that nothing leaks even if exposed by mistake (ADR-0029). The spoofing in H-25 (the development header) is tolerated only under this precondition.

## Consequences

- The API surface becomes three forms: GraphQL, SPARQL (read), and commands. Re-open this when there is a plan for an external client that requires stream subscription.
- The open question in ADR-0008 about "reflecting subscriptions in the UI" disappears (polling is sufficient for the UI).
