"""Entity registry with per-aspect authority lookup."""

from __future__ import annotations

from ontology.generated.musubi_types import Authority, Entity


class EntityRegistry:
    """Holds entities and authorities. Authority is declared per aspect-kind × scope."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._authorities: dict[str, Authority] = {}

    # -- entities ------------------------------------------------------------
    def register(self, entity: Entity) -> Entity:
        self._entities[str(entity.iri)] = entity
        return entity

    def get(self, iri: str) -> Entity | None:
        return self._entities.get(iri)

    def entities(self) -> list[Entity]:
        return list(self._entities.values())

    # -- authorities ---------------------------------------------------------
    def declare_authority(self, authority: Authority) -> Authority:
        self._authorities[str(authority.iri)] = authority
        return authority

    def authority_for(self, aspect_kind: str, scope: str | None = None) -> Authority | None:
        """The declared System of Record for an aspect-kind (optionally a scope). None if unset."""
        best: Authority | None = None
        for auth in self._authorities.values():
            ak = getattr(auth.aspectKind, "value", auth.aspectKind)
            if ak != aspect_kind:
                continue
            if scope is not None and auth.scope not in (None, scope):
                continue
            # prefer a scope-specific authority over a global one
            if best is None or (scope is not None and auth.scope == scope):
                best = auth
        return best

    def authorities(self) -> list[Authority]:
        return list(self._authorities.values())
