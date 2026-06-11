"""Consolidation engine — episodic to semantic memory distillation."""

from __future__ import annotations

from mws.core.atom import ExperienceAtom, SpatiotemporalCoord
from mws.core.logging import get_logger
from mws.core.types import Modality

logger = get_logger(__name__)


class ConsolidationEngine:
    """Groups similar episodic atoms into semantic summaries.

    Uses tag-based clustering and content-hash deduplication.
    """

    def __init__(self, seed: int = 0) -> None:
        self._seed = seed

    def cluster_by_tags(
        self, atoms: list[ExperienceAtom], min_overlap: int = 2
    ) -> dict[str, list[ExperienceAtom]]:
        """Cluster atoms by shared tag patterns. Returns {cluster_key: [atoms]}."""
        clusters: dict[str, list[ExperienceAtom]] = {}
        for atom in atoms:
            # Use sorted tag subset as cluster key
            key_tags = sorted(set(atom.tags) - {"sim", "pose"})[:3]
            key = "|".join(key_tags) if key_tags else "unclustered"
            clusters.setdefault(key, []).append(atom)
        return clusters

    def summarize_cluster(self, cluster_key: str, atoms: list[ExperienceAtom]) -> ExperienceAtom:
        """Create a semantic summary atom from a cluster of episodic atoms."""
        texts = [a.text_summary for a in atoms if a.text_summary]
        summary_text = f"Consolidated ({len(atoms)} atoms): {'; '.join(texts[:3])}"
        if len(texts) > 3:
            summary_text += f" ... and {len(texts) - 3} more"

        # Average position
        xs = [a.coord.x for a in atoms]
        ys = [a.coord.y for a in atoms]
        zs = [a.coord.z for a in atoms]
        ts = [a.coord.timestamp for a in atoms]

        coord = SpatiotemporalCoord(
            x=sum(xs) / len(xs),
            y=sum(ys) / len(ys),
            z=sum(zs) / len(zs),
            timestamp=max(ts),
        )

        # Merge tags
        all_tags: set[str] = set()
        for a in atoms:
            all_tags.update(a.tags)

        summary = ExperienceAtom(
            modality=Modality.DOCUMENT,
            coord=coord,
            text_summary=summary_text,
            tags=[*sorted(all_tags)[:10], "consolidated"],
            structured_fields={
                "cluster_key": cluster_key,
                "n_source_atoms": len(atoms),
            },
        )
        summary.provenance.add("consolidation_engine", "system", "consolidated")
        return summary

    def consolidate(self, atoms: list[ExperienceAtom]) -> list[ExperienceAtom]:
        """Run full consolidation: cluster + summarize."""
        clusters = self.cluster_by_tags(atoms)
        summaries = []
        for key, cluster_atoms in clusters.items():
            if len(cluster_atoms) >= 2:  # Only consolidate groups of 2+
                summaries.append(self.summarize_cluster(key, cluster_atoms))
        logger.info(
            "Consolidation complete",
            n_clusters=len(clusters),
            n_summaries=len(summaries),
        )
        return summaries
