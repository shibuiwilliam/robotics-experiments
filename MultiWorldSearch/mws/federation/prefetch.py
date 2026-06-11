"""Task-plan-driven predictive prefetch (PROJECT.md §3 連合).

Before an instance starts a task, the prefetcher pulls the task-relevant
subset of the FEDERATED view into that instance's LOCAL store, so subsequent
strict-freshness (low-latency, local-first) queries can be answered without a
federated round-trip. This closes the last unimplemented federation promise:
"タスク計画に基づく予測プリフェッチ".

The prefetch copies atoms by reference; atoms that already carry an embedding
for the local store's space are indexed without re-embedding (no extra cloud
calls — same reuse rule as RetrievalEngine.ingest).
"""

from __future__ import annotations

from mws.core.logging import get_logger
from mws.federation.store import FederatedStore
from mws.retrieval.query import RetrievalQuery

logger = get_logger(__name__)


class PrefetchPlanner:
    """Prefetches task-relevant atoms from the federation into a local store."""

    def __init__(self, federated_store: FederatedStore) -> None:
        self._fed = federated_store
        self._prefetched_total = 0

    def prefetch_for_task(
        self,
        instance_id: str,
        task_tags: list[str],
        top_k: int = 10,
    ) -> int:
        """Pull atoms matching the upcoming task's tags into the local store.

        Returns the number of atoms newly added to the instance's local store.
        Atoms the instance already holds are skipped (idempotent).
        """
        inst = self._fed.get_instance(instance_id)
        if inst is None:
            raise ValueError(f"Unknown instance: {instance_id}")

        query = RetrievalQuery(tags=task_tags, top_k=top_k)
        results = self._fed.search(query)
        all_atoms = self._fed.all_atoms()

        added = 0
        for r in results:
            if r.atom_id in inst.atoms:
                continue  # already local
            atom = all_atoms.get(r.atom_id)
            if atom is None:
                continue
            inst.ingest(atom)
            added += 1

        self._prefetched_total += added
        logger.info(
            "Prefetch complete",
            instance=instance_id,
            task_tags=task_tags,
            added=added,
        )
        return added

    @property
    def stats(self) -> dict[str, int]:
        return {"prefetched_atoms": self._prefetched_total}
