# ADR-0034: Principals are held as pseudonymous IRIs, and erasure is done on rows of a mapping table (proposed policy for H-05)

- Date: 2026-11-08
- Status: **Proposed** (awaiting legal confirmation; not implemented until adopted)
- Layers touched: api (resolver contract), knowledge (location of the mapping table). Code changes come after adoption

## Context

Invariant 2 (append-only) collides directly with personal-data erasure requests. We hold no raw video or audio (invariant 9), and personal identification of a `Person` is forbidden at the type level in probes.
**What remains is the principal IRI attached to a "human judgment"**, and the locations confirmed in the code are as follows:

| Location | Content | Append-only? |
|---|---|---|
| Event log (Redpanda) and knowledge graph | `RecordEvidence.source = answered_by` of a human-answer claim (`integration/human_answer.py`). Also the unit of audit isolation | Yes |
| `learning_decisions` (alembic 0005) | The `decided_by` column, and `resolved_by` / `approved_by` etc. in the `payload` JSON | Rows are not changed once written (by convention) |
| `quarantine` (ADR-0035) | `payload` holds **the raw row of the business system** (may contain personal information). The reviewer `reviewed_by` is on the `learning_decisions` side | Rows are not deleted (by convention) |
| `discrepancies` | `assignee_role` (held as a role; does not point at an individual) | — |

If a principal IRI points directly at an individual (contains a name, employee number, or email), every erasure request would require rewriting the append-only layers, and as-of reconstruction would also break.

## Decision (proposed)

1. **The principal IRI returned by `principal_resolver` is a pseudonym (an opaque identifier) that does not point directly at an individual.** For example `urn:sbp:principal:<random>`. Do not include a name, employee number, or email in the IRI. This is imposed as a contract on the ADR-0029 resolver.
2. **The mapping between a pseudonym and the person is kept outside the platform** (the IdP, or a deletable table placed outside the platform's append-only layers). The platform's append-only layers know only the pseudonym.
3. **An erasure request is satisfied by deleting the row in the mapping table.** Statements are not deleted (this preserves invariant 2). After deletion, who that pseudonym was can no longer be traced from inside the platform. As-of reconstruction is preserved with the pseudonym as is.
4. Operate such that personal information is not written in the `note` (free text) of a human judgment, and show a notice in the UI. Erasure of free text is outside the scope of item 3, so a separate ADR is needed if required.

## Open questions (to confirm with legal)

- Whether pseudonymization (deleting the mapping table) can be regarded as satisfying an erasure request. If not, define a separate exceptional procedure for physical deletion of statements in an ADR (it is an exception to invariant 2, so it is treated as MAJOR-equivalent).
- Handling of cases where a pseudonym can be re-identified from behavioral history (e.g., only one person in charge at a small site).
- Provisions for retention period (whether the mapping table is automatically deleted after a fixed period).

## Consequences (if adopted)

- Implementation centers on a resolver contract test (checking the pseudonym format) and the addition of a mapping table; the append-only layers do not change. Designing this together with the remainder of H-25 (principal authorization, `decision_id`) causes the least rework.
- This ADR is not an answer to H-05 but a **proposed policy**. H-05 remains "pending" until legal confirmation arrives.
