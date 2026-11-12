# ADR-0005: Represent statement-level metadata with RDF 1.2 triple terms + `rdf:reifies`

- Date: 2026-10-03
- Status: Accepted
- Layers touched: knowledge, ontology (query notation), docs/design (notation updates in §6.3, §6.4, §6.6)

## Context

Design document §6.3 chose **RDF-star** to implement statement-level metadata, and the query example in §6.6 uses the following notation.

```sparql
<< ?pump sbp:hasState ent:Failed >> sbp:modality sbp:Predicted ;
                                     sbp:leadTime ?lead .
```

This is the old RDF-star notation, which puts a quoted triple in the **subject** position. In the spike before starting M0-11 (the procedure instructed by the M0-11 prompt in `docs/PROMPT.md`), it turned out that **this notation cannot be used in pyoxigraph 0.5.11**.

```
>>> store.add(ox.Quad(ox.Triple(S, P, O), TXF, ..., G))
TypeError: argument 'subject': failed to extract enum PyNamedOrBlankNode ('NamedNode | BlankNode')
```

The reason is that Oxigraph followed the outcome of RDF 1.2 standardization. In RDF 1.2, the quoted triple was redefined as a **triple term**, which **can appear only in the object position**. Annotating a statement is done with a reifier, linked via `rdf:reifies`, as the subject.

Facts confirmed in the spike (both the embedded store of pyoxigraph 0.5.11 and the `oxigraph/oxigraph:0.5.11` server in Compose):

| What was tried | Result |
|---|---|
| Put a `Triple` in the subject of a Quad | **Not possible** (the TypeError above) |
| Put a `Triple` in the object of a Quad | Possible |
| Parse the Turtle 1.2 `<<( s p o )>>` notation | Possible (readable as 3 quads) |
| SPARQL with `?a rdf:reifies <<( ?s ?p ?o )>>` filtered by metadata | **Possible** (as-of filtering works) |
| Send the same query to `/query` on the HTTP server | **Possible** (HTTP 200, 1 row as expected) |
| Dump to N-Quads | Possible |

In other words, **the requirements (statement-level metadata, as-of / valid-at filtering) can be met**. Only the **notation** that the design document wrote cannot.

## Options

1. **Use RDF 1.2 triple terms + `rdf:reifies`.** Update the design document's notation.
   - Benefits: follows the W3C standardization outcome. The store's features can be used as they are. Because an explicit identifier for the statement (the reifier IRI) exists, `assertion_id` (Appendix A.1) can be mapped directly to the reifier IRI, and references from provenance bundles also become natural.
   - Drawbacks: the query examples in design document §6.6 must be rewritten. One extra `rdf:reifies` triple per statement.
2. Keep statement metadata in an auxiliary table (Postgres) and put only bare triples in the graph.
   - Benefits: avoids the notation problem. Time-range indexes work via B-tree.
   - Drawbacks: **the belief's bitemporality is split across two stores.** As-of queries become cross-store joins, and auditing is no longer possible with SPARQL alone (this breaks the non-functional requirement "from any assertion to its evidence within 3 hops"). It would throw away the reasons (standardization, provenance, inference) why design document §6.3 decided that "the primary store is RDF and the property graph is a derived view". Rejected.
3. Pin an old Oxigraph (0.3 line) that supports the old RDF-star.
   - Benefits: the design document's notation can be used as-is.
   - Drawbacks: bets the future on a pre-standardization notation. The 0.3 line is heading toward the end of maintenance with the move to RDF 1.2, and security updates cannot be expected. Rejected.
4. Put each statement in its own named graph and attach metadata to the graph IRI (graph-per-statement).
   - Benefits: needs only standard RDF 1.1.
   - Drawbacks: conflicts with the design (§6.3) of partitioning named graphs by `<modality>/<site>/<ontologyVersion>`. The number of graphs equals the number of assertions, bloating the store's indexes. Rejected.

## Decision

**We adopt Option 1.** Statement metadata is represented in the following form.

```turtle
# 1 assertion = 1 reifier IRI + 1 triple term + a group of metadata triples
<https://sbp.example/site/siteA/assertion/{assertion_id}>
    rdf:reifies <<( <...asset/P-101> ent:Running true )>> ;
    sbp:validFrom "..."^^xsd:dateTime ;
    sbp:validTo   "..."^^xsd:dateTime ;   # omitted while open
    sbp:txFrom    "..."^^xsd:dateTime ;
    sbp:txTo      "..."^^xsd:dateTime ;   # omitted while open
    sbp:modality  sbp:Inferred ;
    sbp:confidence "0.91"^^xsd:double ;
    sbp:provenance <...prov/bundle/...> ;
    sbp:ontologyVersion "0.1.0" .
```

- The reifier IRI is derived from `Assertion.assertion_id` (Appendix A.1). This makes the Python-side `Assertion`
  and a statement in the graph correspond one to one, so `retract` can point to its target uniquely.
- An "open interval" is expressed by **not placing the triple** for `validTo` / `txTo` (no `null` literal is placed).
  as-of / valid-at queries are handled with `OPTIONAL` + `!BOUND(...)` (behavior confirmed in the spike).
- Predicates such as `sbp:validFrom` are added to the L1 core ontology (an ontology MINOR update).

References to this ADR and the updated notation will be reflected in the relevant places of design document §6.3, §6.4, and §6.6.

## Consequences

- Impact on invariants:
  - 1 (every assertion has the required fields): only the way of representation changes; the requirement is unchanged. SHACL shapes
    can verify that the reifier has all required predicates (verification actually becomes easier).
  - 2 (append-only): a correction **adds** `txTo` to the old reifier and creates a new reifier. No triple deletion is involved.
- Ontology version: becomes **0.2.0 (MINOR)**. Adds `sbp:validFrom` / `validTo` / `txFrom` / `txTo` /
  `modality` / `confidence` / `provenance` / `ontologyVersion` / `scenario` / `situation` /
  `horizonOrigin` / `leadTime` / `sourceReliability` additively. There are no existing probes or queries, so
  backward compatibility is not broken. Re-grounding is not needed (no existing data).
- Impact on testing: property tests for bitemporality (as-of monotonicity, non-destructiveness of corrections, no overlap of tx intervals) can be written in a form
  independent of the notation. SPARQL strings are confined within `sbp.knowledge` (a knowledge rule), so
  the blast radius of a notation change also stays within that one module.
- Version pinning: it is a precondition that the embedded pyoxigraph and the Compose Oxigraph server are **pinned to the same 0.5.11**
  (because notation support varies by version). `pyproject.toml` constrains it to `>=0.5.11,<0.6`.

## Open questions

Whether to use the RDF 1.2 `<< s p o ~ ?r >>` shorthand (syntactic sugar that binds the reifier at the same time).
Support in pyoxigraph has not been confirmed. Writing it with an explicit `rdf:reifies` suffices, so there is no urgency.
