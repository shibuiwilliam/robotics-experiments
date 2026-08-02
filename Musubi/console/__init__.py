"""Musubi Console — the operator cockpit (interface).

A single, discoverable, JSON-capable command surface for driving and introspecting the
Robotics × AI-agent × Ontology platform: environment/invariant health, the ontology, scenarios,
experiments, and any run's beliefs/events/oracle/trace. Read-only observer — never writes to a
production path (PROJECT.md §6.2). Invoked via ``python -m console`` / ``make console``.
"""

from __future__ import annotations

from console.app import main

__all__ = ["main"]
