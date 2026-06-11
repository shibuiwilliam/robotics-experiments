"""Base protocols for polyglot storage adapters."""

from __future__ import annotations

from typing import Protocol

from mws.core.atom import ExperienceAtom


class AtomStore(Protocol):
    """Protocol for storing and retrieving atoms."""

    def put(self, atom: ExperienceAtom) -> None:
        """Store an atom."""
        ...

    def get(self, atom_id: str) -> ExperienceAtom | None:
        """Retrieve an atom by ID."""
        ...

    def delete(self, atom_id: str) -> None:
        """Delete an atom by ID."""
        ...

    def list_ids(self) -> list[str]:
        """List all stored atom IDs."""
        ...
