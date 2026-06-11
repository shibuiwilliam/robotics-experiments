"""Content-hash deduplication for atoms."""

from __future__ import annotations

import xxhash

from mws.core.atom import ExperienceAtom
from mws.core.logging import get_logger

logger = get_logger(__name__)


def deduplicate(atoms: list[ExperienceAtom]) -> list[ExperienceAtom]:
    """Remove duplicate atoms based on content hash of text_summary."""
    seen: dict[str, ExperienceAtom] = {}
    for atom in atoms:
        h = (
            xxhash.xxh64(atom.text_summary.encode()).hexdigest()
            if atom.text_summary
            else atom.atom_id
        )
        if h not in seen:
            seen[h] = atom
    n_removed = len(atoms) - len(seen)
    if n_removed > 0:
        logger.info("Deduplication", removed=n_removed, remaining=len(seen))
    return list(seen.values())
