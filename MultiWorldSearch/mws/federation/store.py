"""Federated store — wraps multiple instance stores behind a unified query interface.

Models the edge-cloud pattern from PROJECT.md §4: each robot instance has its own
local store (edge), queries fan out to all stores (cloud view) and results are merged.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mws.core.atom import ExperienceAtom
from mws.core.logging import get_logger
from mws.core.types import EmbeddingSpace
from mws.embedding.base import Embedder
from mws.embedding.cache import EmbeddingCache
from mws.retrieval.fusion import reciprocal_rank_fusion
from mws.retrieval.query import RetrievalQuery, RetrievalResult
from mws.storage.registry import StoreRegistry
from mws.storage.timeseries import TimeseriesRecord

logger = get_logger(__name__)


class InstanceStore:
    """One instance's local store + atom cache."""

    def __init__(
        self,
        instance_id: str,
        embedding_space: EmbeddingSpace,
        embedding_dims: int,
        embedder: Embedder,
        embed_document_fn: Any | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.stores = StoreRegistry(
            embedding_space=embedding_space,
            embedding_dims=embedding_dims,
        )
        self.embedder = embedder
        #: Tracked embedding path supplied by the owning FederatedStore — a
        #: fallback embed here is a REAL cloud call in live mode and must go
        #: through cost/latency/bandwidth accounting (reconciliation audit).
        self._embed_document_fn = embed_document_fn
        self.atoms: dict[str, ExperienceAtom] = {}

    def ingest(self, atom: ExperienceAtom) -> None:
        """Ingest an atom into this instance's local store.

        Reuses an existing embedding for this store's space when the atom
        already carries one (e.g. prefetched copies) — no duplicate embedding
        call, same rule as RetrievalEngine.ingest.
        """
        self.atoms[atom.atom_id] = atom

        if atom.text_summary:
            existing = atom.get_embedding(self.stores.vector.embedding_space)
            if existing is not None and existing.shape == (self.stores.vector.dims,):
                self.stores.vector.add(atom.atom_id, existing)
            else:
                # Document side of the asymmetric scheme (E1); identity for
                # the symmetric mock embedder. Routed through the federation's
                # tracked/cached path when available.
                if self._embed_document_fn is not None:
                    result = self._embed_document_fn(atom.text_summary)
                else:
                    result = self.embedder.embed_document(atom.text_summary)
                atom.add_embedding(result.space, result.vector, result.content_hash)
                self.stores.vector.add(atom.atom_id, np.array(result.vector, dtype=np.float32))

        self.stores.spatial.add(
            atom.atom_id,
            (atom.coord.x, atom.coord.y, atom.coord.z),
        )
        self.stores.timeseries.add(
            TimeseriesRecord(
                atom_id=atom.atom_id,
                timestamp=atom.coord.timestamp,
                values={"trust": atom.trust, "freshness": atom.freshness},
                tags={t: "1" for t in atom.tags[:5]},
            )
        )


class FederatedStore:
    """Wraps N InstanceStores with a unified query interface.

    - ingest(instance_id, atom): writes to the specified instance's LOCAL store only.
    - search(query): fans out to ALL instances, merges results with RRF.
    - This models stigmergy: instances share data without direct communication,
      only through the federated query layer.
    """

    def __init__(
        self,
        embedder: Embedder,
        cost_tracker: Any | None = None,
        latency_tracker: Any | None = None,
        bandwidth_meter: Any | None = None,
    ) -> None:
        self._instances: dict[str, InstanceStore] = {}
        self._embedder = embedder
        # Measurement plumbing (IMPROVEMENT M12): federated query embeddings
        # are real cloud calls in live mode and must be counted/timed like the
        # engine's. The content-hash cache prevents re-embedding repeated
        # queries (and keeps the reconciliation audit at zero delta).
        self._cost = cost_tracker
        self._latency = latency_tracker
        self._bandwidth = bandwidth_meter
        self._cache = EmbeddingCache()

    def _embed_query(self, text: str) -> Any:
        return self._embed(text, role="query")

    def _embed_document(self, text: str) -> Any:
        return self._embed(text, role="document")

    def _embed(self, text: str, role: str) -> Any:
        """Embed with role formatting, caching, and cost/latency/bandwidth
        accounting.

        Asymmetric scheme (E1): the embedder's role formatting is applied
        first, and the cache/hash/cost cover the FINAL formatted string
        (identity for symmetric embedders).
        """
        formatter = getattr(self._embedder, f"format_{role}", None)
        final_text = formatter(text) if formatter is not None else text
        content_hash = self._embedder._content_hash(final_text)
        cached = self._cache.get(content_hash, self._embedder.space)
        if cached is not None:
            return cached
        bucket = getattr(self._embedder, "latency_bucket", "local_embed")
        if self._latency is not None:
            with self._latency.track(bucket):
                result = self._embedder.embed_text(final_text)
        else:
            result = self._embedder.embed_text(final_text)
        self._cache.put(result)
        if self._cost is not None:
            self._cost.record_embedding_call(tokens=max(1, len(final_text) // 4), texts=1)
        if self._bandwidth is not None:
            self._bandwidth.record_embedding_request(
                final_text, self._embedder.dims, real=bucket == "gemini_embed"
            )
        return result

    def add_instance(
        self,
        instance_id: str,
        embedding_space: EmbeddingSpace,
        embedding_dims: int,
    ) -> InstanceStore:
        """Register a new instance with its own local store."""
        inst = InstanceStore(
            instance_id=instance_id,
            embedding_space=embedding_space,
            embedding_dims=embedding_dims,
            embedder=self._embedder,
            embed_document_fn=self._embed_document,
        )
        self._instances[instance_id] = inst
        logger.debug("Federation: added instance", instance_id=instance_id)
        return inst

    def get_instance(self, instance_id: str) -> InstanceStore | None:
        return self._instances.get(instance_id)

    def ingest(self, instance_id: str, atom: ExperienceAtom) -> None:
        """Ingest an atom into a specific instance's LOCAL store only."""
        inst = self._instances.get(instance_id)
        if inst is None:
            raise ValueError(f"Unknown instance: {instance_id}")
        inst.ingest(atom)

    def search(
        self,
        query: RetrievalQuery,
        fusion_weights: dict[str, float] | None = None,
    ) -> list[RetrievalResult]:
        """Fan-out search across all instances, merge with RRF."""
        all_ranked: dict[str, list[tuple[str, float]]] = {}

        # Embed the query ONCE and reuse the vector across all instances.
        # (Previously embedded per instance: 3 redundant — and in live mode
        # real — cloud calls per federated search.)
        query_vec: np.ndarray | None = None
        if query.text:
            emb = self._embed_query(query.text)
            query_vec = np.array(emb.vector, dtype=np.float32)

        for inst_id, inst in self._instances.items():
            # Semantic search per instance
            if query_vec is not None:
                hits = inst.stores.vector.search(query_vec, top_k=query.top_k * 2)
                key = f"semantic::{inst_id}"
                all_ranked[key] = hits

            # Spatial per instance
            if query.position is not None:
                spatial = inst.stores.spatial.query_radius(query.position, query.spatial_radius)
                key = f"spatial::{inst_id}"
                all_ranked[key] = [(aid, 1.0 / (1.0 + d)) for aid, d in spatial]

            # Tag match per instance
            if query.tags:
                tag_set = set(query.tags)
                tag_results = []
                for atom_id, atom in inst.atoms.items():
                    overlap = len(tag_set & set(atom.tags))
                    if overlap > 0:
                        tag_results.append((atom_id, overlap / len(tag_set)))
                tag_results.sort(key=lambda x: x[1], reverse=True)
                all_ranked[f"symbolic::{inst_id}"] = tag_results

        if not all_ranked:
            return []

        # Flatten all ranked lists into per-index-type lists for RRF.
        # The "::" delimiter keeps instance ids (which contain "_") from
        # corrupting the index-type key — fusion weights are keyed by the
        # real index names (semantic/spatial/symbolic).
        merged: dict[str, list[tuple[str, float]]] = {}
        for key, ranked in all_ranked.items():
            idx_type = key.split("::", 1)[0]  # e.g., "semantic"
            if idx_type not in merged:
                merged[idx_type] = []
            merged[idx_type].extend(ranked)

        # Deduplicate by taking best score per atom per index
        for idx_type in merged:
            best: dict[str, float] = {}
            for atom_id, score in merged[idx_type]:
                if atom_id not in best or score > best[atom_id]:
                    best[atom_id] = score
            merged[idx_type] = sorted(best.items(), key=lambda x: x[1], reverse=True)

        results = reciprocal_rank_fusion(
            merged,
            weights=fusion_weights,
            top_n=query.top_k,
        )

        # Enrich with atom metadata
        all_atoms = self.all_atoms()
        for r in results:
            atom = all_atoms.get(r.atom_id)
            if atom:
                r.text_summary = atom.text_summary
                r.modality = atom.modality

        return results

    def all_atoms(self) -> dict[str, ExperienceAtom]:
        """Return all atoms across all instances (federated view)."""
        merged: dict[str, ExperienceAtom] = {}
        for inst in self._instances.values():
            merged.update(inst.atoms)
        return merged

    @property
    def total_atom_count(self) -> int:
        return sum(len(inst.atoms) for inst in self._instances.values())

    @property
    def instance_ids(self) -> list[str]:
        return list(self._instances.keys())
