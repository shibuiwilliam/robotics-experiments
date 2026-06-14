"""Unified retrieval engine — wires all indices, fusion, and projection."""

from __future__ import annotations

from typing import Any

import numpy as np

from mws.core.atom import ExperienceAtom
from mws.core.logging import get_logger
from mws.embedding.base import Embedder, EmbeddingResult
from mws.embedding.cache import EmbeddingCache
from mws.eval.bandwidth import BandwidthMeter
from mws.eval.cost import CloudCostTracker
from mws.reactive.standing_query import StandingQueryEngine
from mws.retrieval.fusion import reciprocal_rank_fusion
from mws.retrieval.projection import project_for_consumer
from mws.retrieval.query import RetrievalQuery, RetrievalResult
from mws.storage.registry import StoreRegistry
from mws.worldmodel.scene_graph import SceneGraph

logger = get_logger(__name__)

# Every index type the engine can fire. The fusion-weight dict MUST cover this
# set: an index missing from an explicit weights dict is excluded by fusion
# (weight 0.0), so forgetting one here silently disables it.
ALL_INDICES: frozenset[str] = frozenset(
    {"semantic", "spatial", "temporal", "symbolic", "structured", "relational"}
)


class RetrievalEngine:
    """Multi-index retrieval engine with fusion and projection.

    Queries across semantic (vector), spatial (KD-tree), temporal (timeseries),
    symbolic/structured (tag/field match), and relational (scene graph) indices,
    then fuses with RRF.
    """

    def __init__(
        self,
        stores: StoreRegistry,
        embedder: Embedder,
        fusion_weights: dict[str, float] | None = None,
        cost_tracker: CloudCostTracker | None = None,
        bandwidth_meter: BandwidthMeter | None = None,
        graph: SceneGraph | None = None,
        standing_query_engine: StandingQueryEngine | None = None,
        latency_tracker: Any | None = None,
        embedding_batch_size: int = 64,
    ) -> None:
        self.stores = stores
        self.embedder = embedder
        self.graph = graph
        self.fusion_weights = fusion_weights or {
            "semantic": 0.4,
            "spatial": 0.2,
            "temporal": 0.2,
            "symbolic": 0.1,
            "structured": 0.1,
            "relational": 0.1,
        }
        self._atoms: dict[str, ExperienceAtom] = {}
        # Inverted indices (stretch item, IMPROVEMENT 2026-06-11): symbolic and
        # structured lookups touch only candidate atoms instead of scanning the
        # whole store. Tie ordering is kept IDENTICAL to the previous linear
        # scan via the ingest-order tie-break. Atoms must not mutate their
        # tags/structured_fields after ingest (none of the scenarios do).
        self._tag_index: dict[str, list[str]] = {}
        self._field_index: dict[tuple[str, str | float | int | bool], list[str]] = {}
        self._insert_order: dict[str, int] = {}
        self._cache = EmbeddingCache()
        self._cost = cost_tracker or CloudCostTracker()
        self._bandwidth = bandwidth_meter or BandwidthMeter()
        self._standing_queries = standing_query_engine
        self._latency = latency_tracker
        self._batch_size = max(1, embedding_batch_size)

    def _format_for_role(self, text: str, role: str) -> str:
        """Apply the embedder's task formatting for a role (E1).

        Asymmetric embedders (Gemini teacher) prefix documents and queries
        differently; symmetric embedders return the text unchanged. The
        returned string is what is hashed, cached, costed, and embedded.
        """
        formatter = getattr(self.embedder, f"format_{role}", None)
        return formatter(text) if formatter is not None else text

    def _embed_text(self, text: str, role: str = "query") -> EmbeddingResult:
        """Embed text with role formatting, caching, cost, and bandwidth."""
        final_text = self._format_for_role(text, role)
        # Check cache first — keyed on the FINAL formatted string, so the same
        # raw text caches separately per role (correct for asymmetric models).
        content_hash = self.embedder._content_hash(final_text)
        cached = self._cache.get(content_hash, self.embedder.space)
        if cached is not None:
            return cached

        # Cache miss — embed. Latency is tracked per call under the embedder's
        # bucket (gemini_embed for the cloud teacher, local_embed otherwise) so
        # the CLAUDE.md §10 decomposition has real per-call p50/p95 samples.
        if self._latency is not None:
            bucket = getattr(self.embedder, "latency_bucket", "local_embed")
            with self._latency.track(bucket):
                result = self.embedder.embed_text(final_text)
        else:
            result = self.embedder.embed_text(final_text)
        self._cache.put(result)

        # Track cost: estimate tokens as ~chars/4 (one request, one text)
        token_estimate = max(1, len(final_text) // 4)
        self._cost.record_embedding_call(tokens=token_estimate, texts=1)

        # Track bandwidth: real round-trip for the cloud teacher, virtual otherwise
        self._bandwidth.record_embedding_request(
            final_text,
            self.embedder.dims,
            real=getattr(self.embedder, "latency_bucket", "local_embed") == "gemini_embed",
        )

        return result

    def ingest(self, atom: ExperienceAtom) -> None:
        """Ingest an atom into all relevant indices."""
        if atom.atom_id not in self._atoms:
            self._insert_order[atom.atom_id] = len(self._insert_order)
            for tag in set(atom.tags):
                self._tag_index.setdefault(tag, []).append(atom.atom_id)
            for key, value in atom.structured_fields.items():
                self._field_index.setdefault((key, value), []).append(atom.atom_id)
        self._atoms[atom.atom_id] = atom

        # Semantic index: embed text_summary and store vector. If the atom
        # already carries an embedding for THIS engine's space (e.g. when
        # re-indexing into a consolidated store), reuse it — no duplicate
        # embedding call/cost.
        if atom.text_summary:
            existing = atom.get_embedding(self.embedder.space)
            if existing is not None and existing.shape == (self.embedder.dims,):
                self.stores.vector.add(atom.atom_id, existing)
            else:
                result = self._embed_text(atom.text_summary, role="document")
                atom.add_embedding(result.space, result.vector, result.content_hash)
                self.stores.vector.add(atom.atom_id, np.array(result.vector, dtype=np.float32))

        # Spatial index
        self.stores.spatial.add(
            atom.atom_id,
            (atom.coord.x, atom.coord.y, atom.coord.z),
        )

        # Timeseries index
        from mws.storage.timeseries import TimeseriesRecord

        self.stores.timeseries.add(
            TimeseriesRecord(
                atom_id=atom.atom_id,
                timestamp=atom.coord.timestamp,
                values={"trust": atom.trust, "freshness": atom.freshness},
                tags={t: "1" for t in atom.tags[:5]},
            )
        )

        logger.debug("Ingested atom", atom_id=atom.atom_id, modality=atom.modality)

        # Auto-fire standing queries on every ingested atom
        if self._standing_queries is not None:
            self._standing_queries.on_atom_ingested(atom)

    def ingest_batch(self, atoms: list[ExperienceAtom]) -> None:
        """Ingest atoms, embedding their summaries in batched requests (E2).

        Collects every text that actually needs an embedding (no reusable
        embedding on the atom, not in the cache), embeds them in chunks of
        ``embedding_batch_size`` — ONE API request per chunk — attaches the
        vectors to the atoms, then runs the normal per-atom ``ingest`` path
        (which reuses the attached embeddings, preserving ingest order and
        standing-query firing). Duplicate texts are embedded once and shared.
        """
        pending_atoms: dict[str, list[ExperienceAtom]] = {}
        pending_texts: dict[str, str] = {}
        order: list[str] = []

        for atom in atoms:
            if not atom.text_summary:
                continue
            existing = atom.get_embedding(self.embedder.space)
            if existing is not None and existing.shape == (self.embedder.dims,):
                continue
            final_text = self._format_for_role(atom.text_summary, "document")
            content_hash = self.embedder._content_hash(final_text)
            cached = self._cache.get(content_hash, self.embedder.space)
            if cached is not None:
                atom.add_embedding(cached.space, cached.vector, cached.content_hash)
                continue
            if content_hash not in pending_atoms:
                pending_atoms[content_hash] = []
                pending_texts[content_hash] = final_text
                order.append(content_hash)
            pending_atoms[content_hash].append(atom)

        bucket = getattr(self.embedder, "latency_bucket", "local_embed")
        is_real = bucket == "gemini_embed"
        for start in range(0, len(order), self._batch_size):
            chunk = order[start : start + self._batch_size]
            texts = [pending_texts[h] for h in chunk]
            if self._latency is not None:
                with self._latency.track(bucket):
                    results = self.embedder.embed_batch(texts)
            else:
                results = self.embedder.embed_batch(texts)
            # ONE request covering len(texts) texts (reconciliation: the
            # teacher logs one marker line per embed_batch call).
            self._cost.record_embedding_call(
                tokens=sum(max(1, len(t) // 4) for t in texts),
                texts=len(texts),
            )
            for text, result in zip(texts, results, strict=True):
                self._cache.put(result)
                self._bandwidth.record_embedding_request(text, self.embedder.dims, real=is_real)
                for atom in pending_atoms[result.content_hash]:
                    atom.add_embedding(result.space, result.vector, result.content_hash)

        for atom in atoms:
            self.ingest(atom)

    def _search_graph(self, query: RetrievalQuery) -> list[tuple[str, float]]:
        """Relational search: expand entity_id through scene graph relations.

        When ``query.structured_filters`` contains an ``entity_id`` key and a
        scene graph is available, find all entities related to that entity and
        return atoms belonging to those related entities with a distance-decayed
        score.
        """
        if self.graph is None:
            return []

        entity_id = query.structured_filters.get("entity_id")
        if entity_id is None:
            return []

        entity_id = str(entity_id)
        relations = self.graph.get_relations(entity_id)
        if not relations:
            return []

        # Collect related entity IDs with their relation weights
        related: dict[str, float] = {}
        for rel in relations:
            other = rel.target_id if rel.source_id == entity_id else rel.source_id
            # Keep the highest weight if multiple relations exist
            if other not in related or rel.weight > related[other]:
                related[other] = rel.weight

        # Find atoms belonging to related entities
        results: list[tuple[str, float]] = []
        for atom_id, atom in self._atoms.items():
            if atom.entity_id in related:
                results.append((atom_id, related[atom.entity_id]))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def search(self, query: RetrievalQuery) -> list[RetrievalResult]:
        """Execute a multi-index search with fusion."""
        ranked_lists: dict[str, list[tuple[str, float]]] = {}

        # Semantic search
        if query.text:
            emb_result = self._embed_text(query.text)
            vec = np.array(emb_result.vector, dtype=np.float32)
            # G6: time the vector search under the BACKEND's own bucket. An ES
            # query is an HTTP round-trip (es_search), not local ANN — keeping
            # them distinct stops the §10 latency decomposition being misread.
            vbucket = getattr(self.stores.vector, "latency_bucket", "local_ann")
            if self._latency is not None:
                with self._latency.track(vbucket):
                    ranked_lists["semantic"] = self.stores.vector.search(vec, top_k=query.top_k * 3)
            else:
                ranked_lists["semantic"] = self.stores.vector.search(vec, top_k=query.top_k * 3)

        # Spatial search
        if query.position is not None:
            spatial_results = self.stores.spatial.query_radius(query.position, query.spatial_radius)
            ranked_lists["spatial"] = [(aid, 1.0 / (1.0 + dist)) for aid, dist in spatial_results]

        # Temporal search
        if query.time_start is not None and query.time_end is not None:
            temporal_records = self.stores.timeseries.query_range(query.time_start, query.time_end)
            if temporal_records:
                time_range = query.time_end - query.time_start
                ranked_lists["temporal"] = [
                    (
                        r.atom_id,
                        (r.timestamp - query.time_start) / time_range if time_range > 0 else 1.0,
                    )
                    for r in temporal_records
                ]

        # Symbolic search (tag match) — inverted tag index: only candidate
        # atoms are touched, with the same scores and tie ordering as the
        # previous full scan (ingest-order tie-break).
        if query.tags:
            tag_set = set(query.tags)
            overlap_counts: dict[str, int] = {}
            for tag in tag_set:
                for atom_id in self._tag_index.get(tag, ()):
                    overlap_counts[atom_id] = overlap_counts.get(atom_id, 0) + 1
            symbolic_results = [
                (atom_id, count / len(tag_set)) for atom_id, count in overlap_counts.items()
            ]
            symbolic_results.sort(key=lambda x: (-x[1], self._insert_order[x[0]]))
            ranked_lists["symbolic"] = symbolic_results

        # Structured search (field match) — inverted (field, value) index.
        if query.structured_filters:
            match_counts: dict[str, int] = {}
            for key, value in query.structured_filters.items():
                for atom_id in self._field_index.get((key, value), ()):
                    match_counts[atom_id] = match_counts.get(atom_id, 0) + 1
            structured_results = [
                (atom_id, count / len(query.structured_filters))
                for atom_id, count in match_counts.items()
            ]
            structured_results.sort(key=lambda x: (-x[1], self._insert_order[x[0]]))
            ranked_lists["structured"] = structured_results

        # Relational search (scene graph expansion)
        relational_results = self._search_graph(query)
        if relational_results:
            ranked_lists["relational"] = relational_results

        if not ranked_lists:
            logger.warning(
                "Empty retrieval: no index matched the query",
                query_text=query.text[:80] if query.text else "",
                has_position=query.position is not None,
                n_tags=len(query.tags),
                n_filters=len(query.structured_filters),
            )
            return []

        # Fuse
        results = reciprocal_rank_fusion(
            ranked_lists,
            weights=self.fusion_weights,
            top_n=query.top_k,
        )

        # Enrich with atom metadata
        for r in results:
            atom = self._atoms.get(r.atom_id)
            if atom:
                r.text_summary = atom.text_summary
                r.modality = atom.modality

        logger.debug(
            "Search complete",
            n_results=len(results),
            indices_used=list(ranked_lists.keys()),
            cache_hit_rate=f"{self._cache.hit_rate:.1%}",
        )

        return results

    def search_with_indices(
        self,
        query: RetrievalQuery,
        enabled_indices: set[str],
    ) -> list[RetrievalResult]:
        """Run search using only the specified index types.

        Temporarily overrides fusion weights, zeroing disabled indices.
        Used for ablation studies comparing single-index vs multi-index performance.

        Args:
            query: The retrieval query.
            enabled_indices: Subset of {"semantic","spatial","temporal","symbolic","structured","relational"}.
        """
        original_weights = self.fusion_weights.copy()
        try:
            # Cover ALL firing indices, not just the keys present in the current
            # weights dict — otherwise an index absent from the dict could
            # neither be enabled nor disabled here.
            self.fusion_weights = {
                name: (original_weights.get(name, 0.0) if name in enabled_indices else 0.0)
                for name in ALL_INDICES | set(original_weights)
            }
            return self.search(query)
        finally:
            self.fusion_weights = original_weights

    def project(self, results: list[RetrievalResult], query: RetrievalQuery) -> list[dict]:
        """Project results for the query's consumer type."""
        projected = []
        for r in results:
            atom = self._atoms.get(r.atom_id)
            if atom:
                proj = project_for_consumer(atom, query.consumer)
                proj["relevance_score"] = r.score
                projected.append(proj)
        return projected

    def get_atom(self, atom_id: str) -> ExperienceAtom | None:
        """Read-only access to an ingested atom by id (for consumers that need
        to inspect structured fields / provenance of retrieved results)."""
        return self._atoms.get(atom_id)

    @property
    def atom_count(self) -> int:
        return len(self._atoms)

    @property
    def cache_stats(self) -> dict[str, float | int]:
        """Return embedding cache statistics."""
        return {
            "cache_size": self._cache.size,
            "cache_hit_rate": self._cache.hit_rate,
        }
