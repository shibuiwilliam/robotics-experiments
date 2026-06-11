"""QoR-aware query routing — routes queries based on quality-of-retrieval requirements.

Queries with freshness=strict hit local stores first. Queries with authority=high
hit the full federated view. Adds latency instrumentation for local vs federation hits.
"""

from __future__ import annotations

from mws.core.logging import get_logger
from mws.federation.store import FederatedStore, InstanceStore
from mws.retrieval.query import RetrievalQuery, RetrievalResult

logger = get_logger(__name__)


class QoRRouter:
    """Routes queries to the appropriate scope based on QoR requirements.

    QoR parameters are passed via query.structured_filters:
    - qor_freshness: "strict" → prefer local store (fastest, freshest)
    - qor_authority: "high" → require federated view (broadest coverage)
    - Default: federated view
    """

    def __init__(self, federated_store: FederatedStore) -> None:
        self._fed = federated_store
        self._local_hits = 0
        self._federated_hits = 0

    def route(
        self,
        query: RetrievalQuery,
        local_instance_id: str | None = None,
    ) -> list[RetrievalResult]:
        """Route a query based on QoR requirements."""
        qor_freshness = query.structured_filters.get("qor_freshness", "")

        # Strict freshness + local instance available → try local first
        if qor_freshness == "strict" and local_instance_id:
            inst = self._fed.get_instance(local_instance_id)
            if inst:
                local_results = self._search_local(inst, query)
                if local_results:
                    self._local_hits += 1
                    logger.debug("QoR: local hit", instance=local_instance_id, n=len(local_results))
                    return local_results

        # Default or authority=high → federated search
        self._federated_hits += 1
        return self._fed.search(query)

    def _search_local(self, inst: InstanceStore, query: RetrievalQuery) -> list[RetrievalResult]:
        """Search a single instance's local store."""
        if not query.tags:
            return []
        tag_set = set(query.tags)
        results = []
        for atom_id, atom in inst.atoms.items():
            overlap = len(tag_set & set(atom.tags))
            if overlap > 0:
                results.append(
                    RetrievalResult(
                        atom_id=atom_id,
                        score=overlap / len(tag_set),
                        text_summary=atom.text_summary,
                        modality=atom.modality,
                    )
                )
        results.sort(key=lambda x: x.score, reverse=True)
        return results[: query.top_k]

    @property
    def stats(self) -> dict[str, int]:
        return {"local_hits": self._local_hits, "federated_hits": self._federated_hits}
