"""Deterministic IRI minting. Instance-crossing references are IRIs, never bare (design P2).

IRIs are built from stable parts so the same logical object gets the same IRI across a replay
(no time/random in the IRI — determinism, CLAUDE.md §0-5).
"""

from __future__ import annotations

MSB = "msb:"


def mint(kind: str, *parts: str | int) -> str:
    """Mint a curie IRI ``msb:<kind>/<parts...>`` (parts joined by '/')."""
    tail = "/".join(str(p) for p in parts)
    return f"{MSB}{kind}/{tail}" if tail else f"{MSB}{kind}"


def kind_of(iri: str) -> str:
    """The ``kind`` segment of an ``msb:<kind>/...`` IRI (best-effort)."""
    body = iri[len(MSB) :] if iri.startswith(MSB) else iri
    return body.split("/", 1)[0]
