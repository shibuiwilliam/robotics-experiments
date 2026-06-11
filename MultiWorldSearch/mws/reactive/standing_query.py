"""Standing query engine — pub-sub pattern for continuous monitoring."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mws.core.atom import ExperienceAtom
from mws.core.logging import get_logger

logger = get_logger(__name__)


class StandingQueryEngine:
    """Manages registered query templates and evaluates them against new atoms.

    When an atom is ingested, all registered standing queries are checked.
    If a match is found, the callback fires.
    """

    def __init__(self) -> None:
        self._queries: list[dict[str, Any]] = []
        self._fired: list[dict[str, Any]] = []

    def register(
        self,
        name: str,
        tags: list[str],
        callback: Callable[[ExperienceAtom], None] | None = None,
    ) -> None:
        """Register a standing query. Fires when an ingested atom matches the tags."""
        self._queries.append(
            {"name": name, "tags": set(tags), "callback": callback, "fire_count": 0}
        )

    def on_atom_ingested(self, atom: ExperienceAtom) -> list[str]:
        """Evaluate all standing queries against a newly ingested atom. Returns list of fired query names."""
        fired_names: list[str] = []
        atom_tags = set(atom.tags)
        for sq in self._queries:
            if sq["tags"] & atom_tags:  # any tag overlap = match
                sq["fire_count"] += 1
                fired_names.append(sq["name"])
                self._fired.append({"query": sq["name"], "atom_id": atom.atom_id})
                if sq["callback"]:
                    sq["callback"](atom)
                logger.debug("Standing query fired", query=sq["name"], atom_id=atom.atom_id)
        return fired_names

    @property
    def registered_count(self) -> int:
        return len(self._queries)

    @property
    def total_fires(self) -> int:
        return sum(sq["fire_count"] for sq in self._queries)

    @property
    def fired_log(self) -> list[dict[str, Any]]:
        return list(self._fired)
