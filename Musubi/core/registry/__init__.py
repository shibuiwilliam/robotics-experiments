"""Registry — entities + per-aspect authority, and capability advertisement/matching.

Authority is per-aspect × scope (no global entity master, PROJECT.md §8). Capabilities are matched
to Requirements by subsumption (dotted action-type hierarchy + QoS), not string equality, so a new
robot is added by advertising a Capability — zero agent code (FR-CAP, E4).
"""

from __future__ import annotations

from core.registry.capability import CapabilityRegistry, matches, subsumes
from core.registry.entity import EntityRegistry

__all__ = ["EntityRegistry", "CapabilityRegistry", "matches", "subsumes"]
