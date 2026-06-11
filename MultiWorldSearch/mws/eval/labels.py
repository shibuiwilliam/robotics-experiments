"""Ground-truth relevance label generation from sim state.

Leverages the simulation privilege: MuJoCo provides ground truth,
so we can auto-derive relevance labels without human annotation.
"""

from __future__ import annotations

from mws.core.atom import ExperienceAtom


def derive_relevance_labels(
    query_entity_id: str,
    query_tags: list[str],
    atoms: list[ExperienceAtom],
) -> set[str]:
    """Auto-derive relevance labels from atom metadata.

    An atom is relevant if:
    - It references the same entity_id as the query, OR
    - It shares tags with the query

    This uses the sim privilege: we know the ground-truth entity associations.
    """
    relevant = set()
    query_tag_set = set(query_tags)

    for atom in atoms:
        # Entity match
        if atom.entity_id and atom.entity_id == query_entity_id:
            relevant.add(atom.atom_id)
            continue

        # Tag overlap (at least 2 shared tags for relevance)
        if len(query_tag_set & set(atom.tags)) >= 2:
            relevant.add(atom.atom_id)

    return relevant
