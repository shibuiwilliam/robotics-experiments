"""Musubi core — the semantic substrate.

Belief (claimstore), identity/capability/authority (registry), belief mediation (mediator),
norms + verification gates (norms), the JSON-LD event bus (bus), and the explanation service
(explain). Also the determinism substrate every production path depends on: :mod:`core.clock`
and :mod:`core.rng` (CLAUDE.md §0-5, NFR-DETERM).
"""

from __future__ import annotations
