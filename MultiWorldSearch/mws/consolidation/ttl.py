"""TTL-based pruning for atoms."""

from __future__ import annotations

from mws.core.atom import ExperienceAtom
from mws.core.logging import get_logger

logger = get_logger(__name__)


def prune_by_ttl(
    atoms: list[ExperienceAtom], max_age: float, current_time: float
) -> list[ExperienceAtom]:
    """Remove atoms older than max_age seconds, keeping at least one per entity_id."""
    entity_latest: dict[str, ExperienceAtom] = {}
    kept: list[ExperienceAtom] = []

    for atom in atoms:
        age = current_time - atom.coord.timestamp
        if age <= max_age:
            kept.append(atom)
        elif atom.entity_id:
            # Keep the latest per entity even if expired
            existing = entity_latest.get(atom.entity_id)
            if existing is None or atom.coord.timestamp > existing.coord.timestamp:
                entity_latest[atom.entity_id] = atom

    # Add preserved entity representatives
    for _entity_id, atom in entity_latest.items():
        if atom.atom_id not in {a.atom_id for a in kept}:
            kept.append(atom)

    n_pruned = len(atoms) - len(kept)
    if n_pruned > 0:
        logger.info("TTL pruning", pruned=n_pruned, kept=len(kept))
    return kept
